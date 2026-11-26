"""explore.org source: live streams from its `streams.json` API (direct HLS)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import predisc_key

if TYPE_CHECKING:
    from collections.abc import Iterator

    from webcam_aggregator.fetch import FetcherProtocol

# explore.org's livecams app pulls every stream from this CloudFront JSON (the `id_in`
# filter is ignored — one call returns them all). Each live stream's `playlistUrl` is a
# direct HLS on explore's MediaMTX edges, served by DirectHls; their segments are bound
# to the fetcher's IP, hence the segment relay (`serving._PROXY_SEGMENT_HOSTS`).
_API = "https://d3ll4jl6s7s24a.cloudfront.net/streams.json"


class ExploreOrgSource:
    """explore.org live nature cams via its `streams.json` API.

    Only `state == "live"` streams are kept (on-demand/offline are skipped); the feed
    has no content category, so all -> "Other".
    """

    name: str = "explore"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        """Take the `fetch` used for every request."""
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        """Yield each live stream that has an m3u8 playlist URL."""
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
        for s in streams:
            if not isinstance(s, dict) or s.get("state") != "live":
                continue
            url = str(s.get("playlistUrl") or "")
            if ".m3u8" not in url:
                continue
            yield Candidate(
                title=str(s.get("name") or "").strip(),
                angle_key=None,
                category=None,  # no category in the feed -> "Other"
                source=self.name,
                source_page_url=url,
                target_url=url,
                predisc_key=predisc_key(url),
            )
