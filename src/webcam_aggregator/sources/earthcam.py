from __future__ import annotations

import json
import re
from collections.abc import Iterator
from urllib.parse import urlsplit

from ..fetch import FetcherProtocol
from ..models import Candidate
from .base import predisc_key, with_location_parts

_API = "https://www.earthcam.com/api/mapsearch"
# Network = EarthCam's OWN cams (earthcam.com/world/ pages our extractor resolves).
_NETWORK = f"{_API}/get_locations_network.php?r=ecn&a=fetch"
# Global bbox = the whole map incl partner cams (YouTube / balticlivecam / ipcamlive /
# direct HLS, plus ~2400 one-off sites we can't serve). Whole world, low zoom.
_GLOBAL = (
    f"{_API}/get_locations.php?nwx=85&nwy=-180&nex=85&ney=180"
    "&sex=-85&sey=180&swx=-85&swy=-180&zoom=3"
)

_YT = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|live/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def _routable(url: str) -> tuple[str, str | None] | None:
    """EarthCam's map is a meta-aggregator across thousands of sites; keep only URLs that
    route to an extractor we already have. Returns (target_url, predisc_key) or None to
    drop. EarthCam-own geographic cam pages (`/usa/`, `/world/`) resolve; its `/clients/`
    + `/top25/` landing pages and `myearthcam.com` roots don't, so they fall through."""
    m = _YT.search(url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}", f"yt:{m.group(1)}"
    host = (urlsplit(url).hostname or "").lower()
    if host in ("www.earthcam.com", "earthcam.com"):
        seg = urlsplit(url).path.lstrip("/").split("/", 1)[0]
        return (url, None) if seg in ("usa", "world") else None
    if host == "balticlivecam.com" or host.endswith(".balticlivecam.com"):
        return url, None
    if ".m3u8" in url:  # direct HLS, incl ipcamlive stream.m3u8
        return url, predisc_key(url)
    return None


def _places(raw: str | None) -> list[dict[str, object]]:
    if not raw:
        return []
    try:
        doc = json.loads(raw)
    except ValueError:
        return []
    # get_locations_network wraps the list in {"data": [...]}; get_locations is a bare list.
    groups = doc["data"] if isinstance(doc, dict) and "data" in doc else doc
    out: list[dict[str, object]] = []
    if isinstance(groups, list):
        for g in groups:
            ps = g.get("places") if isinstance(g, dict) else None
            if isinstance(ps, list):
                out += [p for p in ps if isinstance(p, dict)]
    return out


class EarthCamSource:
    """EarthCam's mapsearch JSON API as a source. Most of its ~4000 mapped cams are one-off
    external sites we can't serve, so `_routable` keeps only the ones that hit an existing
    extractor: EarthCam's own `/world/` cams, plus partner YouTube / balticlivecam /
    ipcamlive / direct-HLS streams. No category in the feed -> all land in "Other"."""

    name: str = "earthcam"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        seen: set[str] = set()
        for endpoint in (_NETWORK, _GLOBAL):
            for p in _places(self._fetch.get(endpoint)):
                routed = _routable(str(p.get("url") or ""))
                if routed is None:
                    continue
                target, key = routed
                dedup = (
                    key or target
                )  # de-dupe within EarthCam (same cam in both feeds)
                if dedup in seen:
                    continue
                seen.add(dedup)
                geo = [str(p[k]) for k in ("country", "state", "city") if p.get(k)]
                yield Candidate(
                    title=with_location_parts(str(p.get("name") or ""), geo),
                    angle_key=None,
                    category=None,  # the feed carries no content category -> "Other"
                    source=self.name,
                    source_page_url=str(p.get("url") or ""),
                    target_url=target,
                    predisc_key=key,
                )
