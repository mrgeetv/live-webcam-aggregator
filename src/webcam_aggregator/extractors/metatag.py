from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

_OG = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:video|twitter:player)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)


class MetaTagExtractor:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        html = self._fetch(target_url)
        m = _OG.search(html)
        if not m:
            raise ValueError(f"no og:video in {target_url}")
        url = m.group(1)
        return Resolved(
            url=url, stream_type="mp4" if ".mp4" in url else "hls", ttl_seconds=None
        )
