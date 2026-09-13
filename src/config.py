"""Central configuration for the lead enrichment agent.

Everything tunable lives here and is sourced from environment variables
(loaded from a local .env via python-dotenv) so the agent can be
retargeted — different model, stricter timeouts, more/fewer steps —
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


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in ("false", "0", "no", "")


# Ranking hint (not a hard filter) used when a page's links are handed to
# the agent — links matching these keywords are surfaced first, since
# they're usually what matters for lead enrichment.
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

# A small pool of realistic desktop browser UAs. Rotated per fetch
# attempt to reduce false-positive blocks from WAFs that fingerprint on
# a single static UA string, not to impersonate any specific user or
# defeat access controls on non-public content.
USER_AGENT_POOL: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model_name: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"))
    max_output_tokens: int = field(default_factory=lambda: _env_int("MAX_OUTPUT_TOKENS", 2000))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 5))

    # --- Agent loop ---
    max_agent_steps: int = field(default_factory=lambda: _env_int("MAX_AGENT_STEPS", 8))
    domain_concurrency: int = field(default_factory=lambda: _env_int("DOMAIN_CONCURRENCY", 3))

    # --- Crawling ---
    page_timeout_ms: int = field(default_factory=lambda: _env_int("PAGE_TIMEOUT_MS", 15_000))
    nav_retries: int = field(default_factory=lambda: _env_int("NAV_RETRIES", 2))

    # --- Content reduction (token optimization) ---
    max_chars_per_page: int = field(default_factory=lambda: _env_int("MAX_CHARS_PER_PAGE", 6000))

    # --- Bonus: web search tool (LinkedIn resolution etc.) ---
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    # --- Disk cache for fetched pages (cost/latency optimization only —
    # never a correctness feature; safe to delete at any time) ---
    cache_enabled: bool = field(default_factory=lambda: _env_bool("CACHE_ENABLED", True))
    cache_dir: str = field(default_factory=lambda: os.getenv("CACHE_DIR", ".cache/pages"))
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("CACHE_TTL_SECONDS", 86_400))

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
    user_agent_pool: tuple[str, ...] = tuple(USER_AGENT_POOL)


settings = Settings()
