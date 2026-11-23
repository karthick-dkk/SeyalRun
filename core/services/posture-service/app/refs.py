"""Reference-URL hygiene. Stdlib only.

Finding references come from untrusted files (imported scanner results, feed
data) and the UI renders them as links, so only plain web links survive: the
scheme must be http or https and there must be a host. Everything else
(javascript:, data:, file:, vbscript:, relative paths, junk) is dropped — at
parse time AND again when a finding is served.
"""

from __future__ import annotations

from urllib.parse import urlsplit

MAX_REFS = 50
MAX_URL_LEN = 2000


def safe_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL_LEN:
        return None
    # Control characters / whitespace inside a URL are how scheme checks get
    # bypassed ("java\tscript:"); refuse them outright.
    if any(ord(c) < 0x21 or c == "\x7f" for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return url


def safe_urls(values, limit: int = MAX_REFS) -> list[str]:
    out: list[str] = []
    for v in values or []:
        u = safe_url(v)
        if u and u not in out:
            out.append(u)
            if len(out) >= limit:
                break
    return out
