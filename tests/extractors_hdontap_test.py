from pathlib import Path

import pytest

from webcam_aggregator.extractors.hdontap import HdontapResolver, stream_src

_FIX = Path(__file__).parent / "fixtures"
_CAM_HTML = (_FIX / "hdontap_cam.html").read_text()


def test_hdontap_resolves_player_data_hls_with_both_token_params():
    r = HdontapResolver(lambda _u: _CAM_HTML).resolve(
        "https://hdontap.com/stream/269629/carmel-by-the-sea-live-webcam/"
    )
    # the JSON & decodes to a real "&": BOTH t and e survive (a clipped &e= 403s)
    assert r.url == (
        "https://live.hdontap.com/hls/hosb1/ticklepink_ttv.stream/playlist.m3u8"
        "?t=9Whr9Y5awOBVYfeFLBaEMw&e=1786820572"
    )
    assert r.stream_type == "hls"
    assert r.ttl_seconds is not None  # token is per-fetch; never cache indefinitely


def test_hdontap_offline_page_raises_without_url_in_message():
    # an offline cam renders its page with no player-data blob at all
    with pytest.raises(ValueError) as exc:
        HdontapResolver(lambda _u: "<title>Ospreys - HDOnTap</title>Offline").resolve(
            "https://hdontap.com/stream/723586/bay-point-marina-ospreys/"
        )
    assert "http" not in str(exc.value)  # error detail feeds INFO aggregates


def test_stream_src_rejects_malformed_or_streamless_blobs():
    bad_json = '<script id="player-data" type="application/json">{oops</script>'
    no_src = '<script id="player-data" type="application/json">{"ads": {}}</script>'
    not_hls = (
        '<script id="player-data" type="application/json">'
        '{"streamSrc": "https://x/clip.mp4"}</script>'
    )
    assert stream_src(bad_json) is None
    assert stream_src(no_src) is None
    assert stream_src(not_hls) is None
