"""Headless browser fetching.

Uses Playwright (Chromium) so JS-rendered marketing sites (Next.js,
client-side React, etc.) come back with fully hydrated DOM instead of
an empty shell. Every call here is wrapped so a single bad page
(timeout, 404, bot block) returns `None` plus a reason instead of
raising — the pipeline layer is responsible for deciding what to do
with a miss, this layer just reports it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, TimeoutError as PWTimeoutError, async_playwright

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    html: Optional[str]
    status: Optional[int]
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.html is not None


async def fetch_page(browser: Browser, url: str) -> FetchResult:
    """Fetch one URL with a fresh page/context, retrying on timeout.

    Never raises. Bot blockers (403/429), 404s, DNS failures, and
    navigation timeouts all fall through to a FetchResult with
    html=None and a human-readable `error`, so the caller can log it
    and move on to the next page/domain without the whole run dying.
    """
    last_error = "unknown error"
    for attempt in range(1, settings.nav_retries + 2):  # +1 initial try
        context = None
        try:
            context = await browser.new_context(user_agent=settings.user_agent)
            page = await context.new_page()
            response = await page.goto(
                url, timeout=settings.page_timeout_ms, wait_until="domcontentloaded"
            )
            # Give client-side rendered content a brief moment to settle.
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except PWTimeoutError:
                pass  # Not fatal — some sites never go fully idle (analytics beacons etc.)

            status = response.status if response else None
            if status and status >= 400:
                last_error = f"HTTP {status}"
                if status in (403, 429):
                    # Likely a bot blocker — retrying won't help, fail fast for this URL.
                    return FetchResult(url=url, html=None, status=status, error=last_error)
                # Other 4xx/5xx: fall through to retry loop below.
            else:
                html = await page.content()
                return FetchResult(url=url, html=html, status=status)

        except PWTimeoutError:
            last_error = "navigation timeout"
        except Exception as exc:  # noqa: BLE001 - deliberately broad, this must never crash the run
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if context is not None:
                await context.close()

        if attempt <= settings.nav_retries:
            await asyncio.sleep(1.5 * attempt)  # simple backoff

    logger.warning("giving up on %s after retries: %s", url, last_error)
    return FetchResult(url=url, html=None, status=None, error=last_error)


async def fetch_many(browser: Browser, urls: list[str]) -> list[FetchResult]:
    """Fetch several URLs concurrently, capped by settings.concurrent_pages."""
    semaphore = asyncio.Semaphore(settings.concurrent_pages)

    async def _bounded(u: str) -> FetchResult:
        async with semaphore:
            return await fetch_page(browser, u)

    return await asyncio.gather(*(_bounded(u) for u in urls))


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
