from __future__ import annotations

import json
from typing import TYPE_CHECKING

from webcam_aggregator.sources.cxtvlive import CxtvliveSource

if TYPE_CHECKING:
    from webcam_aggregator.fetch import FetcherProtocol

_MAP_URL = "https://www.cxtvlive.com/data/cxl_mapa.php"
_SITEMAP_URL = "https://www.cxtvlive.com/sitemap.php"
_CAM = "https://www.cxtvlive.com/live-camera/"


def _map(*entries: list[object]) -> str:
    # Map feed shape, in order: lat, lon, name, slug, thumb, "Country - Region - City",
    # flag, cats
    return json.dumps({"n": len(entries), "p": list(entries)})


class _FakeFetcher:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0, /) -> str | None:
        return self._pages.get(url)


def test_category_and_geo_come_from_the_map_feed() -> None:
    # The cam page renders no category/geo; both come from the map feed, keyed by slug.
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [
                    -22.9,
                    -43.2,
                    "Copacabana",
                    "rio-copacabana",
                    "x.webp",
                    "Brazil - RJ - Rio de Janeiro",
                    "Brasil.jpg",
                    "Beaches · Cities",
                ]
            ),
            _SITEMAP_URL: "",
            _CAM + "rio-copacabana": (
                "<h1>Copacabana</h1>"
                '<div data-stream-url="https://cdn.example/live.m3u8"></div>'
            ),
        }
    )
    cands = list(CxtvliveSource(fetch=f).discover())
    assert len(cands) == 1
    c = cands[0]
    assert c.source == "cxtvlive"
    assert c.category == "Beaches"  # first content segment of "Beaches · Cities"
    assert c.predisc_key == "hls:https://cdn.example/live.m3u8"
    assert "Rio de Janeiro" in c.title
    assert "Brazil" in c.title


def test_high_definition_tag_is_skipped_for_the_real_category() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [
                    0,
                    0,
                    "Grand Central",
                    "grand-central",
                    "x.webp",
                    "United States - NY - New York",
                    "US.jpg",
                    "High Definition · Railway Stations",
                ]
            ),
            _SITEMAP_URL: "",
            _CAM + "grand-central": (
                "<h1>Grand Central</h1>"
                '<iframe src="https://www.youtube.com/embed/aaaaaaaaaaa"></iframe>'
            ),
        }
    )
    c = next(iter(CxtvliveSource(fetch=f).discover()))
    assert c.category == "Railway Stations"


def test_sitemap_adds_cams_absent_from_the_map() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [0, 0, "In Map", "in-map", "x.webp", "France", "FR.jpg", "Cities"]
            ),
            _SITEMAP_URL: (
                "<url><loc>https://www.cxtvlive.com/live-camera/in-map</loc></url>"
                "<url><loc>https://www.cxtvlive.com/live-camera/sitemap-only</loc></url>"
            ),
            _CAM
            + "in-map": '<h1>A</h1><iframe src="https://www.youtube.com/embed/aaaaaaaaaaa"></iframe>',
            _CAM
            + "sitemap-only": '<h1>B</h1><iframe src="https://www.youtube.com/embed/bbbbbbbbbbb"></iframe>',
        }
    )
    keys = {c.predisc_key for c in CxtvliveSource(fetch=f).discover()}
    assert keys == {"yt:aaaaaaaaaaa", "yt:bbbbbbbbbbb"}  # union, deduped by slug


def test_youtube_channel_chrome_link_is_dropped() -> None:
    # A YouTube cam page carries the operator's channel link beside the real embed; the
    # shared ladder would emit it as a bogus `…/live` candidate. One cam, one candidate.
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [0, 0, "Sq", "sq", "x.webp", "United States", "US.jpg", "Cities"]
            ),
            _SITEMAP_URL: "",
            _CAM + "sq": (
                "<h1>Square</h1>"
                '<iframe src="https://www.youtube.com/embed/aaaaaaaaaaa"></iframe>'
                '<a href="https://www.youtube.com/@SomeChannel">Our channel</a>'
            ),
        }
    )
    cands = list(CxtvliveSource(fetch=f).discover())
    assert [c.predisc_key for c in cands] == ["yt:aaaaaaaaaaa"]


def test_channel_link_kept_when_it_is_the_only_signal() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [0, 0, "Ch", "ch", "x.webp", "United States", "US.jpg", "Cities"]
            ),
            _SITEMAP_URL: "",
            _CAM + "ch": (
                "<h1>Channel cam</h1>"
                '<a href="https://www.youtube.com/@SoleChannel">Watch live</a>'
            ),
        }
    )
    cands = list(CxtvliveSource(fetch=f).discover())
    assert len(cands) == 1
    assert cands[0].target_url == "https://www.youtube.com/@SoleChannel/live"


def test_page_with_no_streamable_media_is_dropped() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            _MAP_URL: _map(
                [0, 0, "Dead", "dead", "x.webp", "Nowhere", "x.jpg", "Cities"]
            ),
            _SITEMAP_URL: "",
            _CAM + "dead": "<h1>Dead</h1> no streamable media here",
        }
    )
    assert list(CxtvliveSource(fetch=f).discover()) == []
