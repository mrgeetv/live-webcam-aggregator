from __future__ import annotations

import json
import re
from collections.abc import Callable

from .base import Resolved

# viewsurf cams play through Quanteec's joada.net player. The embed URL carries the
# cam's stable uuid; the player asks the platform API for the current sources:
#   GET https://platforms<N>.joada.net/api/videos/manifest/<uuid>
#     -> {"m3u8": "https://<delivery>/contents/encodings/<live|vod>/<uuid>/master.m3u8", …}
#
# The HLS chain is open: no token in any URL, and manifests + fMP4 segments serve
# without a Referer — so nothing here needs _PROXY_SEGMENT_HOSTS. But the delivery
# host is the API's choice per call (a cache/edge that can rebalance), so the TTL is
# moderate rather than treating the URL as permanent; a re-resolve is one GET.
#
# The player rotates across several platforms<N> hosts because not all of them are
# healthy (expired certs, dead hosts among them); we pin two verified-good ones and
# fall back between them.
_API_HOSTS = ("https://platforms8.joada.net", "https://platforms9.joada.net")
_UUID = re.compile(r"[?&]uuid=([0-9a-f-]+)")


class ViewsurfResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        m = _UUID.search(target_url)
        if not m:
            raise ValueError("viewsurf: no uuid in embed url")
        uuid = m.group(1)
        # A dead platform host can answer with an HTML maintenance page (a 200, not a
        # raised fetch), so treat a non-JSON body as a failure and fall through to the
        # next host too — not just a raised one.
        doc: dict[str, object] | None = None
        for host in _API_HOSTS:
            try:
                body = self._fetch(f"{host}/api/videos/manifest/{uuid}")
            except ValueError:
                continue
            try:
                parsed = json.loads(body)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                doc = parsed
                break
        if doc is None:
            raise ValueError("viewsurf: no manifest from any api host")
        m3u8 = doc.get("m3u8")
        if not isinstance(m3u8, str) or not m3u8:
            raise ValueError("viewsurf: no m3u8 in manifest api response")
        return Resolved(url=m3u8, stream_type="hls", ttl_seconds=1800)
