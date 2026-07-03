import pytest

from webcam_aggregator.extractors.skyline import SkylineResolver


def test_skyline_resolver_builds_hd_auth_manifest_url():
    page = "<script>new Clappr.Player({source:'livee.m3u8?a=tok123abc456'});</script>"
    r = SkylineResolver(lambda _u: page).resolve(
        "https://www.skylinewebcams.com/en/webcam/x.html"
    )
    assert r.url == "https://hd-auth.skylinewebcams.com/live.m3u8?a=tok123abc456"
    assert r.stream_type == "hls"
    assert r.ttl_seconds == 300


def test_skyline_resolver_raises_when_no_token():
    with pytest.raises(ValueError):
        SkylineResolver(lambda _u: "<strong>OFFLINE</strong>").resolve(
            "https://www.skylinewebcams.com/en/webcam/x.html"
        )
