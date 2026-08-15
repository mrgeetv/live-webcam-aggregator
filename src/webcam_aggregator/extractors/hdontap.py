from __future__ import annotations

import json
import re
from collections.abc import Callable

from .base import Resolved

# HDOnTap cam pages ship the player config in the static HTML:
#   <script id="player-data" type="application/json">
#     {..., "streamSrc": ".../playlist.m3u8?t=<token>&e=<epoch>", ...}
# The `t`/`e` pair is minted per page fetch and BOTH params are required — a URL
# clipped at the JSON-escaped `&` gets a 403 from the edge — so the blob is
# json.loads-ed (which decodes the escape) rather than regexed out of the raw HTML.
# An offline cam renders its page with no blob at all.
_BLOB = re.compile(r'<script[^>]+id="player-data"[^>]*>(.*?)</script>', re.I | re.S)


def stream_src(html: str) -> str | None:
    """The token-signed HLS URL in a page's player-data blob, or None (an offline
    page, or a YouTube-embed cam). Shared with `sources.hdontap`, which uses it to
    tell native-HLS cam pages from the rest."""
    m = _BLOB.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    src = data.get("streamSrc")
    return src if isinstance(src, str) and ".m3u8" in src else None


class HdontapResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        src = stream_src(self._fetch(target_url))
        if not src:
            raise ValueError("hdontap: no player-data stream (offline?)")
        return Resolved(
            url=src,
            stream_type="hls",
            # the t/e token stays valid for hours; a shorter TTL is cheap (the
            # re-resolve is one unpaced page fetch) and keeps a healthy margin.
            ttl_seconds=3600,
        )
