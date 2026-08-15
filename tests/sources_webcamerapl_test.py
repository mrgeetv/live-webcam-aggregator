import codecs
import json

from webcam_aggregator.sources.webcamerapl import WebcameraPlSource

_BASE = "https://www.webcamera.pl"


def _player(m3u8: str, h1: str | None = None) -> str:
    # the site ships video_src ROT13-obfuscated inside JSON (slashes escaped)
    src = codecs.encode(m3u8, "rot13").replace("/", "\\/")
    head = f'<h1 id="cam-description-toggler">{h1}</h1>' if h1 is not None else ""
    return f'{head}<script>var STREAM_PLAYER_CONFIG = {{"video_src":"{src}","autoplay":true}};</script>'


_MARKERS = {
    "hel": {
        "url": "https://hel.webcamera.pl/",
        "name": "Hel - widok na bulwar i plażę",
        "is_inactive": False,
    },
    "krakow1": {
        "url": f"{_BASE}/kamera/krakow1/",
        "name": "KRAKÓW - Rynek Główny NOWOŚĆ",
        "is_inactive": False,
    },
    # inactive -> skipped even though a category listing re-lists it
    "jaroslawiec": {
        "url": "https://jaroslawiec.webcamera.pl/",
        "name": "Jarosławiec - widok na plażę",
        "is_inactive": True,
    },
    # rotating compilation feeds -> skipped (dup other cams / paywalled)
    "plaze-playlista": {
        "url": "https://50-plaz.webcamera.pl/",
        "name": "50 plaż [playlista]",
        "is_inactive": False,
    },
    "plaze-premium": {
        "url": "https://50-plaz-premium.webcamera.pl/",
        "name": "50 plaż w Polsce PREMIUM",
        "is_inactive": False,
    },
}

_MAPA = f"<html><script>var MAP_MARKERS = {json.dumps(_MARKERS)};</script></html>"


def _listing(*hrefs: str) -> str:
    links = "".join(f'<a class="cam" href="{h}"></a>' for h in hrefs)
    return (
        # nav link before the grid + trailing module: both must be ignored
        '<a href="https://ignored-nav.webcamera.pl/"></a>'
        f'<div class="listing" id="inline-camera-listing">{links}</div>'
        '<div class="searchbox-cams">'
        '<a href="https://ignored-recent.webcamera.pl/"></a></div>'
    )


_PAGES = {
    _BASE + "/mapa": _MAPA,
    _BASE
    + "/kategoria,plaze-i-morze": _listing(
        "https://hel.webcamera.pl/",
        "https://jaroslawiec.webcamera.pl/",  # inactive marker: stays skipped
        "https://www.webcamera.pl/",  # nav noise, not a cam page
        "https://static.webcamera.pl/x.css",  # asset noise
    ),
    _BASE + "/kategoria,mazury": _listing(),
    # the city street cam is listed under BOTH miasta and gory: miasta wins
    _BASE + "/kategoria,miasta": _listing("https://krupowki.webcamera.pl/"),
    _BASE
    + "/kategoria,stacje-narciarskie": _listing(
        "https://czarnagora.webcamera.pl/",  # not on the map at all
        "https://mp4cam.webcamera.pl/",
        "https://offline.webcamera.pl/",
    ),
    _BASE + "/kategoria,gory": _listing("https://krupowki.webcamera.pl/"),
    "https://hel.webcamera.pl/": _player(
        "https://hoktastream1.webcamera.pl/hel_cam/hel_cam.stream/playlist.m3u8",
        "HEL - widok na bulwar i plażę",
    ),
    # no h1 -> marker-name fallback, with the trailing badge stripped
    f"{_BASE}/kamera/krakow1/": _player(
        "https://hoktastream2.webcamera.pl/krakow_cam/krakow_cam.stream/playlist.m3u8"
    ),
    "https://krupowki.webcamera.pl/": _player(
        "https://hoktastream5.webcamera.pl/krupowki_cam/krupowki_cam.stream/playlist.m3u8",
        "Zakopane - widok na Krupówki NOWOŚĆ",
    ),
    # no h1 and no marker name -> slug-title fallback
    "https://czarnagora.webcamera.pl/": _player(
        "https://hoktastream2.webcamera.pl/cg_cam/cg_cam.stream/playlist.m3u8"
    ),
    # an .mp4 video_src is a rebroadcast placeholder -> dropped
    "https://mp4cam.webcamera.pl/": _player(
        "https://hoktastream1.webcamera.pl/loop/last.mp4", "Loop"
    ),
    # no player config at all -> dropped
    "https://offline.webcamera.pl/": "<html><h1>offline</h1></html>",
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_webcamerapl_discovers_decodes_and_categorises():
    cands = list(WebcameraPlSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    assert set(by_title) == {
        "HEL - widok na bulwar i plażę",
        "KRAKÓW - Rynek Główny",  # marker-name fallback, badge stripped
        "Zakopane - widok na Krupówki",  # h1 badge stripped
        "Czarnagora",  # slug fallback
    }

    # video_src ROT13-decoded to the real HLS URL
    assert (
        by_title["HEL - widok na bulwar i plażę"].target_url
        == "https://hoktastream1.webcamera.pl/hel_cam/hel_cam.stream/playlist.m3u8"
    )

    # categories come from the listing pages, priority-ordered (miasta beats gory)
    assert by_title["HEL - widok na bulwar i plażę"].category == "Beaches"
    assert by_title["Zakopane - widok na Krupówki"].category == "Cities"
    assert by_title["Czarnagora"].category == "Ski Resorts"
    assert by_title["KRAKÓW - Rynek Główny"].category is None  # map-only cam

    # inactive / playlist / premium markers never become cams
    for skipped in ("jaroslawiec", "50-plaz", "50-plaz-premium"):
        assert not any(skipped in c.source_page_url for c in cands)
    # nav/asset links inside and around the listing are not cam pages
    assert not any("ignored" in c.source_page_url for c in cands)

    for c in cands:
        assert c.source == "webcamerapl"
        assert c.angle_key is None  # one player per page
        assert (c.predisc_key or "").startswith("hls:")


def test_webcamerapl_survives_dead_site():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(WebcameraPlSource(_Dead()).discover()) == []
