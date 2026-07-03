import pytest

from webcam_aggregator.extractors.baltic import BalticResolver
from webcam_aggregator.extractors.ipcamlive import IpcamliveResolver

# --- offline unit tests -----------------------------------------------------


def test_ipcamlive_builds_m3u8():
    body = 'var address = "https://g0.ipcamlive.com/"; var streamid = "abc123";'
    out = IpcamliveResolver(fetch=lambda u: body).resolve(
        "https://g0.ipcamlive.com/player.php?alias=x"
    )
    assert out.url == "https://g0.ipcamlive.com/streams/abc123/stream.m3u8"
    assert out.stream_type == "hls"


def test_ipcamlive_missing_vars_raises():
    with pytest.raises(ValueError):
        IpcamliveResolver(fetch=lambda u: "no vars here").resolve(
            "https://x/player.php?alias=y"
        )


def test_baltic_extracts_m3u8_from_auth_token():
    page = "var data = { action: 'auth_token', id: 126, embed:1 };"
    fragment = 'sources:[{src:"https://edge01.balticlivecam.com/blc/x/index.m3u8?token=hash:1781917651106"}]'

    def get(_url: str) -> str:
        return page

    def post(_url: str, data: dict[str, str]) -> str:
        assert data["action"] == "auth_token"
        assert data["id"] == "126"
        return fragment

    out = BalticResolver(get=get, post=post).resolve(
        "https://balticlivecam.com/cameras/x/?embed"
    )
    assert out.url.endswith("token=hash:1781917651106")
    assert out.stream_type == "hls"
    assert out.ttl_seconds is not None


def test_baltic_no_camera_id_raises() -> None:
    """Page with no auth_token id: raises ValueError."""
    with pytest.raises(ValueError, match="no camera id"):
        BalticResolver(
            get=lambda _url: "<html>no data block here</html>",
            post=lambda _url, _data: "",
        ).resolve("https://balticlivecam.com/cameras/missing/?embed")


def test_baltic_no_m3u8_in_response_raises() -> None:
    """Valid camera id but post response has no .m3u8 URL → ValueError."""
    page = "var data = { action: 'auth_token', id: 42, embed: 1 };"
    with pytest.raises(ValueError, match="no m3u8"):
        BalticResolver(
            get=lambda _url: page,
            post=lambda _url, _data: "<div>error: no stream available</div>",
        ).resolve("https://balticlivecam.com/cameras/offline/?embed")


def test_baltic_ttl_derived_from_epoch_ms() -> None:
    """ttl_seconds is derived from the 13-digit epoch-ms in the token."""
    import time

    page = "var data = { action: 'auth_token', id: 7, embed: 1 };"
    # Use a far-future epoch_ms (year 2099) so ttl is always positive in tests.
    future_epoch_ms = 4_070_908_800_000  # 2099-01-01 00:00:00 UTC in ms
    fragment = (
        f'src:"https://edge01.balticlivecam.com/blc/cam/playlist.m3u8'
        f'?token=h:{future_epoch_ms}"'
    )

    out = BalticResolver(
        get=lambda _url: page,
        post=lambda _url, _data: fragment,
    ).resolve("https://balticlivecam.com/cameras/ttl-test/?embed")

    assert out.ttl_seconds is not None
    expected = future_epoch_ms // 1000 - int(time.time())
    # Allow ±2 s for execution time
    assert abs(out.ttl_seconds - expected) <= 2
