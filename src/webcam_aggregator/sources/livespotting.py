from __future__ import annotations

import json
import re
from collections.abc import Iterator

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import predisc_key, with_location_parts

_BASE = "https://www.livespotting.tv"
# The consumer directory's server-rendered country pages. Hardcoded rather than
# crawled from the homepage nav: the nav mixes the country links with /standort,
# /kanal and legal pages, so telling them apart would need a list either way.
_COUNTRY_PATHS = (
    "/deutschland",
    "/griechenland",
    "/kroatien",
    "/oesterreich",
    "/schweiz",
)
# Cam detail links are /<country>/<city>/<8-char-id>. Anchored on href=" so the
# thumbnail CDN paths (/vpu/<hash>/<id>_180.jpg) can't false-match.
_CAM_LINK = re.compile(r'href="(/[a-z-]+/[a-z0-9-]+/([a-z0-9]{8}))"')
# Per-cam player-API JSON: `source` is a stable tokenless HLS URL on
# cdn.livespotting.com (served by DirectHls), alongside `name`/`city`/`country`.
_API = "https://player.livespotting.com/v2/livesource/{cam_id}?type=hub"


class LivespottingSource:
    """livespotting.tv tourist cams (mostly DACH coast/resort towns): country pages
    list the cam ids, each id's player-API JSON carries a direct open HLS `source`.
    An offline/missing id just yields no JSON and drops. No category anywhere ->
    None; the German `name` gets country/city appended when it doesn't name them."""

    name: str = "livespotting"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        page_for: dict[str, str] = {}
        for html in thread_map(self._fetch.get, [_BASE + p for p in _COUNTRY_PATHS]):
            for path, cam_id in _CAM_LINK.findall(html or ""):
                page_for.setdefault(cam_id, _BASE + path)
        ids = sorted(page_for)
        raws = thread_map(self._fetch.get, [_API.format(cam_id=i) for i in ids])
        seen: set[str] = set()
        for cam_id, raw in zip(ids, raws):
            try:
                data = json.loads(raw or "")
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            url = str(data.get("source") or "")
            name = str(data.get("name") or "").strip()
            if ".m3u8" not in url or not name or url in seen:
                continue
            seen.add(url)
            yield Candidate(
                title=with_location_parts(
                    name,
                    [str(data.get("country") or ""), str(data.get("city") or "")],
                ),
                angle_key=None,
                category=None,  # no category anywhere -> "Other" (title fallback)
                source=self.name,
                source_page_url=page_for[cam_id],
                target_url=url,
                predisc_key=predisc_key(url),
            )
