from pathlib import Path

from webcam_aggregator.sources.beachcam import BeachcamSource

_FIX = Path(__file__).parent / "fixtures"

_INDEX = "https://beachcam.meo.pt/livecams/"

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
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        self.urls.append(url)
        return _PAGES.get(url)


def test_beachcam_targets_are_bare_playlists_keyed_on_themselves() -> None:
    fetch = _FakeFetch()
    cands = list(BeachcamSource(fetch).discover())
    by_title = {c.title: c for c in cands}

    # only pages with a data-video-url stream; the footer's @Beachcamportugal
    # YouTube link never becomes a candidate, and the duplicate index link collapses
    assert set(by_title) == {
        "Carcavelos | Calhau — Cascais, Portugal",
        "Alvor — Portimão, Portugal",  # entity region unescaped, h1 space stripped
    }

    carc = by_title["Carcavelos | Calhau — Cascais, Portugal"]
    bare = "https://video-auth1.iol.pt/auth-beachcam/carcavelos2/playlist.m3u8"
    # no token: the beachcam extractor mints one at resolve time, so a held target
    # never carries a lapsed one
    assert carc.target_url == bare
    assert carc.predisc_key == f"hls:{bare}"
    assert not any("iol.pt" in u for u in fetch.urls)

    for c in cands:
        assert c.source == "beachcam"
        # blanket category: ~97% right beats ~190 Portuguese titles in "Other"
        assert c.category == "Beaches"
        assert c.angle_key is None  # one stream per page


def test_beachcam_handles_missing_index() -> None:
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(BeachcamSource(_Dead()).discover()) == []
