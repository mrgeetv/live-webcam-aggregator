from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import quote

from .base import Resolved

# Ozolio cams live behind relay.ozolio.com's two-call session API (the same exchange
# the /explore page's player makes):
#   1. ses.api?cmd=init&oid=CID_<id>&…&document=<explore iframe URL>
#        -> {"session": {"id": "SID_…"}}
#      The `document` query param is the gate: without it the relay answers 403.
#      (Referer/Origin headers are NOT required, so the plain resolver fetch works.)
#   2. ses.api?cmd=open&oid=<SID>&output=1&format=M3U8&profile=AUTO
#        -> {"output": {"source": <Wowza HLS>, "media": "LIVE"|"ROLL", …}}
#
# `media` separates a real camera ("LIVE", an /hls-live/ source) from a canned
# media-library loop ("ROLL", an /hls-roll/ mp4) some entries play instead; a loop is
# not a webcam, so it is rejected rather than served.
#
# The relay host in `source` is load-balanced per open call, and the Wowza chunklist
# session is minted for whoever fetches the manifest — a stale session's segments 403.
# Hence ozolio.com is in serving._PROXY_SEGMENT_HOSTS and the TTL below is short.

_RELAY = "https://relay.ozolio.com"
# The explore page path carries the bare id; tolerate a CID_-prefixed form too, since
# [A-Z0-9]+ would otherwise match just the "CID" prefix and silently query CID_CID.
_CID = re.compile(r"ozolio\.com/explore/(?:CID_)?([A-Z0-9]+)")
# The session id is interpolated into the open URL, so hold it to the shape the relay
# actually mints rather than trusting response JSON into a URL.
_SID = re.compile(r"SID_[A-Z0-9]+")


class OzolioResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def _api(self, url: str) -> dict[str, object]:
        body = self._fetch(url)
        try:
            doc = json.loads(body)
        except ValueError:
            # The relay's failure mode is an HTML error page, not JSON.
            raise ValueError("ozolio: relay returned non-JSON") from None
        return doc if isinstance(doc, dict) else {}

    def resolve(self, target_url: str) -> Resolved:
        m = _CID.search(target_url)
        if not m:
            raise ValueError("ozolio: no camera id in target")
        cid = m.group(1)
        document = quote(
            f"{_RELAY}/pub.api?cmd=explore&oid=CID_{cid}&channel=0", safe=""
        )
        init = self._api(
            f"{_RELAY}/ses.api?cmd=init&oid=CID_{cid}"
            f"&ver=5&channel=0&control=0&document={document}"
        )
        session = init.get("session")
        sid = session.get("id") if isinstance(session, dict) else None
        if not isinstance(sid, str) or not _SID.fullmatch(sid):
            raise ValueError("ozolio: no relay session (offline?)")
        opened = self._api(
            f"{_RELAY}/ses.api?cmd=open&oid={sid}&output=1&format=M3U8&profile=AUTO"
        )
        output = opened.get("output")
        source = output.get("source") if isinstance(output, dict) else None
        media = output.get("media") if isinstance(output, dict) else None
        if not isinstance(source, str) or not source:
            raise ValueError("ozolio: no stream in open response (offline?)")
        if media != "LIVE" or "/hls-live/" not in source:
            raise ValueError("ozolio: canned loop, not a live camera")
        return Resolved(
            url=source,
            stream_type="hls",
            # Short by design: the Wowza session lapses for anyone but its fetcher and
            # the relay host rotates per open — re-resolve well before either bites.
            ttl_seconds=180,
        )
