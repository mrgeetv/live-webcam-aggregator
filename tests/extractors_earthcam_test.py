import pytest

from webcam_aggregator.extractors.earthcam import EarthcamResolver


def test_earthcam_resolves_live_hls_and_unescapes():
    page = (
        r'{"html5_streamingdomain":"https:\/\/videos-3.earthcam.com",'
        r'"liveURL":"https:\/\/videos-3.earthcam.com\/fecnetwork\/hd.flv'
        r'\/playlist.m3u8?t=tok&td=202606"}'
    )
    r = EarthcamResolver(lambda _u: page).resolve("https://www.earthcam.com/x/?cam=y")
    assert (
        r.url
        == "https://videos-3.earthcam.com/fecnetwork/hd.flv/playlist.m3u8?t=tok&td=202606"
    )
    assert r.stream_type == "hls"


def test_earthcam_raises_when_only_archive_present():
    # an *archives VOD host is not the live feed -> unresolvable
    page = r'"https:\/\/video2archives.earthcam.com\/x\/playlist.m3u8"'
    with pytest.raises(ValueError):
        EarthcamResolver(lambda _u: page).resolve("https://www.earthcam.com/x/")
