from pathlib import Path

from webcam_aggregator.sources.feratel import FeratelSource

_FIX = Path(__file__).parent / "fixtures"

_SITEMAP = "https://www.feratel.com/sitemap-0.xml"

# only the three-segment /en/webcams/<country>/<region>/<slug> paths are cam
# pages; the shorter ones are country/region indexes
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://www.feratel.com/en/webcams/austria</loc></url>
<url><loc>https://www.feratel.com/en/webcams/austria/tyrol</loc></url>
<url><loc>https://www.feratel.com/en/webcams/austria/tyrol/gschnitz-zentrum</loc></url>
<url><loc>https://www.feratel.com/en/webcams/switzerland/grisons/lenk-metschstand</loc></url>
<url><loc>https://www.feratel.com/en/products/panorama</loc></url>
</urlset>"""

# name already carries the slug's place; no data-src player -> still-image page
_LENK_HTML = """
<title>Webcams in Lenk im Simmental - Metschstand - Livecams in HD - feratel.com</title>
<img src="still.jpg">
"""

_PAGES = {
    _SITEMAP: _SITEMAP_XML,
    "https://www.feratel.com/en/webcams/austria/tyrol/gschnitz-zentrum": (
        _FIX / "feratel_cam.html"
    ).read_text(),
    "https://www.feratel.com/en/webcams/switzerland/grisons/lenk-metschstand": (
        _LENK_HTML
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_feratel_extracts_own_cam_not_carousel_siblings():
    cands = list(FeratelSource(_FakeFetch()).discover())

    # gschnitz yields its own cam (5751, the data-src player), never the
    # carousel sibling (5744, plain src=); the playerless lenk page drops
    assert len(cands) == 1
    c = cands[0]
    # canonical player-page target, routed to the metatag extractor
    assert c.target_url == "https://webtv.feratel.com/webtv/?cam=5751"
    # keyed on the cam id so third-party webtvfc embeds of the same cam merge
    assert c.predisc_key == "feratel:5751"
    # <title> name + the URL's country/region (slug place already in the name)
    assert c.title == "Gschnitz Zentrum — Tyrol, Austria"
    assert c.category == "Travel & Events"  # place views, blanket
    assert c.source == "feratel"


def test_feratel_handles_missing_sitemap():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(FeratelSource(_Dead()).discover()) == []
