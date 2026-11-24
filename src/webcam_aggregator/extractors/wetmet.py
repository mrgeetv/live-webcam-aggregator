from __future__ import annotations

import re
from typing import TYPE_CHECKING

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# WetMet publishes cams as a widget iframe, api.wetmet.net/widgets/stream/frame.php?uid=…,
# whose inline player script assigns the HLS master playlist outright:
#     var vurl = 'https://<edge>.wetmet.net/live/<id>/playlist.m3u8?wmsAuthSign=…'
#
# The playlist is Wowza/Nimble-signed: `wmsAuthSign` plus a `nimblesessionid` minted for
# whoever fetched the manifest, and both propagate down to the segment URLs — a segment
# stripped of its query 404s. The session therefore belongs to the fetcher, not the
# player, which is why wetmet.net is in serving._PROXY_SEGMENT_HOSTS.
# A whole URL run, kept if it holds `.m3u8` (why: sources.base._URL_RUN).
_URL_RUN = re.compile(r"""https?://[^"'\s<>]+""")


class WetmetResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        m = next((r for r in _URL_RUN.finditer(body) if ".m3u8" in r.group(0)), None)
        if not m:
            # no URL in the message — see the aggregated-detail rule in AGENTS.md
            raise ValueError("wetmet: no m3u8 in widget frame")
        return Resolved(
            url=m.group(0),
            stream_type="hls",
            # wmsAuthSign is time-limited; re-resolve rather than cache indefinitely.
            ttl_seconds=300,
        )
