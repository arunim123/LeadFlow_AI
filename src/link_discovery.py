"""Extract same-domain links from a page, for the navigation agent to reason over.

Keyword-matched links (about/team/pricing/etc.) are ranked first — they're
usually what matters for lead enrichment — but this is an ordering hint,
not a hard filter: the full same-domain link set (capped) is returned so
the agent can also follow links a keyword list wouldn't anticipate (e.g.
a company that calls its team page "The Humans of Acme").
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.config import settings


def _keyword_score(path: str) -> int:
    for rank, keyword in enumerate(settings.subpage_keywords):
        if keyword in path:
            return 100 - rank
    return 0


def extract_links(html: str, base_url: str, limit: int = 40) -> list[dict]:
    """Return up to `limit` same-domain links as [{"url": ..., "text": ...}, ...],
    ranked with keyword-matched paths first, deduped by normalized URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_netloc = urlparse(base_url).netloc.lower().removeprefix("www.")

    seen: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        netloc = parsed.netloc.lower().removeprefix("www.")
        if netloc != base_netloc:
            continue  # stay on-domain; external links aren't part of "their web presence"

        path = parsed.path.rstrip("/")
        if not path:
            continue

        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_url in seen:
            continue

        text = " ".join(anchor.get_text(separator=" ").split())[:80]
        seen[clean_url] = {"url": clean_url, "text": text, "_score": _keyword_score(path.lower())}

    ranked = sorted(seen.values(), key=lambda d: d["_score"], reverse=True)[:limit]
    for entry in ranked:
        entry.pop("_score", None)
    return ranked
