"""Tool schemas and execution for the navigation agent.

Three tools are exposed to the model:

- visit_page           — fetch + clean a same-domain URL, returns text,
                          generic emails, and same-domain links found.
- search_web           — (optional, needs TAVILY_API_KEY) look up
                          something on the open web; used mainly to
                          resolve LinkedIn URLs for people identified on
                          the company's own site but not linked there.
- submit_intelligence  — end the research loop with the final record
                          (schema built from CompanyIntelligence).

Keeping tool execution here — rather than inline in agent.py — keeps
the control-flow loop readable and makes each tool independently
testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from src.browser import PageFetcher
from src.config import settings
from src.content_cleaner import clean_html_to_text, extract_emails
from src.link_discovery import extract_links
from src.schemas import CompanyIntelligence

logger = logging.getLogger(__name__)

VISIT_PAGE_TOOL = {
    "name": "visit_page",
    "description": (
        "Fetch and read a page from the TARGET company's own website. "
        "Returns the page's cleaned text content, any generic contact "
        "emails found on it, and a list of same-domain links found on "
        "it (with anchor text) so you can decide where to look next. "
        "Only works for URLs on the target domain — external URLs are "
        "rejected; use search_web for anything off-site."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch, e.g. https://example.com/about",
            }
        },
        "required": ["url"],
    },
}

SEARCH_WEB_TOOL = {
    "name": "search_web",
    "description": (
        "Search the open web. Use this ONLY to resolve a LinkedIn "
        "profile URL for a named leader/founder you already found on "
        "the company's own site but who has no LinkedIn link there. "
        "Not for general company research — prefer visit_page for "
        "anything on the target's own domain. May be unavailable if no "
        "search API key is configured; if so, proceed without it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}


def submit_tool_schema() -> dict:
    schema = CompanyIntelligence.model_json_schema()
    # The model shouldn't re-derive the domain — the caller already
    # knows it from the crawl target — so drop it from the tool's input
    # schema and inject it after the call instead.
    schema["properties"].pop("domain", None)
    if "required" in schema:
        schema["required"] = [f for f in schema["required"] if f != "domain"]
    return {
        "name": "submit_intelligence",
        "description": (
            "Call this exactly once, when you have gathered enough "
            "information (or after exploring what's reasonably "
            "available) to produce the final structured record. This "
            "ends the research process for this domain."
        ),
        "input_schema": schema,
    }


@dataclass
class ToolOutcome:
    tool_result_text: str
    is_error: bool
    fetched_url: Optional[str] = None
    ok: bool = False


async def execute_visit_page(
    fetcher: PageFetcher, target_domain: str, url: str, visited_cache: dict[str, str]
) -> ToolOutcome:
    if not url:
        return ToolOutcome(tool_result_text="No URL provided.", is_error=True)

    netloc = urlparse(url).netloc.lower().removeprefix("www.")
    target_netloc = target_domain.lower().removeprefix("www.")
    if not netloc or netloc != target_netloc:
        return ToolOutcome(
            tool_result_text=(
                f"Rejected: '{url}' is not on the target domain "
                f"({target_domain}). Only same-domain URLs can be visited "
                f"with visit_page — use search_web for anything external."
            ),
            is_error=True,
        )

    if url in visited_cache:
        return ToolOutcome(
            tool_result_text=f"(already visited earlier this run — reusing content)\n{visited_cache[url]}",
            is_error=False,
            fetched_url=url,
            ok=True,
        )

    result = await fetcher.fetch(url)
    if not result.ok:
        reason = result.error_type or "unknown"
        return ToolOutcome(
            tool_result_text=f"Could not retrieve {url} ({reason}): {result.error}",
            is_error=True,
            fetched_url=url,
            ok=False,
        )

    text = clean_html_to_text(result.html or "")
    emails = extract_emails(result.html or "")
    links = extract_links(result.html or "", url, limit=25)
    link_lines = "\n".join(f"- {l['text'] or '(no text)'}: {l['url']}" for l in links) or "(none found)"
    payload = (
        f"--- Content of {url} ---\n{text}\n\n"
        f"--- Generic emails found on this page ---\n{', '.join(emails) or '(none)'}\n\n"
        f"--- Same-domain links found on this page ---\n{link_lines}"
    )
    visited_cache[url] = payload
    return ToolOutcome(tool_result_text=payload, is_error=False, fetched_url=url, ok=True)


async def execute_search_web(query: str) -> ToolOutcome:
    if not query:
        return ToolOutcome(tool_result_text="No query provided.", is_error=True)

    if not settings.tavily_api_key:
        return ToolOutcome(
            tool_result_text=(
                "search_web is unavailable (no TAVILY_API_KEY configured). "
                "Proceed without it — leave linkedin_url null if not found on-site."
            ),
            is_error=True,
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": settings.tavily_api_key, "query": query, "max_results": 5},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - fallback must never break the main run
        logger.warning("search_web failed for %r: %s", query, exc)
        return ToolOutcome(tool_result_text=f"Search failed: {exc}. Proceed without it.", is_error=True)

    results = data.get("results", [])[:5]
    if not results:
        return ToolOutcome(tool_result_text="No results found.", is_error=False, ok=True)
    lines = [f"- {r.get('title', '')}: {r.get('url', '')}" for r in results]
    return ToolOutcome(tool_result_text="\n".join(lines), is_error=False, ok=True)
