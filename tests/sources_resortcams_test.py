from pathlib import Path

from webcam_aggregator.sources.resortcams import ResortCamsSource

_FIX = Path(__file__).parent / "fixtures"

_SITEMAP = "https://www.resortcams.com/webcams-sitemap1.xml"

# the source enumerates cam pages from the webcams post-type sitemap; the bare
# /webcams/ index and non-webcam URLs never match
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://www.resortcams.com/webcams/</loc></url>
<url><loc>https://www.resortcams.com/webcams/blackfin-resort-marina-florida-keys/</loc></url>
<url><loc>https://www.resortcams.com/webcams/greeneville-tn/</loc></url>
<url><loc>https://www.resortcams.com/webcams/sugar-ski/</loc></url>
<url><loc>https://www.resortcams.com/beach-cams/</loc></url>
</urlset>"""

_GREENEVILLE_HTML = """
<title>Greeneville, TN - Resort Cams</title>
<div id="player" data-src="https://stream.resortcams.com/live/greeneville.stream/playlist.m3u8"></div>
"""

_PAGES = {
    _SITEMAP: _SITEMAP_XML,
    "https://www.resortcams.com/webcams/blackfin-resort-marina-florida-keys/": (
        _FIX / "resortcams_cam.html"
    ).read_text(),
    "https://www.resortcams.com/webcams/greeneville-tn/": _GREENEVILLE_HTML,
    # no player in the static HTML (JS-built cam) -> yields nothing, drops
    "https://www.resortcams.com/webcams/sugar-ski/": (
        "<title>Sugar Ski &amp; Country Club Sugar Mountain Web Cam - Resort Cams</title>"
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_resortcams_discovers_direct_hls_with_clean_titles():
    cands = list(ResortCamsSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # boilerplate tail stripped, entities unescaped; the playerless page drops
    assert set(by_title) == {
        "Blackfin Resort & Marina - Florida Keys",
        "Greeneville, TN",
    }

    assert (
        by_title["Greeneville, TN"].target_url
        == "https://stream.resortcams.com/live/greeneville.stream/playlist.m3u8"
    )
    # nav links to other cam pages never become candidates
    assert all("app-ski-mtn" not in c.target_url for c in cands)

    for c in cands:
        assert c.source == "resortcams"
        assert c.category is None  # no per-cam type on the site -> title fallback
        assert c.angle_key is None  # one stream per page
        assert (c.predisc_key or "").startswith("hls:")


def test_resortcams_handles_missing_sitemap():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(ResortCamsSource(_Dead()).discover()) == []
