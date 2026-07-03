import pytest

from webcam_aggregator.sources.skyline import SkylineSource

_BASE = "https://www.skylinewebcams.com"

_OWN_PAGE = (
    "<h1>Cortina d'Ampezzo - Cinque Torri Live cam</h1>"
    '<ol class="breadcrumb">'
    '<li><meta itemprop="name" content="Live Cams"></li>'
    '<li><a href="/en/webcam/italia.html"><span itemprop="name">Italy</span></a></li>'
    '<li><a href="/en/webcam/italia/veneto.html"><span itemprop="name">Veneto</span></a></li>'
    '<li><span itemprop="name">Belluno</span></li>'
    "</ol>"
    "<script>new Clappr.Player({source:'livee.m3u8?a=abc123def456ghi'});</script>"
)
_YT_PAGE = (
    "<h1>Nkorho - Africa Live cam</h1>"
    '<ol class="breadcrumb">'
    '<li><span itemprop="name">South Africa</span></li>'
    '<li><span itemprop="name">Limpopo</span></li>'
    '<li><span itemprop="name">Kruger National Park</span></li>'
    "</ol>"
    "<script>new YT.Player('live',{playerVars:{},videoId:'sQAhSkjRKGk'});</script>"
)


class _FakeFetch:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return self._pages.get(url)


# Everything is exercised through the public discover(); the per-page hooks are internal.
def test_skyline_discover_categorises_extracts_and_names():
    own = "en/webcam/italia/veneto/belluno/cortina.html"  # Skyline-own HLS, beach cat
    yt = "en/webcam/south-africa/limpopo/kruger/nkorho.html"  # "from the web" YouTube
    off = "en/webcam/x/y/z/dead.html"  # offline / no player
    pages = {
        f"{_BASE}/en/live-cams-category/beach-cams.html": (
            f'<a href="{own}"></a><a href="{yt}"></a><a href="{off}"></a>'
            '<a href="/en/webcam/italia.html">Italy</a>'
        ),
        f"{_BASE}/{own}": _OWN_PAGE,
        f"{_BASE}/{yt}": _YT_PAGE,
        f"{_BASE}/{off}": "<h1>404!</h1>",
    }
    by_page = {
        c.source_page_url: c for c in SkylineSource(_FakeFetch(pages)).discover()
    }

    assert f"{_BASE}/{off}" not in by_page  # no player -> dropped

    o = by_page[f"{_BASE}/{own}"]
    assert o.target_url == f"{_BASE}/{own}"  # the PAGE — SkylineResolver resolves it
    assert o.predisc_key is None
    assert o.category == "Beaches"  # from the beach category page
    assert o.source == "skyline"
    # breadcrumb (English) geo, " Live cam" stripped, category kept out of the suffix
    assert o.title == "Cortina d'Ampezzo - Cinque Torri — Belluno, Veneto, Italy"

    y = by_page[f"{_BASE}/{yt}"]
    assert y.target_url == "https://www.youtube.com/watch?v=sQAhSkjRKGk"
    assert y.predisc_key == "yt:sQAhSkjRKGk"  # dedups against youtube-api
    assert y.title == "Nkorho - Africa — Kruger National Park, Limpopo, South Africa"


def test_skyline_discover_follows_country_then_region_uncategorised():
    cam = "en/webcam/italia/lazio/roma/colosseo.html"  # only reachable via region page
    pages = {
        # category page: no cams here, just the nav country link (the seed)
        f"{_BASE}/en/live-cams-category/beach-cams.html": (
            '<a href="/en/webcam/italia.html">Italy</a>'
        ),
        f"{_BASE}/en/webcam/italia.html": (
            '<a href="/en/webcam/italia/lazio.html" class="btn btn-primary tag">Lazio</a>'
        ),
        f"{_BASE}/en/webcam/italia/lazio.html": f'<a href="{cam}"></a>',
        f"{_BASE}/{cam}": _OWN_PAGE,
    }
    cands = list(SkylineSource(_FakeFetch(pages)).discover())
    by_page = {c.source_page_url: c for c in cands}
    assert f"{_BASE}/{cam}" in by_page  # reached via country -> region BFS
    assert by_page[f"{_BASE}/{cam}"].category is None  # never categorised -> "Other"


def test_skyline_index_crawl_flags_unmapped_category(caplog: pytest.LogCaptureFixture):
    import logging

    # the index lists a known category (beach) AND a new one we don't map (drones);
    # the new category's cam carries the raw slug -> map_category -> "Unmapped Category".
    cam = "en/webcam/x/drone-cam.html"
    pages = {
        f"{_BASE}/en/live-cams.html": (
            '<a href="/en/live-cams-category/beach-cams.html">Beaches</a>'
            '<a href="/en/live-cams-category/drones-cams.html">Drones</a>'
        ),
        f"{_BASE}/en/live-cams-category/beach-cams.html": "",
        f"{_BASE}/en/live-cams-category/drones-cams.html": f'<a href="{cam}"></a>',
        f"{_BASE}/{cam}": _OWN_PAGE,
    }
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.skyline"):
        cands = list(SkylineSource(_FakeFetch(pages)).discover())
    by_page = {c.source_page_url: c for c in cands}
    assert by_page[f"{_BASE}/{cam}"].category == "drones"
    assert any("drones" in r.getMessage() for r in caplog.records)
