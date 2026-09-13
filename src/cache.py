"""Tiny disk cache for fetched page HTML, keyed by URL.

Saves re-scraping the same page across repeated runs (development
iteration, re-running after a crash, CI, etc.) — purely a cost/latency
optimization, never a correctness dependency. Safe to delete the cache
directory at any time; a cache miss just falls through to a real fetch.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from src.config import settings


def _path_for(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(settings.cache_dir) / f"{key}.json"


def get(url: str) -> Optional[str]:
    if not settings.cache_enabled:
        return None
    path = _path_for(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - data.get("fetched_at", 0) > settings.cache_ttl_seconds:
        return None
    return data.get("html")


def set(url: str, html: str) -> None:  # noqa: A001 - mirrors dict-like get/set naming
    if not settings.cache_enabled:
        return
    path = _path_for(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"url": url, "html": html, "fetched_at": time.time()}
        path.write_text(json.dumps(entry), encoding="utf-8")
    except OSError:
        pass  # cache is best-effort; a write failure should never break a fetch
