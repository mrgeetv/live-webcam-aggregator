from pathlib import Path

import pytest

from webcam_aggregator.extractors.skyline import SkylineResolver

_FIX = Path(__file__).parent / "fixtures"


def test_skyline_resolver_builds_hd_auth_manifest_url():
    page = (_FIX / "skyline_cam.html").read_text()  # a real, trimmed cam page
    r = SkylineResolver(lambda _u: page).resolve(
        "https://www.skylinewebcams.com/en/webcam/x.html"
    )
    assert (
        r.url
        == "https://hd-auth.skylinewebcams.com/live.m3u8?a=k9l04et6asstdvt6ik56acjf4v"
    )
    assert r.stream_type == "hls"
    # Under skyline's measured idle token lapse (~1-2 min): ResolveCache keeps the
    # entry for ttl * TTL_FACTOR = 48s, so a second viewer can't be handed a token
    # that went stale while nobody was watching.
    assert r.ttl_seconds == 60


def test_skyline_resolver_raises_when_no_token():
    with pytest.raises(ValueError) as exc:
        SkylineResolver(lambda _u: "<strong>OFFLINE</strong>").resolve(
            "https://www.skylinewebcams.com/en/webcam/x.html"
        )
    # No URL in the message: it feeds the aggregated INFO detail, where a URL
    # both leaks paths (DEBUG-only per the log policy) and shatters one failure
    # mode into per-prefix buckets when the detail string is truncated.
    assert "skylinewebcams" not in str(exc.value)
