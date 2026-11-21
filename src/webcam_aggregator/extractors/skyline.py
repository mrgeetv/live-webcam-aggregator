from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# Skyline cam pages embed a Clappr player whose source is `livee.m3u8?a=<token>`, and
# the real manifest lives at hd-auth.skylinewebcams.com/live.m3u8. The player config
# is hand-formatted JS, so the quote style and spacing around `source:` are not
# stable — the pattern tolerates either.
# No token (offline cam / 404) -> unresolvable (raise; liveness validation drops it).
#
# The token STRING is stable per cam — the same page hands back the same value for at
# least tens of minutes — but its authorisation is not: left unused it lapses in
# ~1-2 minutes (measured: alive at 60s, dead by 150s across several cams). Re-fetching
# the page is what re-arms it, which is why serve-time resolves the page afresh rather
# than reusing a stored token. A lapsed token does NOT 404: hd-auth answers 200 with an
# empty playlist, so anything checking it must test for content, not the #EXTM3U header
# (serving.is_playable_manifest).
_TOKEN = re.compile(r"""source:\s*["']livee?\.m3u8\?a=([a-z0-9]+)["']""")
_MANIFEST = "https://hd-auth.skylinewebcams.com/live.m3u8?a="
# Under the ~1-2 min idle lapse above, with ResolveCache's TTL_FACTOR on top (so the
# entry is reused for ~48s). A longer TTL hands a second viewer a token that went stale
# while nobody was watching, and the stream silently refuses to start until it expires.
_TTL_SECONDS = 60


def stream_token(html: str) -> str | None:
    """The cam page's Clappr stream token, or None for a page with no own-HLS player
    (offline, or a "from the web" YouTube embed)."""
    m = _TOKEN.search(html)
    return m.group(1) if m else None


class SkylineResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        token = stream_token(self._fetch(target_url))
        if not token:
            # No URL in the message: it becomes an aggregated INFO detail, where a
            # URL is both a log-policy leak (paths stay at DEBUG) and a bucket
            # splitter (details truncate, so URLs shatter one failure mode into
            # per-prefix counts). Liveness logs the URL at DEBUG already.
            raise ValueError("skyline: no stream token (offline?)")
        return Resolved(
            url=f"{_MANIFEST}{token}",
            stream_type="hls",
            ttl_seconds=_TTL_SECONDS,
        )
