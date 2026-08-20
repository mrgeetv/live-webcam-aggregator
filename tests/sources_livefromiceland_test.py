from pathlib import Path

from webcam_aggregator.sources.livefromiceland import LiveFromIcelandSource

_FIX = Path(__file__).parent / "fixtures"

# double-dash title: only the trailing site boilerplate is stripped
_HEIMAKLETTUR_HTML = """
<title>Vestmannaeyjar &#8211; Heimaklettur &#8211; Live From Iceland</title>
<iframe data-lazyloaded="1" src="about:blank" data-litespeed-src="https://g0.ipcamlive.com/player/player.php?alias=heimaklettur" width="100%" height="100%"></iframe>
"""

_PAGES = {
    "https://livefromiceland.is/wp-json/wp/v2/webcam?per_page=100": (
        _FIX / "livefromiceland_index.json"
    ).read_text(),
    "https://livefromiceland.is/webcam/hekla/": (
        _FIX / "livefromiceland_cam.html"
    ).read_text(),
    "https://livefromiceland.is/webcam/vestmannaeyjar-heimaklettur/": _HEIMAKLETTUR_HTML,
    # no player in the static HTML -> yields nothing, drops
    "https://livefromiceland.is/webcam/reykjavikurtjorn/": "<title>Reykjavíkurtjörn</title>",
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_livefromiceland_extracts_lazy_ipcamlive_players():
    cands = list(LiveFromIcelandSource(_FakeFetch()).discover())
    by_target = {c.target_url: c for c in cands}

    # the lazy-loaded data-litespeed-src URL, never the src="about:blank" decoy
    assert set(by_target) == {
        "https://g0.ipcamlive.com/player/player.php?alias=684daa021140c",
        "https://g0.ipcamlive.com/player/player.php?alias=heimaklettur",
    }

    # titles: boilerplate tail stripped, "Iceland" appended
    titles = {c.title for c in cands}
    assert titles == {"Hekla — Iceland", "Vestmannaeyjar – Heimaklettur — Iceland"}

    for c in cands:
        assert c.source == "livefromiceland"
        assert c.category is None  # site has no per-cam category
        assert c.predisc_key is None  # ipcamlive alias: nothing to merge on
        assert c.angle_key is None  # one player per page

    # a non-cam entry in the collection is never crawled as a cam
    assert not any("about" in c.source_page_url for c in cands)


def test_livefromiceland_handles_missing_index():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(LiveFromIcelandSource(_Dead()).discover()) == []


def test_livefromiceland_handles_a_non_json_index():
    """A retired endpoint answers with an HTML error page, not JSON. That must
    drop to zero cams (the empty-guard then reports it), never crash the source."""

    class _ErrorPage:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return "<!DOCTYPE html><html><body>404 Not Found</body></html>"

    assert list(LiveFromIcelandSource(_ErrorPage()).discover()) == []


def test_livefromiceland_handles_a_json_error_object():
    """WP answers a disabled route with a JSON *object*, not the expected array."""

    class _RestError:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return '{"code":"rest_no_route","message":"No route was found."}'

    assert list(LiveFromIcelandSource(_RestError()).discover()) == []
