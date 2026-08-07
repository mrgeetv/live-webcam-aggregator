from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# Skyline cam pages embed a Clappr player whose source is `livee.m3u8?a=<token>`, and
# the real manifest lives at hd-auth.skylinewebcams.com/live.m3u8.
# No token (offline cam / 404) -> unresolvable (raise; liveness validation drops it).
#
# The token STRING is stable per cam — the same page hands back the same value for at
# least tens of minutes — but its authorisation is not: left unused it lapses in
# ~1-2 minutes (measured: alive at 60s, dead by 150s across several cams). Re-fetching
# the page is what re-arms it, which is why serve-time resolves the page afresh rather
# than reusing a stored token. A lapsed token does NOT 404: hd-auth answers 200 with an
# empty playlist, so anything checking it must test for content, not the #EXTM3U header
# (serving.is_playable_manifest).
_TOKEN = re.compile(r"source:'livee?\.m3u8\?a=([a-z0-9]+)'")
_MANIFEST = "https://hd-auth.skylinewebcams.com/live.m3u8?a="
# Under the ~1-2 min idle lapse above, with ResolveCache's TTL_FACTOR on top (so the
# entry is reused for ~48s). A longer TTL hands a second viewer a token that went stale
# while nobody was watching, and the stream silently refuses to start until it expires.
_TTL_SECONDS = 60


class SkylineResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        m = _TOKEN.search(body)
        if not m:
            # No URL in the message: it becomes an aggregated INFO detail, where a
            # URL is both a log-policy leak (paths stay at DEBUG) and a bucket
            # splitter (details truncate, so URLs shatter one failure mode into
            # per-prefix counts). Liveness logs the URL at DEBUG already.
            raise ValueError("skyline: no stream token (offline?)")
        return Resolved(
            url=f"{_MANIFEST}{m.group(1)}",
            stream_type="hls",
            ttl_seconds=_TTL_SECONDS,
        )
