"""Find relevant subpages (about/team/company/contact/pricing) from a homepage.

Deliberately keyword-driven rather than "crawl everything n levels deep":
for lead enrichment we want a handful of high-signal pages, not a full
site mirror, both for token budget and for latency per domain.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.config import settings


def discover_subpages(homepage_html: str, base_url: str) -> list[str]:
    """Return up to `settings.max_subpages` same-domain URLs worth fetching."""
    soup = BeautifulSoup(homepage_html, "html.parser")
    base_netloc = urlparse(base_url).netloc.lower().removeprefix("www.")

    candidates: dict[str, int] = {}  # url -> match score (higher = more specific match)
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        netloc = parsed.netloc.lower().removeprefix("www.")
        if netloc != base_netloc:
            continue  # stay on-domain; external links aren't part of "their web presence"

        path = parsed.path.lower().rstrip("/")
        if not path:
            continue

        for rank, keyword in enumerate(settings.subpage_keywords):
            if keyword in path:
                # Prefer exact-looking matches (e.g. "/about") over incidental
                # substring hits (e.g. "/about-our-security-practices").
                score = 100 - rank - (len(path) - len(keyword))
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if clean_url not in candidates or score > candidates[clean_url]:
                    candidates[clean_url] = score
                break

    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    return [url for url, _ in ranked[: settings.max_subpages]]
