"""HDOnTap extractor: cam page `player-data` blob -> its token-signed HLS URL."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# HDOnTap cam pages ship the player config in the static HTML:
#   <script id="player-data" type="application/json">
#     {..., "streamSrc": ".../playlist.m3u8?t=<token>&e=<epoch>", ...}
# The `t`/`e` pair is minted per page fetch and BOTH params are required — a URL
# clipped at the JSON-escaped `&` gets a 403 from the edge — so the blob is
# json.loads-ed (which decodes the escape) rather than regexed out of the raw HTML.
# An offline cam renders its page with no blob at all.
# The id is found by a lookahead inside the tag and the tag skipped possessively, and
# the body may hold any `<` except another script tag: each keeps an unclosed tag or
# body from being rescanned from every repeat inside it.
_BLOB = re.compile(
    r'<script(?=[^<>]+?id="player-data")[^<>]*+>((?:[^<]|<(?!/?script))*+)</script>',
    re.IGNORECASE,
)


def stream_src(html: str) -> str | None:
    """Return the token-signed HLS URL in a page's player-data blob, or None.

    None means an offline page or a YouTube-embed cam. Shared with `sources.hdontap`,
    which uses it to tell native-HLS cam pages from the rest.
    """
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
    """Resolve an HDOnTap cam page to its token-signed HLS URL."""

    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        """Take the `fetch` callable that returns a page body as text."""
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        """Resolve `target_url` to the HLS URL in its page's player-data blob."""
        src = stream_src(self._fetch(target_url))
        if not src:
            msg = "hdontap: no player-data stream (offline?)"
            raise ValueError(msg)
        return Resolved(
            url=src,
            stream_type="hls",
            # the t/e token stays valid for hours; a shorter TTL is cheap (the
            # re-resolve is one unpaced page fetch) and keeps a healthy margin.
            ttl_seconds=3600,
        )
