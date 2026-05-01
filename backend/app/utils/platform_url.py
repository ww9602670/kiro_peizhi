"""Platform URL validation helpers."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

ALLOWED_PLATFORM_URL_SCHEMES = {"http", "https"}


def normalize_platform_url(raw_url: str | None) -> str:
    """Return a stable platform URL, or an empty string when invalid."""
    url = (raw_url or "").strip()
    if not url:
        return ""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if scheme not in ALLOWED_PLATFORM_URL_SCHEMES or not netloc:
        return ""

    path = (parsed.path or "").rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))
