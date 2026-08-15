import logging

import pytest

from webcam_aggregator.sources.viewsurf import ViewsurfSource

_SITEMAP = "https://www.viewsurf.com/sitemap.xml"

# The same cam repeats once per univers (654 under plage AND surf); 999 sits only in
# a univers we have no mapping for; the liste/homepage URLs carry no /vue/ and are
# ignored.
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://viewsurf.com/univers/plage/vue/654-france-aquitaine-lacanau-sud-du-poste-de-secours-nord</loc></url>
<url><loc>https://viewsurf.com/univers/surf/vue/654-france-aquitaine-lacanau-sud-du-poste-de-secours-nord</loc></url>
<url><loc>https://viewsurf.com/univers/ville/vue/18654-france-ile-de-france-paris-port-de-plaisance-de-paris-arsenal</loc></url>
<url><loc>https://viewsurf.com/univers/montagne/vue/17026-france-midi-pyrenees-hautacam-panoramique-hd-tramassel</loc></url>
<url><loc>https://viewsurf.com/univers/randonnee/vue/999-suisse-valais-zermatt-sentier</loc></url>
<url><loc>https://viewsurf.com/univers/surf/liste</loc></url>
<url><loc>https://viewsurf.com/</loc></url>
</urlset>"""


def _page(h1: str, uuid: str | None, kind: str = "vod") -> str:
    embed = (
        '<iframe \n scrolling="no" src="https://platforms5.joada.net/embeded/'
        f"embeded.html?uuid={uuid}&type={kind}&liveicon=0&vsheader=1"
        '&tz=Europe/Paris&tsp=1786788008&titletext=">\n</iframe>'
        if uuid
        else ""
    )
    return f"<h1>{h1}</h1>{embed}"


_PAGES = {
    _SITEMAP: _SITEMAP_XML,
    # vod cam, h1 splits city/name across a <small>, listed under plage AND surf
    "https://viewsurf.com/univers/plage/vue/654-france-aquitaine-lacanau-sud-du-poste-de-secours-nord": _page(
        "Lacanau <small>Sud du poste de secours nord</small>",
        "d269f1ee-c5fb-41e5-3730-3330-6d61-63-97f8-6bd3c03f2099d",
    ),
    # live cam with an HTML entity in the h1
    "https://viewsurf.com/univers/ville/vue/18654-france-ile-de-france-paris-port-de-plaisance-de-paris-arsenal": _page(
        "Paris <small>Port de plaisance de Paris-Arsenal&#39;s</small>",
        "e6b6f1cd-ae5d-40f2-3032-3630-6d61-63-a744-dbccb109af26d",
        "live",
    ),
    # a panoramic-photo cam: page has no joada embed at all -> yields nothing
    "https://viewsurf.com/univers/montagne/vue/17026-france-midi-pyrenees-hautacam-panoramique-hd-tramassel": _page(
        "Hautacam <small>Panoramique HD</small>", None
    ),
    # unmapped univers slug -> passed through raw (lands in "Unmapped Category")
    "https://viewsurf.com/univers/randonnee/vue/999-suisse-valais-zermatt-sentier": _page(
        "Zermatt <small>Sentier</small>", "aaaa1111-2222-3333-4444-555566667777d"
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_viewsurf_discover_dedups_titles_and_categories() -> None:
    cands = list(ViewsurfSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # 654 collapses to ONE cam despite plage+surf listings; the photo-only page drops
    assert set(by_title) == {
        "Lacanau Sud du poste de secours nord — France",
        "Paris Port de plaisance de Paris-Arsenal's — France",
        "Zermatt Sentier — Suisse",
    }

    lacanau = by_title["Lacanau Sud du poste de secours nord — France"]
    # the target is the canonical embed URL: uuid+type only, presentation params gone
    assert lacanau.target_url == (
        "https://platforms5.joada.net/embeded/embeded.html"
        "?uuid=d269f1ee-c5fb-41e5-3730-3330-6d61-63-97f8-6bd3c03f2099d&type=vod"
    )
    assert lacanau.category == "Beaches"

    paris = by_title["Paris Port de plaisance de Paris-Arsenal's — France"]
    assert paris.target_url.endswith("&type=live")
    assert paris.category == "Cities"

    # the unmapped univers slug passes through raw -> "Unmapped Category" at build
    assert by_title["Zermatt Sentier — Suisse"].category == "randonnee"

    for c in cands:
        assert c.source == "viewsurf"
        assert c.angle_key is None
        assert c.predisc_key is None  # joada embeds appear nowhere else


def test_viewsurf_logs_unmapped_univers_slugs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.viewsurf"):
        list(ViewsurfSource(_FakeFetch()).discover())
    assert any(
        "randonnee" in r.getMessage() and "Unmapped Category" in r.getMessage()
        for r in caplog.records
    )


def test_viewsurf_empty_sitemap_yields_nothing() -> None:
    class _Empty:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(ViewsurfSource(_Empty()).discover()) == []
