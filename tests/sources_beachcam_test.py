from pathlib import Path

from webcam_aggregator.sources.beachcam import BeachcamSource

_FIX = Path(__file__).parent / "fixtures"

_INDEX = "https://beachcam.meo.pt/livecams/"
_TOKEN_API = "https://services.iol.pt/matrix?userId="
# same base64 shape as the real IOL auth token, deliberately not a real one
_TOK = "dGVzdC1vbmx5LXdtc0F1dGhTaWduLXRva2VuLXZhbHVl=="

# region label carries an HTML entity ("Portimão") + h1 with a trailing space
_ALVOR_HTML = """
<title>Beachcam</title>
<small class="liveCamsHeader__label">LIVECAMS / Portim&#xE3;o</small>
<h1 class="liveCamsHeader__title">Alvor </h1>
<div data-control="livecam"
     data-video-url="https://video-auth1.iol.pt/auth-beachcam/alvor/playlist.m3u8">
</div>
"""

_PAGES = {
    _INDEX: (_FIX / "beachcam_index.html").read_text(),
    _TOKEN_API: _TOK,
    "https://beachcam.meo.pt/livecams/carcavelos-calhau/": (
        _FIX / "beachcam_cam.html"
    ).read_text(),
    "https://beachcam.meo.pt/livecams/alvor/": _ALVOR_HTML,
    # no data-video-url in the static HTML -> yields nothing, drops
    "https://beachcam.meo.pt/livecams/sem-player/": (
        '<h1 class="liveCamsHeader__title">Sem Player</h1>'
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_beachcam_appends_auth_token_and_keys_on_bare_url():
    cands = list(BeachcamSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # only pages with a data-video-url stream; the footer's @Beachcamportugal
    # YouTube link never becomes a candidate, and the duplicate index link collapses
    assert set(by_title) == {
        "Carcavelos | Calhau — Cascais, Portugal",
        "Alvor — Portimão, Portugal",  # entity region unescaped, h1 space stripped
    }

    carc = by_title["Carcavelos | Calhau — Cascais, Portugal"]
    bare = "https://video-auth1.iol.pt/auth-beachcam/carcavelos2/playlist.m3u8"
    # the playable URL carries the per-build auth token...
    assert carc.target_url == f"{bare}?wmsAuthSign={_TOK}"
    # ...but the merge key stays on the bare URL (the token changes every build)
    assert carc.predisc_key == f"hls:{bare}"

    for c in cands:
        assert c.source == "beachcam"
        assert c.category is None  # beach site with lake/city/skatepark outliers
        assert c.angle_key is None  # one stream per page


def test_beachcam_token_failure_ships_bare_urls():
    # an error page is not a token: the bare URL still ships, so liveness sees the
    # cams die (dead-manifest) instead of the whole source vanishing silently
    pages = dict(_PAGES, **{_TOKEN_API: "<html>Forbidden</html>"})

    class _NoToken:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return pages.get(url)

    cands = list(BeachcamSource(_NoToken()).discover())
    assert cands
    assert all("wmsAuthSign" not in c.target_url for c in cands)


def test_beachcam_handles_missing_index():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(BeachcamSource(_Dead()).discover()) == []
