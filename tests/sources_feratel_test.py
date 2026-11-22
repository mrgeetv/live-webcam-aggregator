from pathlib import Path

from webcam_aggregator.sources.feratel import FeratelSource

_FIX = Path(__file__).parent / "fixtures"

_SITEMAP = "https://www.feratel.com/sitemap-0.xml"

_GSCHNITZ = "https://www.feratel.com/en/webcams/austria/tyrol/gschnitz-zentrum-5751"
_LENK = "https://www.feratel.com/en/webcams/switzerland/bern/lenk-im-simmental-metschstand-4230"
# three segments but no trailing cam id: not a cam page, so never fetched
_NO_ID = "https://www.feratel.com/en/webcams/austria/tyrol/gschnitz-zentrum"

# only the three-segment /en/webcams/<country>/<region>/<slug>-<id> paths are cam
# pages; the shorter ones are country/region indexes
_SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://www.feratel.com/en/webcams/austria</loc></url>
<url><loc>https://www.feratel.com/en/webcams/austria/tyrol</loc></url>
<url><loc>{_GSCHNITZ}</loc></url>
<url><loc>{_LENK}</loc></url>
<url><loc>{_NO_ID}</loc></url>
<url><loc>https://www.feratel.com/en/products/panorama</loc></url>
</urlset>"""

# a page whose <title> doesn't parse: the name falls back to the URL slug
_LENK_HTML = "<title>feratel.com</title>"

_PAGES = {
    _SITEMAP: _SITEMAP_XML,
    _GSCHNITZ: (_FIX / "feratel_cam.html").read_text(),
    _LENK: _LENK_HTML,
    _NO_ID: (_FIX / "feratel_cam.html").read_text(),
}


class _FakeFetch:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        self.fetched.append(url)
        return _PAGES.get(url)


def test_feratel_takes_own_cam_id_from_url_not_carousel_siblings():
    fetch = _FakeFetch()
    cands = {c.source_page_url: c for c in FeratelSource(fetch).discover()}

    # one candidate per cam page, and an id-less path isn't even fetched; gschnitz
    # yields its own cam (5751, the id its URL ends in), never the carousel
    # sibling (5744) that precedes the player
    assert set(cands) == {_GSCHNITZ, _LENK}
    assert _NO_ID not in fetch.fetched
    c = cands[_GSCHNITZ]
    # canonical player-page target, routed to the metatag extractor
    assert c.target_url == "https://webtv.feratel.com/webtv/?cam=5751"
    # keyed on the cam id so third-party webtvfc embeds of the same cam merge
    assert c.predisc_key == "feratel:5751"
    # <title> name + the URL's country/region (slug place already in the name)
    assert c.title == "Gschnitz Zentrum — Tyrol, Austria"
    assert c.category == "Travel & Events"  # place views, blanket
    assert c.source == "feratel"


def test_feratel_slug_title_fallback_drops_the_cam_id():
    c = next(
        c for c in FeratelSource(_FakeFetch()).discover() if c.source_page_url == _LENK
    )

    assert c.target_url == "https://webtv.feratel.com/webtv/?cam=4230"
    assert c.title == "Lenk Im Simmental Metschstand — Bern, Switzerland"


def test_feratel_handles_missing_sitemap():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(FeratelSource(_Dead()).discover()) == []
