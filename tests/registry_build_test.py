import pytest

from webcam_aggregator.app import build_registry
from webcam_aggregator.extractors.base import Extractor, Resolved


class _Stub:
    def resolve(self, target_url: str) -> Resolved:
        return Resolved(url=target_url, stream_type="hls", ttl_seconds=None)


_EXTRACTORS: dict[str, Extractor] = {
    n: _Stub()
    for n in (
        "ytdlp",
        "direct",
        "metatag",
        "baltic",
        "ipcamlive",
        "skyline",
        "earthcam",
        "wetmet",
        "hdontap",
        "ozolio",
    )
}


def _route(url: str) -> str | None:
    return build_registry(_EXTRACTORS).match(url)


def test_real_registry_predicates():
    assert _route("https://webtv.feratel.com/webtv/?cam=1") == "metatag"
    assert _route("https://g0.ipcamlive.com/player/player.php?alias=x") == "ipcamlive"
    # the MAJORITY (direct ipcamlive m3u8) must route to DirectHls, NOT the resolver
    assert _route("https://s79.ipcamlive.com/streams/abc/stream.m3u8") == "direct"
    assert _route("https://balticlivecam.com/cameras/x/?embed") == "baltic"
    # skyline cam PAGE -> the skyline resolver; its youtube embeds fall through to ytdlp
    assert (
        _route("https://www.skylinewebcams.com/en/webcam/italia/x/cam.html")
        == "skyline"
    )
    assert _route("https://www.earthcam.com/usa/x/?cam=y") == "earthcam"
    assert _route("https://www.twitch.tv/somechannel") == "ytdlp"
    assert _route("https://www.youtube.com/watch?v=aaaaaaaaaaa") == "ytdlp"
    assert _route("https://worldcams.tv/player?url=https://x/p.m3u8") == "direct"
    # hdontap/ozolio cam PAGES -> their resolvers; the resolved CDN URLs (live.hdontap
    # HLS, *-relay.ozolio Wowza) don't match the page-path predicates
    assert _route("https://hdontap.com/stream/269629/carmel-by-the-sea/") == "hdontap"
    assert (
        _route("https://live.hdontap.com/hls/hosb1/x.stream/playlist.m3u8?t=1&e=2")
        == "direct"
    )
    assert _route("https://www.ozolio.com/explore/CID_BFWA000002E5") == "ozolio"
    assert _route("https://example.com/page") is None


def test_unknown_extractor_name_fails_at_build():
    incomplete: dict[str, Extractor] = {
        k: v for k, v in _EXTRACTORS.items() if k != "baltic"
    }
    with pytest.raises(ValueError):
        build_registry(incomplete)


def test_bare_alias_landing_page_routes_to_the_resolver() -> None:
    """Some camscape embeds point at the share page, https://www.ipcamlive.com/<alias>,
    which carries no address/streamid — it used to fall through with no extractor."""
    assert _route("https://www.ipcamlive.com/campusmartius") == "ipcamlive"
    assert _route("https://www.ipcamlive.com/627b6e3cac4e9") == "ipcamlive"
    assert _route("http://ipcamlive.com/68094c989e733") == "ipcamlive"
    assert _route("https://www.ipcamlive.com/kobyla/") == "ipcamlive"


def test_alias_rule_does_not_steal_direct_ipcamlive_m3u8() -> None:
    """The alias rule must stay anchored to the apex/www host: the s*/g* subdomains
    carry the direct m3u8s that have to reach DirectHls."""
    assert _route("https://s79.ipcamlive.com/streams/abc/stream.m3u8") == "direct"
    assert _route("https://s111.ipcamlive.com/streams/6f3obm/stream.m3u8") == "direct"
    assert _route("http://s2.ipcamlive.com/streams/02mih6/stream.m3u8") == "direct"


def test_wetmet_widget_frame_routes_to_its_resolver() -> None:
    assert (
        _route("https://api.wetmet.net/widgets/stream/frame.php?uid=2d864f52e2fd96")
        == "wetmet"
    )


def test_wetmet_direct_m3u8_still_routes_to_direct() -> None:
    """Only the widget frame needs the resolver; a resolved wetmet CDN URL is plain HLS."""
    assert (
        _route("https://wmso-us-ea1.wetmet.net/live/289-06-01/playlist.m3u8?x=1")
        == "direct"
    )
