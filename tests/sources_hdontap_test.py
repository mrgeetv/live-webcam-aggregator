from pathlib import Path

from webcam_aggregator.sources.hdontap import (
    HdOnTapSource,
    _title_of,  # pyright: ignore[reportPrivateUsage]
)

_FIX = Path(__file__).parent / "fixtures"

# The /embed/ twins live in <video:player_loc>, not <loc> — they must not become
# pages. One cam (269629) is listed under two slugs: dedupe on the numeric id.
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://hdontap.com/</loc></url>
<url><loc>https://hdontap.com/explore/tag/beaches/</loc></url>
<url><loc>https://hdontap.com/stream/269629/carmel-by-the-sea-live-webcam/</loc>
<video:video><video:player_loc>https://hdontap.com/stream/269629/carmel-by-the-sea-live-webcam/embed/</video:player_loc></video:video></url>
<url><loc>https://hdontap.com/stream/269629/tickle-pink-inn-big-sur-coast-live-webcam/</loc></url>
<url><loc>https://hdontap.com/stream/390711/cook-inlet-alaska-bear-viewing/</loc></url>
<url><loc>https://hdontap.com/stream/723586/bay-point-marina-ospreys/</loc></url>
</urlset>"""

# Pages without a player-data blob go through the standard ladder: a YouTube embed
# resolves; the GTM consent iframe every page carries is denylisted noise.
_GTM = '<iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe>'
_YT_PAGE = (
    "<title>Cook Inlet Alaska Bear Viewing Live Cam - HDOnTap</title>"
    + _GTM
    + '<iframe src="https://www.youtube.com/embed/Hkvl3vlT4k8" allowfullscreen></iframe>'
)
# offline: no player-data blob, no embed -> yields nothing
_OFFLINE_PAGE = "<title>Bay Point Marina Ospreys - HDOnTap</title>" + _GTM + "Offline"

_PAGES = {
    "https://hdontap.com/sitemap.xml": _SITEMAP_XML,
    "https://hdontap.com/stream/269629/carmel-by-the-sea-live-webcam/": (
        _FIX / "hdontap_cam.html"
    ).read_text(),
    "https://hdontap.com/stream/390711/cook-inlet-alaska-bear-viewing/": _YT_PAGE,
    "https://hdontap.com/stream/723586/bay-point-marina-ospreys/": _OFFLINE_PAGE,
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_hdontap_discover_dedup_native_hls_and_youtube():
    cands = list(HdOnTapSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # carmel (native HLS) + cook inlet (YouTube); offline yields nothing; the
    # duplicate-slug 269629 entry and the /embed/ player_loc never become pages
    assert set(by_title) == {"Carmel-By-The-Sea", "Cook Inlet Alaska Bear Viewing"}

    # a native-HLS cam's candidate is the PAGE url (the extractor re-resolves the
    # per-fetch t/e token at serve time), never mergeable -> predisc_key None
    carmel = by_title["Carmel-By-The-Sea"]
    assert (
        carmel.target_url
        == "https://hdontap.com/stream/269629/carmel-by-the-sea-live-webcam/"
    )
    assert carmel.predisc_key is None

    # a YouTube-embed cam goes through the standard ladder and dedups on yt:
    yt = by_title["Cook Inlet Alaska Bear Viewing"]
    assert yt.target_url == "https://www.youtube.com/watch?v=Hkvl3vlT4k8"
    assert yt.predisc_key == "yt:Hkvl3vlT4k8"

    # the GTM consent iframe never leaks through as a candidate
    assert not any("googletagmanager" in c.target_url for c in cands)
    for c in cands:
        assert c.source == "hdontap"
        assert c.category is None  # no taxonomy -> title fallback / "Other"


def test_hdontap_title_stripping():
    url = "https://hdontap.com/stream/1/pa-peregrine-falcon-cam-live-webcam/"
    # boilerplate is stripped once, end-anchored: a mid-name "Cam" survives
    t = _title_of("<title>PA Peregrine Falcon Cam Live Webcam - HDOnTap</title>", url)
    assert t == "PA Peregrine Falcon Cam"
    t = _title_of(
        "<title>Sandcastle Condos Port Aransas Live Beach Cam - HDOnTap</title>", url
    )
    assert t == "Sandcastle Condos Port Aransas"
    # entities decoded before stripping
    t = _title_of("<title>Clearwater Beach &amp; Pier Live Cam - HDOnTap</title>", url)
    assert t == "Clearwater Beach & Pier"
    # no <title> -> prettified slug (boilerplate stripped there too)
    assert _title_of("no title here", url) == "Pa Peregrine Falcon Cam"
