"""Host matching: the one check of which site a URL points at."""

from __future__ import annotations

from urllib.parse import urlsplit


def on_host(url: str, *domains: str) -> bool:
    """Return whether `url`'s host is one of `domains` or a subdomain of one.

    A URL that doesn't parse is on no host.
    """
    try:
        host = (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)
