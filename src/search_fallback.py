"""Bonus feature: look up LinkedIn URLs for leaders not found on-site.

Uses Tavily (https://tavily.com) if TAVILY_API_KEY is set — it's a
search API built for LLM pipelines and returns clean results without
needing our own SERP scraping/anti-bot handling. If no key is
configured, this step is skipped entirely and every found leader just
keeps whatever `linkedin_url` (if any) the on-site extraction produced.
"""

from __future__ import annotations

import logging
import re

import httpx

from src.config import settings
from src.schemas import CompanyIntelligence

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?")


def _find_linkedin_via_tavily(name: str, company: str) -> str | None:
    try:
        resp = httpx.post(
            _TAVILY_URL,
            json={
                "api_key": settings.tavily_api_key,
                "query": f"{name} {company} linkedin",
                "max_results": 5,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - fallback must never break the main run
        logger.warning("Tavily lookup failed for %s: %s", name, exc)
        return None

    for result in data.get("results", []):
        url = result.get("url", "")
        match = _LINKEDIN_RE.search(url)
        if match:
            return match.group(0)
    return None


def enrich_missing_linkedin_urls(intelligence: CompanyIntelligence, company_name: str) -> int:
    """Fill in missing linkedin_url fields in place. Returns count of lookups performed."""
    if not settings.tavily_api_key:
        return 0

    lookups = 0
    for member in intelligence.leadership:
        if member.linkedin_url:
            continue
        found = _find_linkedin_via_tavily(member.name, company_name)
        lookups += 1
        if found:
            member.linkedin_url = found
            member.source = "search"
    return lookups
