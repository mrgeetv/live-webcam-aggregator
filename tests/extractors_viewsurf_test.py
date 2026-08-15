import pytest

from webcam_aggregator.extractors.viewsurf import ViewsurfResolver

_UUID = "e6b6f1cd-ae5d-40f2-3032-3630-6d61-63-a744-dbccb109af26d"
_TARGET = f"https://platforms5.joada.net/embeded/embeded.html?uuid={_UUID}&type=live"
_M3U8 = f"https://ds2-cache.quanteec.com/contents/encodings/live/{_UUID}/master.m3u8"

# trimmed real manifest-API response
_MANIFEST = f"""{{
  "thumbnail": "https://ds2-cache.quanteec.com/contents/encodings/live/{_UUID}/thumbnail.jpg",
  "poster": "https://ds2-cache.quanteec.com/contents/encodings/live/{_UUID}/poster.jpg",
  "mpd": "https://ds2-cache.quanteec.com/contents/encodings/live/{_UUID}/mpd.mpd",
  "m3u8": "{_M3U8}",
  "alternativeBaseUrls": ["https://ds2-cache.quanteec.com"]
}}"""


def _fetch(responses: dict[str, str], calls: list[str]):
    def fetch(url: str) -> str:
        calls.append(url)
        for host, body in responses.items():
            if url.startswith(host):
                return body
        # the wired resolver fetch raises rather than returning None
        raise ValueError("resolver fetch failed")

    return fetch


def test_viewsurf_resolves_via_manifest_api() -> None:
    calls: list[str] = []
    out = ViewsurfResolver(
        _fetch({"https://platforms8.joada.net": _MANIFEST}, calls)
    ).resolve(_TARGET)

    assert out.url == _M3U8
    assert out.stream_type == "hls"
    # no token in the chain, but the API picks the delivery host per call and can
    # rebalance — don't cache the URL as permanent
    assert out.ttl_seconds is not None and out.ttl_seconds <= 1800

    assert len(calls) == 1
    assert calls[0] == f"https://platforms8.joada.net/api/videos/manifest/{_UUID}"


def test_viewsurf_falls_back_to_second_api_host() -> None:
    calls: list[str] = []
    out = ViewsurfResolver(
        _fetch({"https://platforms9.joada.net": _MANIFEST}, calls)
    ).resolve(_TARGET)

    assert out.url == _M3U8
    assert len(calls) == 2
    assert calls[1].startswith("https://platforms9.joada.net/api/videos/manifest/")


def test_viewsurf_all_api_hosts_down_raises() -> None:
    with pytest.raises(ValueError, match="no manifest from any api host"):
        ViewsurfResolver(_fetch({}, [])).resolve(_TARGET)


def test_viewsurf_non_json_on_first_host_falls_back_to_second() -> None:
    # a dead host serving an HTML maintenance page (a 200, not a raised fetch) must
    # not abort the resolve — the second host is tried
    calls: list[str] = []
    out = ViewsurfResolver(
        _fetch(
            {
                "https://platforms8.joada.net": "<html>maintenance</html>",
                "https://platforms9.joada.net": _MANIFEST,
            },
            calls,
        )
    ).resolve(_TARGET)
    assert out.url == _M3U8
    assert len(calls) == 2  # host8 (HTML) then host9 (JSON)


def test_viewsurf_json_without_m3u8_raises() -> None:
    fetch = _fetch({"https://platforms8.joada.net": '{"mpd": "x"}'}, [])
    with pytest.raises(ValueError, match="no m3u8"):
        ViewsurfResolver(fetch).resolve(_TARGET)


def test_viewsurf_target_without_uuid_raises() -> None:
    with pytest.raises(ValueError, match="no uuid"):
        ViewsurfResolver(lambda _u: "").resolve("https://platforms5.joada.net/x.html")


def test_viewsurf_error_messages_carry_no_url() -> None:
    # errors feed the aggregated resolve-failed detail at INFO; a URL would shatter
    # one failure mode into per-URL buckets
    cases = [
        ({}, _TARGET),
        ({"https://platforms8.joada.net": "not json"}, _TARGET),
        ({"https://platforms8.joada.net": "{}"}, _TARGET),
        ({}, "https://platforms5.joada.net/embeded/embeded.html"),
    ]
    for responses, target in cases:
        with pytest.raises(ValueError) as exc:
            ViewsurfResolver(_fetch(responses, [])).resolve(target)
        assert "http" not in str(exc.value)
