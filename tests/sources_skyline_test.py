import logging
from pathlib import Path

import pytest

from webcam_aggregator.sources.skyline import SkylineSource

_BASE = "https://www.skylinewebcams.com"
_SITEMAP = f"{_BASE}/sitemap_en.xml"
_FIX = Path(__file__).parent / "fixtures"
# Real cam pages, trimmed: an own-HLS (Clappr) cam, a "from the web" YouTube cam, and
# an offline cam whose JSON-LD still names a YouTube video but which has no player.
_OWN_PAGE = (_FIX / "skyline_cam.html").read_text()
_YT_PAGE = (_FIX / "skyline_cam_youtube.html").read_text()
_OFFLINE_PAGE = (_FIX / "skyline_cam_offline.html").read_text()


class _FakeFetch:
    _pages: dict[str, str]
    requested: list[str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.requested = []

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        self.requested.append(url)
        return self._pages.get(url)


def _sitemap(*paths: str) -> str:
    locs = "".join(f"<url><loc>{_BASE}{p}</loc></url>" for p in paths)
    return f'<?xml version="1.0"?><urlset>{locs}</urlset>'


# Everything is exercised through the public discover(); the per-page hooks are internal.
def test_skyline_discover_enumerates_cams_from_the_sitemap():
    own = "/en/webcam/italia/lazio/roma/roma-colosseo.html"
    yt = "/en/webcam/united-states/florida/jacksonville/bald-eagle-s-nest.html"
    off = "/en/webcam/italia/abruzzo/chieti/roccacaramanico.html"
    # The sitemap also lists geo index pages, per-cam timelapses, weather pages and
    # site pages; only the four-level /en/webcam/ paths are cams.
    not_cams = (
        "/en/webcam.html",
        "/en/webcam/italia.html",
        "/en/webcam/italia/lazio.html",
        "/en/webcam/italia/lazio/roma.html",
        "/en/webcam/italia/lazio/roma/roma-colosseo/timelapse.html",
        "/en/weather/italia/lazio/roma.html",
        "/en/premium.html",
    )
    fetch = _FakeFetch(
        {
            _SITEMAP: _sitemap(own, yt, off, *not_cams),
            f"{_BASE}{own}": _OWN_PAGE,
            f"{_BASE}{yt}": _YT_PAGE,
            f"{_BASE}{off}": _OFFLINE_PAGE,
        }
    )
    by_page = {c.source_page_url: c for c in SkylineSource(fetch).discover()}

    assert set(by_page) == {f"{_BASE}{own}", f"{_BASE}{yt}"}  # offline -> dropped
    assert not {f"{_BASE}{p}" for p in not_cams} & set(fetch.requested)

    o = by_page[f"{_BASE}{own}"]
    assert o.target_url == f"{_BASE}{own}"  # the PAGE — SkylineResolver resolves it
    assert o.predisc_key is None
    assert o.category is None  # listed on no category page
    assert o.source == "skyline"
    # breadcrumb (English) geo, " Live cam" stripped, "Live Cams" root left out
    assert o.title == "Rome - Colosseum — Lazio, Italy"  # "Rome" is already named

    y = by_page[f"{_BASE}{yt}"]
    assert y.target_url == "https://www.youtube.com/watch?v=UANvTm4J18M"
    assert y.predisc_key == "yt:UANvTm4J18M"  # dedups against youtube-api
    assert y.title == "Bald Eagle's Nest — Jacksonville, Florida, United States"


def test_skyline_categories_come_from_the_category_pages(
    caplog: pytest.LogCaptureFixture,
):
    beach = "/en/webcam/espana/canarias/santa-cruz-de-tenerife/playa-las-vistas.html"
    drone = "/en/webcam/italia/lazio/roma/drone-cam.html"
    fetch = _FakeFetch(
        {
            # the index lists a known category (beach) AND one we don't map (drones)
            f"{_BASE}/en/categories.html": (
                '<a href="/en/live-cams-category/beach-cams.html">Beach</a>'
                '<a href="/en/live-cams-category/drones-cams.html">Drones</a>'
            ),
            f"{_BASE}/en/live-cams-category/beach-cams.html": (
                f'<a class="cam-title" href="{beach}">Playa Las Vistas</a>'
            ),
            f"{_BASE}/en/live-cams-category/drones-cams.html": (
                f'<a class="cam-title" href="{drone}">Drone</a>'
            ),
            _SITEMAP: _sitemap(beach, drone),
            f"{_BASE}{beach}": _OWN_PAGE,
            f"{_BASE}{drone}": _OWN_PAGE,
        }
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.skyline"):
        by_page = {c.source_page_url: c for c in SkylineSource(fetch).discover()}

    assert by_page[f"{_BASE}{beach}"].category == "Beaches"
    # the raw slug passes through -> map_category -> "Unmapped Category"
    assert by_page[f"{_BASE}{drone}"].category == "drones"
    assert any("drones" in r.getMessage() for r in caplog.records)
