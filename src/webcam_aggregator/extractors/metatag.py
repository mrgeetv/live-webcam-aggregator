"""Meta-tag extractor: a page's `og:video` or `twitter:player` URL is the stream."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# The property is checked by a lookahead, so attribute order doesn't matter and one
# unclosed tag repeating it isn't rescanned from every repeat.
_OG = re.compile(
    r'<meta(?=[^<>]+?(?:property|name)=["\'](?:og:video|twitter:player)["\'])'
    r'[^<>]+?content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


class MetaTagExtractor:
    """Resolve a page to the stream URL in its `og:video` or `twitter:player` tag."""

    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        """Take the `fetch` callable that returns a page body as text."""
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        """Resolve `target_url` to its meta-tag stream URL (mp4 or hls), or raise."""
        html = self._fetch(target_url)
        m = _OG.search(html)
        if not m:
            # no URL in the message — see the aggregated-detail rule in AGENTS.md
            msg = "no og:video"
            raise ValueError(msg)
        url = m.group(1)
        return Resolved(
            url=url, stream_type="mp4" if ".mp4" in url else "hls", ttl_seconds=None
        )
