import pytest

from webcam_aggregator.extractors.ozolio import OzolioResolver

_TARGET = "https://www.ozolio.com/explore/BFWA000002E5"

# trimmed real relay responses
_INIT = """{
  "session": {
    "id": "SID_KIYC000AF552",
    "server": "https://relay.ozolio.com",
    "home": "https://www.ozolio.com/explore/BFWA000002E5"
  },
  "outputs": [{"id": "1", "name": "Keauhou Bay, Hawaii", "media": "LIVE"}]
}"""

_LIVE_SOURCE = (
    "https://use01-smr05-relay.ozolio.com:443/hls-live/_definst_/"
    "relay01.bfwauv.edge.basic.stream/playlist.m3u8"
)
_OPEN_LIVE = f"""{{
  "output": {{
    "id": "1",
    "name": "Keauhou Bay, Hawaii",
    "source": "{_LIVE_SOURCE}",
    "state": "Active",
    "media": "LIVE",
    "format": "M3U8"
  }},
  "location": {{"city": "Kahaluu-Keauhou", "province": "HI"}}
}}"""

# some entries play a canned media-library loop instead of a camera
_OPEN_ROLL = """{
  "output": {
    "id": "1",
    "source": "https://usw01-smr04-relay.ozolio.com:443/hls-roll/_definst_/mp4:ozsd/ozolio-media-library/747/videos/c01.mp4/playlist.m3u8",
    "media": "ROLL",
    "format": "M3U8"
  }
}"""


def _fetch(init: str, opened: str, calls: list[str]):
    def fetch(url: str) -> str:
        calls.append(url)
        if "cmd=init" in url:
            return init
        if "cmd=open" in url:
            return opened
        raise AssertionError(f"unexpected fetch {url}")

    return fetch


def test_ozolio_resolves_live_stream():
    calls: list[str] = []
    out = OzolioResolver(_fetch(_INIT, _OPEN_LIVE, calls)).resolve(_TARGET)

    assert out.url == _LIVE_SOURCE
    assert out.stream_type == "hls"
    # the Wowza session is minted for whoever fetches the manifest, so the TTL
    # must stay short enough to re-resolve before it lapses
    assert out.ttl_seconds is not None and out.ttl_seconds <= 240

    # call 1: init carries the CID and the gating `document` param (urlencoded)
    assert len(calls) == 2
    assert "cmd=init" in calls[0] and "oid=CID_BFWA000002E5" in calls[0]
    assert (
        "document=https%3A%2F%2Frelay.ozolio.com%2Fpub.api"
        "%3Fcmd%3Dexplore%26oid%3DCID_BFWA000002E5%26channel%3D0" in calls[0]
    )
    # call 2: open uses the session id from init
    assert "cmd=open" in calls[1] and "oid=SID_KIYC000AF552" in calls[1]


def test_ozolio_canned_loop_is_rejected():
    with pytest.raises(ValueError, match="canned loop"):
        OzolioResolver(_fetch(_INIT, _OPEN_ROLL, [])).resolve(_TARGET)


def test_ozolio_no_session_raises():
    # the relay's refusal shape is an HTML error page, not JSON
    html = "<html><h2>ERROR: 403 Forbidden</h2></html>"
    with pytest.raises(ValueError, match="non-JSON"):
        OzolioResolver(_fetch(html, _OPEN_LIVE, [])).resolve(_TARGET)
    # or JSON without a session block
    with pytest.raises(ValueError, match="no relay session"):
        OzolioResolver(_fetch('{"channel": []}', _OPEN_LIVE, [])).resolve(_TARGET)


def test_ozolio_open_without_source_raises():
    with pytest.raises(ValueError, match="no stream"):
        OzolioResolver(_fetch(_INIT, '{"output": {"media": "LIVE"}}', [])).resolve(
            _TARGET
        )


def test_ozolio_target_without_cid_raises():
    with pytest.raises(ValueError, match="no camera id"):
        OzolioResolver(lambda _u: "").resolve("https://www.ozolio.com/about-us/")


def test_ozolio_cid_prefixed_target_parses_the_bare_id():
    # a CID_-prefixed explore URL must yield the bare id, not capture "CID" and
    # silently query oid=CID_CID
    calls: list[str] = []
    OzolioResolver(_fetch(_INIT, _OPEN_LIVE, calls)).resolve(
        "https://www.ozolio.com/explore/CID_BFWA000002E5"
    )
    assert "oid=CID_BFWA000002E5" in calls[0] and "CID_CID" not in calls[0]


def test_ozolio_error_messages_carry_no_url():
    # errors feed the aggregated resolve-failed detail at INFO; a URL would leak
    # session ids and shatter one failure mode into per-URL buckets
    for init, opened, target in [
        (_INIT, _OPEN_ROLL, _TARGET),
        ('{"x": 1}', _OPEN_LIVE, _TARGET),
        (_INIT, _OPEN_LIVE, "https://elsewhere.example/"),
    ]:
        with pytest.raises(ValueError) as exc:
            OzolioResolver(_fetch(init, opened, [])).resolve(target)
        assert "http" not in str(exc.value)
