from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# Skyline cam pages embed a Clappr player whose source is `livee.m3u8?a=<token>`. The
# token is regenerated per page-load, so resolve it FRESH at serve-time (don't cache the
# discovery-time one). The real manifest lives at hd-auth.skylinewebcams.com/live.m3u8.
# No token (offline cam / 404) -> unresolvable (raise; liveness validation drops it).
_TOKEN = re.compile(r"source:'livee?\.m3u8\?a=([a-z0-9]+)'")
_MANIFEST = "https://hd-auth.skylinewebcams.com/live.m3u8?a="


class SkylineResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        m = _TOKEN.search(body)
        if not m:
            raise ValueError(f"skyline: no stream token (offline?) in {target_url}")
        # Short TTL: the token expires, so re-resolve the page periodically (cheap).
        return Resolved(
            url=f"{_MANIFEST}{m.group(1)}", stream_type="hls", ttl_seconds=300
        )
