from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import replace
from html import unescape

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import extract_candidates, with_location_parts

# Windy's webcams platform hosts no video itself, but a live cam's stream page
# (webcams.windy.com/webcams/stream/<id>) is a tiny static HTML wrapper around the
# ORIGINAL provider's embed — one iframe, entity-encoded. That page is both the
# live-probe (a non-live cam answers 200 with a ~170-byte "not available" body) and
# the extraction fetch, so per-cam cost is one small request. The keyless internal
# list API (node.windy.com) is the only enumeration: nearby=<lat>,<lon> (radius max
# 250 km, limit max 25, offset paginates; verified past offset=3000) returns
# id/title/location per cam — but NO live flag, which is why liveness is per-id via
# the stream page.
_LIST = (
    "https://node.windy.com/webcams/v2.0/list"
    "?nearby={lat},{lon}&radius=250&limit=25&offset={off}"
)
_STREAM = "https://webcams.windy.com/webcams/stream/{id}"
_IFRAME = re.compile(r'<iframe[^>]+src="([^"]+)"', re.I)

# The corpus is ~65k cams worldwide and only ~5-10% have live video, so one build
# can neither enumerate nor probe everything politely. Instead the instance keeps
# state across rebuilds (sources live for the process lifetime) and spends a fixed
# request budget per build: known-live cams are re-probed first (they are the
# catalogue's current content), the rest of the corpus is probed in rotating
# slices. Coverage therefore RAMPS over the first ~2 days of builds — and resets
# to ramp again after a restart. That is expected, not an outage: the per-source
# counts grow, they don't collapse.
_BUDGET = 9000
# a dense-region circle holds ~4.6k cams; past this cap the overlapping neighbour
# circles cover the remainder, so deeper paging is waste
_CIRCLE_CAP = 4500
_PAGE = 25
# 250 km-radius circles at ~350 km (~3.15°) spacing overlap enough for coverage;
# latitudes outside the band hold no cams worth a probe row
_STEP = 3.15
_LAT_MIN, _LAT_MAX = -60.0, 72.0
# re-run the grid enumeration every N builds to pick up newly added cams
_ENUM_EVERY = 40
# a stream page smaller than this is the "not available" stub, not an embed
_MIN_STREAM_PAGE = 300


def _grid() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    lat = _LAT_MIN
    while lat <= _LAT_MAX:
        step = _STEP / max(0.2, math.cos(math.radians(lat)))
        lon = -180.0
        while lon < 180.0:
            pts.append((round(lat, 2), round(lon, 2)))
            lon += step
        lat += _STEP
    return pts


def _cams_of(raw: str | None) -> tuple[int, list[dict[str, object]]]:
    """(total, cams) from a list-API response; (0, []) on any malformed body."""
    try:
        doc = json.loads(raw or "")
    except ValueError:
        return 0, []
    if not isinstance(doc, dict):
        return 0, []
    cams = doc.get("cams")
    total = doc.get("total")
    return (
        total if isinstance(total, int) else 0,
        [c for c in cams if isinstance(c, dict)] if isinstance(cams, list) else [],
    )


class WindySource:
    """windy.com webcams via the keyless internal API + per-cam stream pages.

    A meta-aggregator like camscape: the emitted candidates are the ORIGINAL
    providers' embeds (feratel, direct DOT m3u8s, YouTube, …), so the shared
    predisc keys make dedup against the first-party sources do real work."""

    name: str = "windy"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch
        # id -> (title, city, country); persists across rebuilds
        self._corpus: dict[int, tuple[str, str, str]] = {}
        self._live: set[int] = set()
        self._cursor: int = 0
        self._builds_until_enum: int = 0

    def _remember(self, cams: list[dict[str, object]]) -> None:
        for c in cams:
            cid = c.get("id")
            if not isinstance(cid, int):
                continue
            loc = c.get("location")
            loc = loc if isinstance(loc, dict) else {}
            self._corpus[cid] = (
                str(c.get("title") or ""),
                str(loc.get("city") or ""),
                str(loc.get("country") or ""),
            )

    def _enumerate(self) -> int:
        """Grid-scan the list API into the corpus; returns requests spent."""
        points = _grid()
        first = [_LIST.format(lat=lat, lon=lon, off=0) for lat, lon in points]
        extras: list[str] = []
        for (lat, lon), raw in zip(points, thread_map(self._fetch.get, first)):
            total, cams = _cams_of(raw)
            self._remember(cams)
            for off in range(_PAGE, min(total, _CIRCLE_CAP), _PAGE):
                extras.append(_LIST.format(lat=lat, lon=lon, off=off))
        for raw in thread_map(self._fetch.get, extras):
            _, cams = _cams_of(raw)
            self._remember(cams)
        self._builds_until_enum = _ENUM_EVERY
        return len(first) + len(extras)

    def discover(self) -> Iterator[Candidate]:
        budget = _BUDGET
        if not self._corpus or self._builds_until_enum <= 0:
            budget -= self._enumerate()
        self._builds_until_enum -= 1
        if budget <= 0 or not self._corpus:
            return
        # known-live first (the current catalogue content), then a rotating slice
        # of everything else so the whole corpus gets re-checked over ~a week
        rest = [i for i in sorted(self._corpus) if i not in self._live]
        slice_n = max(0, budget - len(self._live))
        start = self._cursor % len(rest) if rest else 0
        rotation = (rest + rest)[start : start + slice_n]
        self._cursor = (start + len(rotation)) % len(rest) if rest else 0
        ids = sorted(self._live) + rotation
        urls = [_STREAM.format(id=i) for i in ids]
        for cid, page in zip(ids, thread_map(self._fetch.get, urls)):
            if not page or len(page) < _MIN_STREAM_PAGE or not _IFRAME.search(page):
                self._live.discard(cid)
                continue
            self._live.add(cid)
            title, city, country = self._corpus.get(cid, ("", "", ""))
            # the iframe src is HTML-entity-encoded; unescape before the ladder
            for c in extract_candidates(
                unescape(page), page_url=_STREAM.format(id=cid), source=self.name
            ):
                yield replace(c, title=with_location_parts(title, [country, city]))
