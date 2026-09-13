"""Headless browser fetching.

Uses Playwright (Chromium) so JS-rendered marketing sites (Next.js,
client-side React, etc.) come back with fully hydrated DOM instead of
an empty shell. Every call here is wrapped so a single bad page
(timeout, 404, bot block) returns a `FetchResult` with `html=None` and
a classified `error_type` instead of raising — callers decide what to
do with a miss, this layer just reports it precisely.

`PlaywrightFetcher` wraps a live `Browser` behind the `PageFetcher`
protocol so the rest of the codebase (and tests) depend on "something
that can fetch a URL", not on Playwright directly — a fake fetcher in
tests can simulate any mix of successes/timeouts/blocks without ever
launching a real browser.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional, Protocol

from playwright.async_api import Browser, TimeoutError as PWTimeoutError, async_playwright

from src import cache
from src.config import settings

logger = logging.getLogger(__name__)

try:
    from playwright_stealth import stealth_async
except ImportError:  # optional dependency — degrade to no stealth patching
    stealth_async = None  # type: ignore[assignment]


@dataclass
class FetchResult:
    url: str
    html: Optional[str]
    status: Optional[int]
    error: Optional[str] = None
    error_type: Optional[str] = None  # "blocked" | "not_found" | "server_error" | "timeout" | "network" | None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.html is not None


class PageFetcher(Protocol):
    """Anything that can fetch a URL and return a FetchResult. Implemented
    by PlaywrightFetcher for real use and by fakes in tests."""

    async def fetch(self, url: str) -> FetchResult: ...


async def fetch_page(browser: Browser, url: str) -> FetchResult:
    """Fetch one URL with a fresh page/context, retrying on timeout.

    Never raises. Bot blockers (403/429), 404s, DNS failures, and
    navigation timeouts all fall through to a FetchResult with
    html=None and a classified error, so the caller can log it and move
    on without the whole run dying.
    """
    cached_html = cache.get(url)
    if cached_html is not None:
        return FetchResult(url=url, html=cached_html, status=200, from_cache=True)

    last_error = "unknown error"
    last_error_type: Optional[str] = "network"
    for attempt in range(1, settings.nav_retries + 2):  # +1 initial try
        context = None
        try:
            ua = random.choice(settings.user_agent_pool)
            context = await browser.new_context(user_agent=ua)
            page = await context.new_page()
            if stealth_async is not None:
                try:
                    await stealth_async(page)
                except Exception:  # noqa: BLE001 - stealth patching must never break a fetch
                    pass

            response = await page.goto(
                url, timeout=settings.page_timeout_ms, wait_until="domcontentloaded"
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except PWTimeoutError:
                pass  # not fatal — some sites never go fully idle (analytics beacons etc.)

            status = response.status if response else None
            if status and status >= 400:
                if status in (403, 429):
                    last_error, last_error_type = f"HTTP {status} (likely bot-blocked)", "blocked"
                    return FetchResult(url=url, html=None, status=status, error=last_error, error_type=last_error_type)
                if status == 404:
                    last_error, last_error_type = "HTTP 404", "not_found"
                    return FetchResult(url=url, html=None, status=status, error=last_error, error_type=last_error_type)
                if status >= 500:
                    last_error, last_error_type = f"HTTP {status}", "server_error"
                    # worth retrying — fall through to the retry loop below
                else:
                    last_error, last_error_type = f"HTTP {status}", "other"
                    return FetchResult(url=url, html=None, status=status, error=last_error, error_type=last_error_type)
            else:
                html = await page.content()
                cache.set(url, html)
                return FetchResult(url=url, html=html, status=status)

        except PWTimeoutError:
            last_error, last_error_type = "navigation timeout", "timeout"
        except Exception as exc:  # noqa: BLE001 - deliberately broad, this must never crash the run
            last_error, last_error_type = f"{type(exc).__name__}: {exc}", "network"
        finally:
            if context is not None:
                await context.close()

        if attempt <= settings.nav_retries:
            await asyncio.sleep(1.5 * attempt)  # simple backoff

    logger.warning("giving up on %s after retries: %s", url, last_error)
    return FetchResult(url=url, html=None, status=None, error=last_error, error_type=last_error_type)


class PlaywrightFetcher:
    """Concrete PageFetcher backed by a live Playwright Browser."""

    def __init__(self, browser: Browser) -> None:
        self._browser = browser

    async def fetch(self, url: str) -> FetchResult:
        return await fetch_page(self._browser, url)


class BrowserSession:
    """Thin async context manager so callers don't juggle Playwright's own lifecycle."""

    def __init__(self) -> None:
        self._playwright = None
        self.browser: Optional[Browser] = None

    async def __aenter__(self) -> "BrowserSession":
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
