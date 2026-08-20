import time
from pathlib import Path

import pytest

from webcam_aggregator.extractors.direct_hls import DirectHls
from webcam_aggregator.extractors.metatag import MetaTagExtractor
from webcam_aggregator.extractors.ytdlp import YtDlpExtractor

FIX = Path(__file__).parent / "fixtures"


def test_directhls_unwraps_player_wrapper():
    inner = "https://stream1.example.no/cam/playlist.m3u8"
    out = DirectHls().resolve(f"https://worldcams.tv/player?url={inner}")
    assert out.url == inner
    assert out.stream_type == "hls"


def test_directhls_passthrough_plain_m3u8():
    out = DirectHls().resolve("https://cdn.x/live.m3u8")
    assert out.url == "https://cdn.x/live.m3u8"


def test_metatag_pulls_og_video():
    html = (FIX / "feratel.html").read_text()
    out = MetaTagExtractor(fetch=lambda u: html).resolve(
        "https://webtv.feratel.com/webtv/?cam=1"
    )
    assert out.url.endswith("Vid.mp4")
    assert out.stream_type == "mp4"


def test_ytdlp_parses_expire_ttl():
    future = int(time.time()) + 6 * 3600
    url = (
        f"https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/{future}/x"
    )
    out = YtDlpExtractor(run=lambda argv: url).resolve(
        "https://www.youtube.com/watch?v=abc"
    )
    assert out.stream_type == "hls"
    assert out.ttl_seconds is not None
    assert 5 * 3600 < out.ttl_seconds <= 6 * 3600


def test_ytdlp_requests_hls_format():
    captured: list[str] = []

    def _run(argv: list[str]) -> str:
        captured.extend(argv)
        return "https://x.googlevideo.com/playlist.m3u8"

    YtDlpExtractor(run=_run).resolve("https://www.youtube.com/watch?v=abc")
    # must select an HLS-protocol format so we never get served a DASH .mpd
    assert "-f" in captured
    assert "m3u8" in captured[captured.index("-f") + 1]


def test_ytdlp_pins_a_muxed_player_client():
    """We emit ONE url, so the resolve must ask for clients that still serve
    youtube's muxed live ladder — yt-dlp's default client serves video-only and
    audio-only formats this extractor cannot use."""
    captured: list[str] = []

    def _run(argv: list[str]) -> str:
        captured.extend(argv)
        return "https://x.googlevideo.com/playlist.m3u8"

    YtDlpExtractor(run=_run).resolve("https://www.youtube.com/watch?v=abc")
    assert "--extractor-args" in captured
    arg = captured[captured.index("--extractor-args") + 1]
    assert arg.startswith("youtube:player_client=")
    # namespaced to youtube, so the twitch resolves sharing this extractor are
    # unaffected
    assert "mweb" in arg


def test_ytdlp_rejects_a_split_format():
    """Two urls mean yt-dlp picked separate video and audio streams. Taking one
    would ship a cam that resolves clean and plays audio only."""
    split = (
        "https://x.googlevideo.com/video-only.m3u8\n"
        "https://x.googlevideo.com/audio-only.m3u8"
    )
    with pytest.raises(ValueError, match="split format"):
        YtDlpExtractor(run=lambda argv: split).resolve(
            "https://www.youtube.com/watch?v=abc"
        )


def test_wetmet_extracts_the_player_script_url() -> None:
    """The widget's inline script assigns the master playlist to `vurl` (plain, not
    JSON-escaped — the fixture mirrors the real page)."""
    from webcam_aggregator.extractors.wetmet import WetmetResolver

    body = (
        "\t\t\t\tvar tuprun = false;\n"
        "\t\t\t\tvar vurl = 'https://wmso-us-ea1.wetmet.net/live/289-06-01"
        "/playlist.m3u8?wmsAuthSign=abc123';\n"
    )
    out = WetmetResolver(lambda _u: body).resolve(
        "https://api.wetmet.net/widgets/stream/frame.php?uid=deadbeef"
    )
    assert out.url == (
        "https://wmso-us-ea1.wetmet.net/live/289-06-01/playlist.m3u8?wmsAuthSign=abc123"
    )
    assert out.stream_type == "hls"
    assert out.ttl_seconds == 300  # wmsAuthSign is time-limited


def test_wetmet_missing_m3u8_raises() -> None:
    from webcam_aggregator.extractors.wetmet import WetmetResolver

    with pytest.raises(ValueError, match="no m3u8"):
        WetmetResolver(lambda _u: "<html>offline</html>").resolve(
            "https://api.wetmet.net/widgets/stream/frame.php?uid=deadbeef"
        )
