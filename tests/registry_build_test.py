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
    assert _route("https://example.com/page") is None


def test_unknown_extractor_name_fails_at_build():
    incomplete: dict[str, Extractor] = {
        k: v for k, v in _EXTRACTORS.items() if k != "baltic"
    }
    with pytest.raises(ValueError):
        build_registry(incomplete)
