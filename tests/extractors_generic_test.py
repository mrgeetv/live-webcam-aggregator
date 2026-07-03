import time
from pathlib import Path

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
