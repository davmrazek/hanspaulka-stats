"""Rate-limited, disk-cached HTTP layer.

The ONLY place in the project where `requests` is imported.

Rules (see CLAUDE.md):
- max 1 request/second, enforced here
- honest User-Agent with contact email
- every fetched page cached as raw HTML in cache/, keyed by URL slug
- cached pages are never re-fetched unless force=True (current season)
- max 2 retries per URL with backoff; then fail loudly
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests

VERSION = "0.1.0"
USER_AGENT = f"hanspaulka-stats/{VERSION} (contact: davmrazek@seznam.cz)"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
MIN_INTERVAL = 1.0  # seconds between requests
MAX_RETRIES = 2
BACKOFF = 2.0  # seconds; doubled per retry

_last_request_time = 0.0


class FetchError(Exception):
    """Raised when a URL cannot be fetched after retries. Do not catch and retry."""


def url_to_slug(url: str) -> str:
    """Turn a URL into a filesystem-safe cache filename."""
    slug = re.sub(r"^https?://", "", url)
    slug = slug.strip("/")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", slug)
    return slug + ".html"


def cache_path(url: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / url_to_slug(url)


def _throttle() -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def get(url: str, *, force: bool = False, cache_dir: Path | None = None) -> str:
    """Return page HTML, from cache if available.

    force=True re-fetches (use only for the current, unfinished season).
    Raises FetchError after MAX_RETRIES failed attempts.
    """
    path = cache_path(url, cache_dir)
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")

    last_error: Exception | None = None
    for attempt in range(1 + MAX_RETRIES):
        if attempt > 0:
            time.sleep(BACKOFF * (2 ** (attempt - 1)))
        _throttle()
        try:
            response = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=30
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            last_error = exc
            continue
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return html

    raise FetchError(f"failed to fetch {url} after {1 + MAX_RETRIES} attempts") from last_error
