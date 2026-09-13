"""Central configuration for the lead enrichment agent.

Everything tunable lives here and is sourced from environment variables
(loaded from a local .env via python-dotenv) so the pipeline can be
retargeted — different model, stricter timeouts, more/fewer subpages —
without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Subpage slugs we actively look for when crawling a homepage. The link
# discovery step matches these against every <a href> found, so a site
# that uses "/company" instead of "/about" is still picked up.
DEFAULT_SUBPAGE_KEYWORDS: list[str] = [
    "about",
    "about-us",
    "company",
    "team",
    "leadership",
    "our-team",
    "contact",
    "contact-us",
    "pricing",
]


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model_name: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"))
    max_output_tokens: int = field(default_factory=lambda: _env_int("MAX_OUTPUT_TOKENS", 2000))

    # --- Crawling ---
    max_subpages: int = field(default_factory=lambda: _env_int("MAX_SUBPAGES", 5))
    page_timeout_ms: int = field(default_factory=lambda: _env_int("PAGE_TIMEOUT_MS", 15_000))
    nav_retries: int = field(default_factory=lambda: _env_int("NAV_RETRIES", 2))
    concurrent_pages: int = field(default_factory=lambda: _env_int("CONCURRENT_PAGES", 3))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SCRAPER_USER_AGENT",
            "Mozilla/5.0 (compatible; LeadEnrichmentBot/1.0; "
            "+https://example.com/bot-info)",
        )
    )

    # --- Content reduction (token optimization) ---
    max_chars_per_page: int = field(default_factory=lambda: _env_int("MAX_CHARS_PER_PAGE", 6000))
    max_chars_total: int = field(default_factory=lambda: _env_int("MAX_CHARS_TOTAL", 20_000))

    # --- Bonus: search fallback for LinkedIn discovery ---
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    # --- Cost tracking ---
    # Approximate list pricing, USD per million tokens. Verify current
    # rates at https://www.anthropic.com/pricing before relying on this
    # for real accounting — it exists to give a directional per-domain
    # cost estimate, not an invoice-grade figure.
    price_per_million_input: float = field(
        default_factory=lambda: _env_float("PRICE_PER_M_INPUT", 3.00)
    )
    price_per_million_output: float = field(
        default_factory=lambda: _env_float("PRICE_PER_M_OUTPUT", 15.00)
    )

    subpage_keywords: tuple[str, ...] = tuple(DEFAULT_SUBPAGE_KEYWORDS)


settings = Settings()
