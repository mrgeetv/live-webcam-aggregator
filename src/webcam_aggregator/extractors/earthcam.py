from __future__ import annotations

import re
from typing import TYPE_CHECKING

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# EarthCam pages embed the live HLS in a JSON config (forward-slashes \/-escaped):
#   https:\/\/videos-N.earthcam.com\/<path>\/playlist.m3u8?t=<token>&td=<ts>
# Prefer the LIVE feed (videos-N host, carries the ?t= token) over the *archives VOD
# hosts. The token is time-bound, so resolve fresh at serve-time. yt-dlp has no working
# EarthCam extractor, hence this. No live m3u8 = offline -> raise (liveness drops it).
# A whole URL run, kept if it holds `.m3u8` (why: sources.base._URL_RUN).
_LIVE_RUN = re.compile(r"https:(?:\\?/){2}videos-\d+\.earthcam\.com[^\"'\s]+")


class EarthcamResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        m = next((r for r in _LIVE_RUN.finditer(body) if ".m3u8" in r.group(0)), None)
        if not m:
            # no URL in the message: it feeds the aggregated resolve-failed detail
            # at INFO, where a URL would shatter one failure mode into per-cam
            # buckets (liveness already logs the URL at DEBUG)
            raise ValueError("earthcam: no live HLS (offline?)")
        return Resolved(
            url=m.group(0).replace("\\/", "/"), stream_type="hls", ttl_seconds=120
        )
