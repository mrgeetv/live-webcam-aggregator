from __future__ import annotations

import json
from collections.abc import Iterator

from ..fetch import FetcherProtocol
from ..models import Candidate
from .base import predisc_key

# explore.org's livecams app pulls every stream from this CloudFront JSON (the `id_in`
# filter is ignored — one call returns them all). Each live stream has a direct HLS
# `playlistUrl` on an open CDN (no token/Referer), served by DirectHls.
_API = "https://d11gsgd2hj8qxd.cloudfront.net/streams.json"


class ExploreOrgSource:
    """explore.org live nature cams via its `streams.json` API. `state == "live"` streams
    only (skip on-demand/offline); the feed has no content category, so all -> "Other".
    """

    name: str = "explore"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        raw = self._fetch.get(_API)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        # the API returns {"streams": [...]} (older snapshots were a bare list)
        streams = data.get("streams", []) if isinstance(data, dict) else data
        if not isinstance(streams, list):
            return
        seen: set[str] = set()
        for s in streams:
            if not isinstance(s, dict) or s.get("state") != "live":
                continue
            url = str(s.get("playlistUrl") or "")
            if ".m3u8" not in url or url in seen:
                continue
            seen.add(url)
            yield Candidate(
                title=str(s.get("name") or "").strip(),
                angle_key=None,
                category=None,  # no category in the feed -> "Other"
                source=self.name,
                source_page_url=url,
                target_url=url,
                predisc_key=predisc_key(url),
            )
