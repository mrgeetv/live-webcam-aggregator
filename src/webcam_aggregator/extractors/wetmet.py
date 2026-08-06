from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# WetMet publishes cams as a widget iframe, api.wetmet.net/widgets/stream/frame.php?uid=…,
# whose inline player script assigns the HLS master playlist outright:
#     var vurl = 'https://<edge>.wetmet.net/live/<id>/playlist.m3u8?wmsAuthSign=…'
#
# The playlist is Wowza/Nimble-signed: `wmsAuthSign` plus a `nimblesessionid` minted for
# whoever fetched the manifest, and both propagate down to the segment URLs — a segment
# stripped of its query 404s. The session therefore belongs to the fetcher, not the
# player, which is why wetmet.net is in serving._PROXY_SEGMENT_HOSTS.
_M3U8 = re.compile(r"""https?://[^"'\s<>]+\.m3u8[^"'\s<>]*""")


class WetmetResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        m = _M3U8.search(body)
        if not m:
            raise ValueError(f"wetmet: no m3u8 in widget frame {target_url}")
        return Resolved(
            url=m.group(0),
            stream_type="hls",
            # wmsAuthSign is time-limited; re-resolve rather than cache indefinitely.
            ttl_seconds=300,
        )
