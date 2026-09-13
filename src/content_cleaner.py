"""Reduce raw rendered HTML to clean, token-cheap text.

This is the "Step 2" token-optimization layer: we never hand raw HTML
trees to the LLM. Scripts, styles, SVGs, and nav/footer boilerplate are
stripped before the remaining text is flattened and truncated to a hard
character budget per page (see config.max_chars_per_page).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.config import settings

_STRIP_TAGS = ["script", "style", "svg", "noscript", "iframe", "template", "link", "meta"]
# Structural boilerplate: dropped by tag name AND by common nav/footer
# landmark roles, since many sites don't use <nav>/<footer> semantically.
_BOILERPLATE_SELECTORS = ["nav", "footer", "header"]


def clean_html_to_text(html: str, *, max_chars: int | None = None) -> str:
    """Return visible text stripped of markup noise, capped to a char budget."""
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for selector in _BOILERPLATE_SELECTORS:
        for tag in soup.find_all(selector):
            tag.decompose()

    # Drop elements explicitly hidden or marked decorative — cheap signal,
    # cuts a fair amount of cookie-banner / off-canvas-menu junk.
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines/whitespace left behind by stripped tags.
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)

    cap = max_chars if max_chars is not None else settings.max_chars_per_page
    if len(text) > cap:
        text = text[:cap] + "\n[...truncated for token budget...]"
    return text


def extract_emails(html: str) -> list[str]:
    """Pull generic public emails (contact@, sales@, support@, info@, etc.) from raw HTML.

    Regex over the raw HTML (not just visible text) so mailto: links
    count even if the anchor text itself is a display label like
    "Email us".
    """
    found = set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html))
    generic_prefixes = ("info", "contact", "sales", "support", "hello", "team", "press", "media")
    return sorted(e for e in found if e.split("@")[0].lower() in generic_prefixes)
