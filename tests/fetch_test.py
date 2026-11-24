from __future__ import annotations

import http.server
import logging
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
import requests
from curl_cffi import requests as curl_requests

from webcam_aggregator.fetch import (
    MAX_BYTES,
    UA,
    Fetcher,
    FetchStats,
    HostPacer,
    _as_requests_error,  # pyright: ignore[reportPrivateUsage]
    _classify,  # pyright: ignore[reportPrivateUsage]
    _FetchError,  # pyright: ignore[reportPrivateUsage]
    _ImpersonatingSession,  # pyright: ignore[reportPrivateUsage]
    _pin,  # pyright: ignore[reportPrivateUsage]
    _PinDNS,  # pyright: ignore[reportPrivateUsage]
    _referer_for,  # pyright: ignore[reportPrivateUsage]
    _validate_ip,  # pyright: ignore[reportPrivateUsage]
    resolve_scrape_workers,
    thread_imap,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_getaddrinfo(ip: str):
    """Return a monkeypatch for socket.getaddrinfo that resolves to a single IP."""
    return [(None, None, None, None, (ip, 0))]


def _url_is_safe(url: str) -> bool:
    """Return True iff the URL resolves to a non-private IP.

    This is a test predicate over the real gate: the Fetcher would be allowed to fetch
    the URL.
    """
    return _validate_ip(url)[0] is not None


# ---------------------------------------------------------------------------
# URL safety (_validate_ip) — private/reserved addresses must be blocked
# ---------------------------------------------------------------------------


def test_url_safety_blocks_link_local() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("169.254.169.254"),
    ):
        assert _url_is_safe("http://169.254.169.254/latest/meta-data/") is False


def test_url_safety_blocks_loopback() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("127.0.0.1"),
    ):
        assert _url_is_safe("http://127.0.0.1/") is False


def test_url_safety_blocks_private_10() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("10.0.0.1"),
    ):
        assert _url_is_safe("http://10.0.0.1/") is False


def test_url_safety_blocks_private_192_168() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("192.168.1.1"),
    ):
        assert _url_is_safe("http://192.168.1.1/") is False


def test_url_safety_blocks_file_scheme() -> None:
    # file:// must be rejected regardless of DNS
    assert _url_is_safe("file:///etc/passwd") is False


def test_url_safety_blocks_ftp_scheme() -> None:
    assert _url_is_safe("ftp://example.com/x") is False


def test_url_safety_allows_public_host() -> None:
    # Monkeypatch so the test is offline and deterministic
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("93.184.216.34"),  # example.com's public IP
    ):
        assert _url_is_safe("https://example.com/x") is True


def test_url_safety_dns_failure_returns_false() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        side_effect=socket.gaierror("nxdomain"),
    ):
        assert _url_is_safe("https://this.does.not.exist.example/") is False


def test_url_safety_no_host_returns_false() -> None:
    assert _url_is_safe("https:///path") is False


def test_url_safety_blocks_ipv6_loopback() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("::1"),
    ):
        assert _url_is_safe("http://[::1]/") is False


def test_url_safety_blocks_multicast() -> None:
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("224.0.0.1"),
    ):
        assert _url_is_safe("http://multicast.example/") is False


# ---------------------------------------------------------------------------
# Fetcher — redirects are followed manually, each hop re-validated
# ---------------------------------------------------------------------------


def test_fetcher_does_not_follow_redirect_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def addrinfo(host: str, *_a: object, **_k: object) -> list[tuple[object, ...]]:
        ip = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(None, None, None, None, (ip, 0))]

    fetched: list[str] = []

    class _Resp:
        is_redirect: bool = True
        is_permanent_redirect: bool = False
        headers: dict[str, str] = {"Location": "http://127.0.0.1/secret"}

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            return iter(())

        def close(self) -> None: ...

    def fake_get(_self: object, url: str, **_k: object) -> _Resp:
        fetched.append(url)
        return _Resp()

    monkeypatch.setattr("webcam_aggregator.fetch.socket.getaddrinfo", addrinfo)
    monkeypatch.setattr("requests.Session.get", fake_get)

    f = Fetcher(delay=0.0, retries=1)
    # public start → 302 to a private host → must NOT be fetched, returns None
    assert f.get("https://public.example/p.m3u8") is None
    assert fetched == ["https://public.example/p.m3u8"]


# ---------------------------------------------------------------------------
# Fetcher.get_segment
# ---------------------------------------------------------------------------


def test_get_segment_blocks_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_segment refuses private/unsafe URLs, saying why, without a request."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("127.0.0.1"),
    )
    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("http://127.0.0.1/seg.ts") == (None, "blocked-ip")


def test_get_segment_happy_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_segment relays status_code, Content-Type, Content-Range and body."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _FakeResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 206
        headers: dict[str, str] = {
            "Content-Type": "video/mp2t",
            "Content-Range": "bytes 0-65535/1000000",
        }

        def iter_content(self, _chunk_size: int) -> object:
            return iter([b"hello", b"world"])

        def close(self) -> None: ...

    def fake_get(_self: object, _url: str, **_k: object) -> _FakeResp:
        return _FakeResp()

    monkeypatch.setattr("requests.Session.get", fake_get)

    f = Fetcher(delay=0.0, retries=1)
    result, outcome = f.get_segment(
        "https://cdn.balticlivecam.com/seg.ts", range_header="bytes=0-65535"
    )
    assert outcome == "ok"
    assert result is not None
    status, ct, cr, body = result
    assert status == 206
    assert ct == "video/mp2t"
    assert cr == "bytes 0-65535/1000000"
    assert body == b"helloworld"


def test_get_segment_refuses_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_segment refuses a segment URL that redirects."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _RedirectResp:
        is_redirect: bool = True
        is_permanent_redirect: bool = False
        headers: dict[str, str] = {"Location": "https://other.example/seg.ts"}

        def close(self) -> None: ...

    monkeypatch.setattr(
        "requests.Session.get", lambda _self, _url, **_k: _RedirectResp()
    )

    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("https://cdn.balticlivecam.com/seg.ts") == (
        None,
        "unexpected-redirect",
    )


# ---------------------------------------------------------------------------
# Fetcher.get — RequestException paths
# ---------------------------------------------------------------------------


def test_get_returns_none_after_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All retry attempts raise RequestException → Fetcher.get returns None."""
    import requests

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    monkeypatch.setattr(
        "requests.Session.get",
        lambda _self, _url, **_k: (_ for _ in ()).throw(
            requests.RequestException("connection failed")
        ),
    )
    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://cdn.example/playlist.m3u8") is None


def test_get_retries_on_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First attempt raises RequestException, second succeeds → returns content."""
    import requests

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    call_count = [0]

    class _GoodResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            return iter([b"hello"])

        def close(self) -> None: ...

    def fake_get(_self: object, _url: str, **_k: object) -> _GoodResp:
        call_count[0] += 1
        if call_count[0] == 1:
            raise requests.RequestException("transient failure")
        return _GoodResp()

    monkeypatch.setattr("requests.Session.get", fake_get)
    f = Fetcher(delay=0.0, retries=2)
    result = f.get("https://cdn.example/playlist.m3u8")
    assert result == "hello"
    assert call_count[0] == 2


def test_get_redirect_no_location_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect response with no Location header → returns None."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _NoLocResp:
        is_redirect: bool = True
        is_permanent_redirect: bool = False
        headers: dict[str, str] = {}  # no Location

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _NoLocResp())
    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://cdn.example/playlist.m3u8") is None


def test_get_segment_labels_a_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A RequestException in get_segment is a failure labelled as get() labels it."""
    import requests

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    monkeypatch.setattr(
        "requests.Session.get",
        lambda _self, _url, **_k: (_ for _ in ()).throw(
            requests.ConnectTimeout("timed out")
        ),
    )
    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("https://cdn.example/seg.ts") == (None, "timeout")


def test_get_oversized_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Body exceeding MAX_BYTES ceiling → _fetch_following returns None."""
    from webcam_aggregator.fetch import MAX_BYTES

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _BigResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            # Return one chunk larger than MAX_BYTES
            return iter([b"x" * (MAX_BYTES + 1)])

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _BigResp())
    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://cdn.example/big.m3u8") is None


def test_get_respects_custom_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised byte_cap accepts a body the default cap would reject.

    So the manifest fetcher can pull large DVR playlists.
    """
    from webcam_aggregator.fetch import MAX_BYTES

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    big = b"#EXTM3U\n" + b"x" * MAX_BYTES  # just over the default 8 MB ceiling

    class _BigResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            return iter([big])

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _BigResp())
    assert Fetcher(delay=0.0, retries=1).get("https://cdn.x/big.m3u8") is None
    assert (
        Fetcher(delay=0.0, retries=1, byte_cap=MAX_BYTES * 4).get(
            "https://cdn.x/big.m3u8"
        )
        is not None
    )


def test_get_segment_refuses_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A segment exceeding the SEGMENT_MAX_BYTES ceiling is refused as too-large."""
    from webcam_aggregator.fetch import SEGMENT_MAX_BYTES

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _BigSegResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200
        headers: dict[str, str] = {"Content-Type": "video/mp2t"}

        def iter_content(self, _n: int) -> object:
            return iter([b"x" * (SEGMENT_MAX_BYTES + 1)])

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _BigSegResp())
    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("https://cdn.example/seg.ts") == (None, "too-large")


# ---------------------------------------------------------------------------
# Fetcher.post
# ---------------------------------------------------------------------------


def test_post_blocks_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Post must return None for private/loopback URLs without calling the session."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("127.0.0.1"),
    )
    called = [False]

    def fake_post(_self: object, _url: str, **_k: object) -> None:
        called[0] = True

    monkeypatch.setattr("requests.Session.post", fake_post)
    f = Fetcher(delay=0.0, retries=1)
    assert f.post_with_outcome(
        "http://127.0.0.1/admin-ajax.php", {"action": "auth_token"}
    ) == (None, "blocked-ip")
    assert not called[0]


def test_referer_for_gated_hosts() -> None:
    # CamSecure player pages + EarthCam CDN get the right Referer; others get none.
    assert _referer_for("https://camsecure.co/httpswebcam/camsecure/brixham1.html") == {
        "Referer": "https://www.camsecure.co.uk/"
    }
    assert _referer_for("https://camsecure.uk/HLS/x.m3u8") == {
        "Referer": "https://www.camsecure.co.uk/"
    }
    assert _referer_for("https://videos-3.earthcam.com/x/playlist.m3u8") == {
        "Referer": "https://www.earthcam.com/"
    }
    # the embedding site itself (.co.uk) and unrelated hosts get nothing
    assert _referer_for("https://www.camsecure.co.uk/oban.html") == {}
    assert _referer_for("https://example.com/x") == {}


def test_post_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful POST returns the decoded body and the call carried supplied headers and data."""
    import urllib.parse

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    captured_url: list[str] = []
    captured_data: list[bytes] = []
    captured_headers: list[dict[str, str]] = []

    class _OkResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            return iter([b"response body"])

        def close(self) -> None: ...

    def fake_post(
        _self: object, url: str, *, data: bytes, headers: dict[str, str], **_k: object
    ) -> _OkResp:
        captured_url.append(url)
        captured_data.append(data)
        captured_headers.append(headers)
        return _OkResp()

    monkeypatch.setattr("requests.Session.post", fake_post)
    f = Fetcher(delay=0.0, retries=1)
    result = f.post_with_outcome(
        "https://example.com/wp-admin/admin-ajax.php",
        {"action": "auth_token", "id": "42"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert result == ("response body", "ok")
    assert captured_url[0] == "https://example.com/wp-admin/admin-ajax.php"
    # data must be url-encoded bytes
    decoded = urllib.parse.parse_qs(captured_data[0].decode())
    assert decoded["action"] == ["auth_token"]
    assert decoded["id"] == ["42"]
    # supplied headers must be forwarded
    assert captured_headers[0]["X-Requested-With"] == "XMLHttpRequest"
    # and the form Content-Type must be set by default (pre-encoded bytes won't get it
    # auto-added by requests, and servers like WP admin-ajax 400 without it)
    assert captured_headers[0]["Content-Type"] == "application/x-www-form-urlencoded"


def test_post_caller_can_override_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    captured_headers: list[dict[str, str]] = []

    class _OkResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False
        status_code: int = 200

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            return iter([b"ok"])

        def close(self) -> None: ...

    def fake_post(
        _self: object, _url: str, *, headers: dict[str, str], **_k: object
    ) -> _OkResp:
        captured_headers.append(headers)
        return _OkResp()

    monkeypatch.setattr("requests.Session.post", fake_post)
    f = Fetcher(delay=0.0, retries=1)
    f.post_with_outcome(
        "https://example.com/x",
        {"a": "b"},
        headers={"Content-Type": "application/json"},
    )
    assert captured_headers[0]["Content-Type"] == "application/json"


def test_post_refuses_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect response from a POST must return None (admin-ajax shouldn't redirect)."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    class _RedirectResp:
        is_redirect: bool = True
        is_permanent_redirect: bool = False
        headers: dict[str, str] = {"Location": "https://other.example/login"}

        def close(self) -> None: ...

    monkeypatch.setattr(
        "requests.Session.post", lambda _self, _url, **_k: _RedirectResp()
    )
    f = Fetcher(delay=0.0, retries=1)
    assert (
        f.post_with_outcome(
            "https://example.com/wp-admin/admin-ajax.php", {"action": "x"}
        )[0]
        is None
    )


# ---------------------------------------------------------------------------
# _scrape_workers — bad env values warn and fall back to default
# ---------------------------------------------------------------------------


def test_scrape_workers_bad_value_warns_and_returns_default(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SCRAPE_WORKERS", "abc")
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.fetch"):
        result = resolve_scrape_workers()
    import os

    expected_default = min(16, (os.cpu_count() or 2) * 4)
    assert result == expected_default
    assert "SCRAPE_WORKERS" in caplog.text


def test_scrape_workers_valid_value_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SCRAPE_WORKERS", "6")
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.fetch"):
        result = resolve_scrape_workers()
    assert result == 6
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# DNS-rebinding TOCTOU: validate-then-pin-IP (curl --resolve pattern)
# ---------------------------------------------------------------------------


def test_validate_ip_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any private/loopback resolved IP poisons the host → no IP returned."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("10.0.0.1"),
    )
    assert _validate_ip("http://internal.example/") == (None, "blocked-ip")


def test_validate_ip_returns_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """A safe public host resolves to a concrete IP that the caller will pin to."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    assert _validate_ip("https://example.com/x") == ("93.184.216.34", "ok")


def test_pin_dns_pins_getaddrinfo_then_clears() -> None:
    """Inside `with _PinDNS(host, ip)`, getaddrinfo resolves the host to the pinned IP.

    The socket dials the pinned IP; the hostname stays in the URL, so urllib3 still does
    SNI/Host/cert against it. The pin is cleared on exit. Scheme-agnostic by design.
    """
    host = "cam.example"
    with _PinDNS(host, "203.0.113.7"):
        infos = socket.getaddrinfo(host, 443)
        assert any(info[4][0] == "203.0.113.7" for info in infos)
    assert not getattr(_pin, "map", {})  # pin cleared on exit


class _PinStubResp:
    is_redirect: bool = False
    is_permanent_redirect: bool = False
    status_code: int = 200

    def raise_for_status(self) -> None: ...

    def iter_content(self, _n: int) -> object:
        return iter([b"#EXTM3U"])

    def close(self) -> None: ...


def test_fetcher_pins_to_validated_ip_not_a_second_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebinding simulation: the resolver returns a PUBLIC IP at validation time.

    The connection must be pinned to THAT ip (host kept for SNI/Host) via _PinDNS, never
    a fresh lookup at connect time.
    """
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )

    seen: dict[str, str] = {}
    real_init = _PinDNS.__init__

    def spy_init(self: _PinDNS, host: str, ip: str) -> None:
        seen["host"] = host
        seen["ip"] = ip
        real_init(self, host, ip)

    monkeypatch.setattr(_PinDNS, "__init__", spy_init)
    monkeypatch.setattr(
        "requests.Session.get", lambda _self, _url, **_k: _PinStubResp()
    )

    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://cam.example/playlist.m3u8") == "#EXTM3U"
    # Pinned to the IP chosen at validation; host kept for TLS/Host.
    assert seen == {"host": "cam.example", "ip": "93.184.216.34"}


def test_fetcher_get_blocks_private_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetcher.get must make no connection when the host resolves to a private IP."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("127.0.0.1"),
    )
    called = [False]

    def fake_get(_self: object, _url: str, **_k: object) -> None:
        called[0] = True

    monkeypatch.setattr("requests.Session.get", fake_get)
    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://rebind.example/playlist.m3u8") is None
    assert not called[0]


def test_fetcher_tls_verified_against_hostname_while_connecting_to_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof (offline, real sockets) that a pinned connection verifies TLS.

    Stand up an HTTPS server on 127.0.0.1:0 with a self-signed cert for hostname
    'pinned.test'. Resolver maps 'pinned.test' -> 127.0.0.1. The Fetcher must connect to
    the pinned IP yet complete the TLS handshake by validating the cert AGAINST THE
    HOSTNAME.

    This is the regression guard for the hard requirement: connecting to the IP
    must not weaken TLS. If cert validation were done against the IP (or disabled),
    this fetch would fail.
    """
    import ssl
    import tempfile

    crypto = pytest.importorskip("cryptography")
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    hostname = "pinned.test"

    # --- self-signed cert with SAN = pinned.test ---
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    assert crypto  # importorskip handle, silence vulture

    tmp = tempfile.TemporaryDirectory()
    cert_path = f"{tmp.name}/cert.pem"
    key_path = f"{tmp.name}/key.pem"
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"#EXTM3U pinned-ok")

        def log_message(  # noqa: A002 # pyright: ignore[reportImplicitOverride]
            self, format: str, *args: object
        ) -> None: ...

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    # Wait until the listener actually accepts a TCP connection before fetching
    # (avoids a race where the worker thread hasn't entered accept() yet).
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.02)

    try:
        # Resolver: hostname -> loopback. The Fetcher pins the connection here.
        # NOTE: fetch.socket IS the global socket module, so this patch is also
        # seen by urllib3's real socket layer — return a fully-shaped addrinfo
        # tuple (proper family/type/proto) so the actual loopback connect works.
        def _loopback_addrinfo(*_a: object, **_k: object) -> list[tuple[object, ...]]:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", port),
                )
            ]

        monkeypatch.setattr(
            "webcam_aggregator.fetch.socket.getaddrinfo", _loopback_addrinfo
        )
        # _validate_ip would normally reject loopback; bypass ONLY the IP-safety
        # gate for this connectivity test (we are deliberately dialing loopback)
        # while leaving the pin + TLS path fully real.
        monkeypatch.setattr("webcam_aggregator.fetch._ip_is_unsafe", lambda _ip: False)

        # Trust our self-signed CA so the cert validates AGAINST THE HOSTNAME, via
        # REQUESTS_CA_BUNDLE (requests honours it per-request; equivalent to the
        # production default verify=True, just pointed at our test CA — never off).
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", cert_path)

        f = Fetcher(delay=0.0, retries=1)
        body = f.get(f"https://{hostname}:{port}/playlist.m3u8")
        assert body == "#EXTM3U pinned-ok"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Fetch outcome accounting — the seam that lets an empty source say WHY.
# Every test here patches the resolver: the suite is fully offline.
# ---------------------------------------------------------------------------


def test_stats_are_per_instance_not_global() -> None:
    a, b = FetchStats(), FetchStats()
    a.record("one.example", "ok")
    b.record("two.example", "timeout")
    assert a.drain() == {"one.example": {"ok": 1}}
    assert b.drain() == {"two.example": {"timeout": 1}}


def test_stats_drain_clears() -> None:
    s = FetchStats()
    s.record("e.example", "ok")
    assert s.drain() == {"e.example": {"ok": 1}}
    assert s.drain() == {}


def test_stats_record_is_threadsafe() -> None:
    s = FetchStats()

    def hammer() -> None:
        for _ in range(200):
            s.record("e.example", "ok")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.drain() == {"e.example": {"ok": 1600}}


def test_stats_caps_host_cardinality() -> None:
    """Host keys come from scraped third-party HTML — they must not grow unbounded."""
    s = FetchStats(max_hosts=3)
    for i in range(10):
        s.record(f"h{i}.example", "http-404")
    drained = s.drain()
    assert len(drained) == 4  # 3 real hosts + the "other" bucket
    assert drained["other"]["http-404"] == 7


def test_one_outcome_per_call_not_per_retry() -> None:
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=3, stats=s)
    resp = requests.Response()
    resp.status_code = 500
    with (
        patch.object(
            Fetcher, "_fetch_following", side_effect=requests.HTTPError(response=resp)
        ),
        patch("webcam_aggregator.fetch.time.sleep"),
    ):
        assert f.get("https://e.example/x") is None
    assert s.drain() == {"e.example": {"http-500": 1}}


def test_get_with_outcome_says_why_a_fetch_failed() -> None:
    """A caller can tell "gone" (404) from "not for you right now" (401).

    `get` collapses both to None.
    """
    f = Fetcher(delay=0.0, retries=1)
    resp = requests.Response()
    resp.status_code = 404
    resp.url = "https://cdn.example/media_0.m3u8"
    with (
        patch.object(
            Fetcher, "_fetch_following", side_effect=requests.HTTPError(response=resp)
        ),
        patch("webcam_aggregator.fetch.time.sleep"),
    ):
        assert f.get_with_outcome(resp.url) == (None, "http-404")
    with patch.object(Fetcher, "_fetch_following", return_value="#EXTM3U\n"):
        assert f.get_with_outcome(resp.url) == ("#EXTM3U\n", "ok")


def test_failure_mid_redirect_attributed_to_the_failing_hop() -> None:
    """An HTTPError on hop 2 of a redirect chain counts against the host that failed.

    It is not counted against the URL the caller asked for; otherwise the "0 ok (11x
    http-403 ...)" line names the wrong host.
    """
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=s)
    resp = requests.Response()
    resp.status_code = 403
    resp.url = "https://cdn.example/blocked"
    with (
        patch.object(
            Fetcher, "_fetch_following", side_effect=requests.HTTPError(response=resp)
        ),
        patch("webcam_aggregator.fetch.time.sleep"),
    ):
        assert f.get("https://origin.example/x") is None
    assert s.drain() == {"cdn.example": {"http-403": 1}}


def test_success_records_ok() -> None:
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=s)
    with patch.object(Fetcher, "_fetch_following", return_value="body"):
        assert f.get("https://e.example/x") == "body"
    assert s.drain() == {"e.example": {"ok": 1}}


def test_politeness_delay_still_runs_on_non_retryable_failure() -> None:
    """A bot-wall serving redirect loops must NOT become unthrottled hammering.

    These paths hit time.sleep(delay) via the success return, and must keep doing so.
    """
    s = FetchStats()
    f = Fetcher(delay=1.0, retries=3, stats=s)
    with (
        patch.object(Fetcher, "_fetch_following", side_effect=_FetchError("too-large")),
        patch("webcam_aggregator.fetch.time.sleep") as slept,
    ):
        assert f.get("https://e.example/x") is None
    assert any(c.args and c.args[0] == 1.0 for c in slept.call_args_list)


def test_politeness_delay_runs_on_request_exception() -> None:
    s = FetchStats()
    f = Fetcher(delay=1.0, retries=1, stats=s)
    with (
        patch.object(
            Fetcher, "_fetch_following", side_effect=requests.ConnectionError()
        ),
        patch("webcam_aggregator.fetch.time.sleep") as slept,
    ):
        assert f.get("https://e.example/x") is None
    assert any(c.args and c.args[0] == 1.0 for c in slept.call_args_list)


def test_validate_ip_distinguishes_all_rejection_causes() -> None:
    assert _validate_ip("ftp://e.example/x") == (None, "bad-scheme")
    assert _validate_ip("https:///nohost") == (None, "bad-scheme")
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo", side_effect=socket.gaierror()
    ):
        assert _validate_ip("https://nx.example/x") == (None, "dns-error")
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("10.0.0.1"),
    ):
        assert _validate_ip("https://internal.example/x") == (None, "blocked-ip")
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("93.184.216.34"),
    ):
        assert _validate_ip("https://ok.example/x") == ("93.184.216.34", "ok")


def test_blocked_ip_is_recorded_and_not_retried() -> None:
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=3, stats=s)
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("127.0.0.1"),
    ) as resolver:
        assert f.get("https://internal.example/x") is None
    assert resolver.call_count == 1  # the verdict cannot change on a retry
    assert s.drain() == {"internal.example": {"blocked-ip": 1}}


def test_dns_failure_is_not_reported_as_a_security_block() -> None:
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=3, stats=s)
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo", side_effect=socket.gaierror()
    ):
        assert f.get("https://nx.example/x") is None
    assert s.drain() == {"nx.example": {"dns-error": 1}}


def test_post_is_instrumented() -> None:
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=s)
    with patch(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        return_value=_mock_getaddrinfo("127.0.0.1"),
    ):
        assert f.post_with_outcome("https://internal.example/ajax", {"a": "b"}) == (
            None,
            "blocked-ip",
        )
    assert s.drain() == {"internal.example": {"blocked-ip": 1}}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (requests.ConnectTimeout(), "timeout"),
        (requests.ReadTimeout(), "timeout"),
        (requests.TooManyRedirects(), "too-many-redirects"),
        (requests.ConnectionError(), "conn-error"),
    ],
)
def test_classify_maps_exceptions(exc: Exception, expected: str) -> None:
    assert _classify(exc) == expected


def test_classify_http_error_uses_status() -> None:
    resp = requests.Response()
    resp.status_code = 403
    assert _classify(requests.HTTPError(response=resp)) == "http-403"


def test_classify_http_error_without_response_falls_back() -> None:
    assert _classify(requests.HTTPError()) == "conn-error"


def test_scrape_workers_blank_means_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """docker-compose forwards `${SCRAPE_WORKERS:-}` as "" when it isn't in .env.

    That must take the default silently, not warn about a bad int.
    """
    import os as _os

    monkeypatch.setenv("SCRAPE_WORKERS", "")
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.fetch"):
        result = resolve_scrape_workers()
    assert result == min(16, (_os.cpu_count() or 2) * 4)
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Cookies — the serve-time fetcher is shared by every viewer, so it must not keep them
# ---------------------------------------------------------------------------


def _cookie_server() -> tuple[http.server.HTTPServer, threading.Thread, int]:
    """Serve the MediaMTX shape: /index 302s to /child setting a cookie.

    /child echoes the Cookie header it received (or "none").
    """

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/index":
                self.send_response(302)
                self.send_header("Location", "/child")
                self.send_header("Set-Cookie", "session=viewer-a; Path=/")
                self.end_headers()
                return
            body = (self.headers.get("Cookie") or "none").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(  # noqa: A002 # pyright: ignore[reportImplicitOverride]
            self, format: str, *args: object
        ) -> None: ...

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, httpd.server_address[1]


@pytest.mark.parametrize("impersonate", [False, True])
@pytest.mark.parametrize(
    ("keep_cookies", "expected"), [(True, "session=viewer-a"), (False, "none")]
)
def test_fetcher_cookie_jar_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
    keep_cookies: bool,
    expected: str,
    impersonate: bool,
) -> None:
    # A cookie an upstream sets for one viewer's stream would otherwise ride along on
    # every later fetch to that host, for every viewer — MediaMTX keys its HLS session
    # on exactly such a cookie, so a second cam on the same edge clobbers the first.
    monkeypatch.setattr("webcam_aggregator.fetch._ip_is_unsafe", lambda _ip: False)
    httpd, thread, port = _cookie_server()
    try:
        f = Fetcher(
            delay=0.0, retries=1, keep_cookies=keep_cookies, impersonate=impersonate
        )
        # across the redirect hop, and across separate calls
        assert f.get(f"http://127.0.0.1:{port}/index") == expected
        assert f.get(f"http://127.0.0.1:{port}/child") == expected
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "path",
    [
        "http://b\u00fccher.test:{port}/x",  # urllib3 resolves xn--bcher-kva.test
        "http://%61bc.test:{port}/x",  # requests resolves abc.test
        "http://internal.test:{port}\\@public.test/x",  # urllib3 dials internal.test
    ],
)
def test_requests_transport_never_resolves_a_name_outside_the_pin(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """urllib3 does not always resolve the host urlsplit validated.

    Validation sees a public address; any other name resolves to the internal server
    here, so reaching it would be a rebinding/SSRF bypass. The pin must refuse every
    lookup it didn't validate.
    """
    httpd, thread, port = _cookie_server()  # the "internal" server, on 127.0.0.1
    hits: list[str] = []
    handler = httpd.RequestHandlerClass
    orig: Any = getattr(handler, "do_GET")

    def counting(self: Any) -> None:
        hits.append(self.path)
        orig(self)

    monkeypatch.setattr(handler, "do_GET", counting)

    def resolver(host: Any, port: Any = None, *_a: Any, **_k: Any) -> Any:
        validated = host in ("b\u00fccher.test", "%61bc.test", "public.test")
        ip = "93.184.216.34" if validated else "127.0.0.1"
        addr = (ip, int(port or 0))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", addr)]

    # Patch the resolver UNDER the pin, so the pinning wrapper stays in force.
    monkeypatch.setattr("webcam_aggregator.fetch._real_getaddrinfo", resolver)
    stats = FetchStats()
    try:
        f = Fetcher(delay=0.0, retries=1, stats=stats)
        assert f.get(path.format(port=port)) is None
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
    assert hits == []
    assert list(stats.drain().values()) == [{"conn-error": 1}]


@pytest.mark.parametrize("impersonate", [False, True])
@pytest.mark.parametrize("port", ["99999", "-1", "abc", "0"])
def test_a_bad_port_is_a_bad_scheme_not_a_crash(port: str, impersonate: bool) -> None:
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, impersonate=impersonate)
    assert f.get(f"http://cam.example:{port}/x") is None
    assert stats.drain() == {"cam.example": {"bad-scheme": 1}}


# ---------------------------------------------------------------------------
# impersonate=True — the curl_cffi transport. libcurl does its own DNS and never
# calls socket.getaddrinfo, so these use a hostname libcurl cannot resolve: a
# fetch that succeeds can only have dialled the IP Fetcher validated and pinned.
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned_server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[int, list[dict[str, str]]]]:
    """Plain-HTTP server on 127.0.0.1:0.

    Validation resolves every name to loopback except `private.test` (10.0.0.1, which
    stays blocked). Yields (port, the request headers it saw, names lowercased).
    """
    seen: list[dict[str, str]] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append({k.lower(): v for k, v in self.headers.items()})
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "/body")
                self.end_headers()
            elif self.path == "/to-private":
                self.send_response(302)
                self.send_header("Location", "http://private.test/secret")
                self.end_headers()
            elif self.path == "/shed":
                self.send_response(503)
                self.end_headers()
            else:
                body = b"x" * 50_000 if self.path == "/big" else b"pinned-ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            seen.append({k.lower(): v for k, v in self.headers.items()})
            n = int(self.headers.get("Content-Length") or 0)
            body = b"posted " + self.rfile.read(n)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(  # noqa: A002 # pyright: ignore[reportImplicitOverride]
            self, format: str, *args: object
        ) -> None: ...

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def addrinfo(host: str, *_a: object, **_k: object) -> list[tuple[object, ...]]:
        ip = "10.0.0.1" if host == "private.test" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    monkeypatch.setattr("webcam_aggregator.fetch.socket.getaddrinfo", addrinfo)
    # Loopback is the test server; every other unsafe range stays blocked.
    monkeypatch.setattr(
        "webcam_aggregator.fetch._ip_is_unsafe", lambda ip: ip != "127.0.0.1"
    )
    try:
        yield port, seen
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_impersonating_fetcher_dials_the_pinned_ip(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    port, seen = pinned_server
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, impersonate=True)
    assert f.get(f"http://pinned.test:{port}/page") == "pinned-ok"
    assert stats.drain() == {"pinned.test": {"ok": 1}}
    # The browser profile's own User-Agent, not the requests one it would contradict.
    assert seen[0]["user-agent"] != UA
    assert "Chrome/" in seen[0]["user-agent"]


def test_impersonating_fetcher_validates_every_redirect_hop(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    port, seen = pinned_server
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, impersonate=True)
    assert f.get(f"http://pinned.test:{port}/hop") == "pinned-ok"
    assert len(seen) == 2  # Fetcher followed the 302 itself, not libcurl
    # A hop into a private range is refused before any request is made.
    assert f.get(f"http://pinned.test:{port}/to-private") is None
    assert stats.drain()["private.test"] == {"blocked-ip": 1}
    assert len(seen) == 3


def test_impersonating_fetcher_enforces_the_byte_cap(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    port, _ = pinned_server
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, byte_cap=10_000, impersonate=True)
    assert f.get(f"http://pinned.test:{port}/big") is None
    assert stats.drain() == {"pinned.test": {"too-large": 1}}


def test_impersonating_fetcher_feeds_shedding_to_the_pacer(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    port, _ = pinned_server
    pacer = HostPacer(sleep=lambda _s: None)
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, pacer=pacer, impersonate=True)
    assert f.get(f"http://pinned.test:{port}/shed") is None
    assert stats.drain() == {"pinned.test": {"http-503": 1}}
    assert pacer.drain()["pinned.test"]["penalties"] == 1


def test_impersonating_fetcher_dials_the_pin_whatever_libcurl_makes_of_the_host(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    """Urlsplit reads `bücher.test`; libcurl IDNA-encodes it to `xn--bcher-kva.test`.

    A pin keyed by urlsplit's host would miss and let libcurl resolve the name itself
    (here: fail; in the wild: a rebinding window). Every connection must go to the
    validated IP regardless.
    """
    port, _ = pinned_server
    f = Fetcher(delay=0.0, retries=1, impersonate=True)
    assert f.get(f"http://b\u00fccher.test:{port}/page") == "pinned-ok"


def test_impersonating_fetcher_refuses_a_response_from_an_unpinned_ip(
    pinned_server: tuple[int, list[dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backstop for a parser difference the wildcard cannot cover.

    The RESOLVE entry still keys on the port: libcurl reporting any IP but the validated
    one is a blocked fetch, whatever came back.
    """
    port, _ = pinned_server
    real = curl_requests.Session.request

    def elsewhere(self: Any, *a: Any, **k: Any) -> Any:
        resp = real(self, *a, **k)
        resp.primary_ip = "10.0.0.9"
        return resp

    monkeypatch.setattr(curl_requests.Session, "request", elsewhere)
    stats = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=stats, impersonate=True)
    assert f.get(f"http://pinned.test:{port}/page") is None
    assert stats.drain() == {"pinned.test": {"blocked-ip": 1}}


def test_impersonating_fetcher_accepts_a_reused_connection_to_an_earlier_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-robin DNS: a second validated IP is served, not thrown away as blocked-ip.

    The second validation of a host picks a different IP, and libcurl reuses its
    keep-alive connection to the first. That IP was validated, so the page is served.
    """
    servers: list[http.server.ThreadingHTTPServer] = []
    for ip in ("127.0.0.1", "127.0.0.2"):

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version: str = "HTTP/1.1"  # keep-alive, so libcurl can reuse
            body: bytes = ip.encode()

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", str(len(self.body)))
                self.end_headers()
                self.wfile.write(self.body)

            def log_message(  # noqa: A002 # pyright: ignore[reportImplicitOverride]
                self, format: str, *args: object
            ) -> None: ...

        port = servers[0].server_address[1] if servers else 0
        server = http.server.ThreadingHTTPServer((ip, port), _H)
        server.daemon_threads = True  # a held keep-alive must not block shutdown
        servers.append(server)
    port = servers[0].server_address[1]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads:
        t.start()
    answer = ["127.0.0.1"]

    def resolver(*_a: Any, **_k: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer[0], 0))]

    monkeypatch.setattr("webcam_aggregator.fetch.socket.getaddrinfo", resolver)
    monkeypatch.setattr("webcam_aggregator.fetch._ip_is_unsafe", lambda _ip: False)
    stats = FetchStats()
    try:
        f = Fetcher(delay=0.0, retries=1, stats=stats, impersonate=True)
        url = f"http://rr.test:{port}/"
        assert f.get(url) == "127.0.0.1"
        answer[0] = "127.0.0.2"
        assert f.get(url) in ("127.0.0.1", "127.0.0.2")
        assert stats.drain() == {"rr.test": {"ok": 2}}
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def test_impersonating_session_aborts_the_transfer_past_its_limit(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    """The transport stops reading at its limit (plus the chunk that crossed it).

    The Fetcher's own cap then refuses the body.
    """
    port, _ = pinned_server
    with _PinDNS("pinned.test", "127.0.0.1"):
        resp = _ImpersonatingSession(1000, keep_cookies=True).get(
            f"http://pinned.test:{port}/big", timeout=5
        )
    body = b"".join(resp.iter_content(65536))
    assert 1000 < len(body) < 50_000


def test_impersonating_fetcher_posts(
    pinned_server: tuple[int, list[dict[str, str]]],
) -> None:
    port, seen = pinned_server
    f = Fetcher(delay=0.0, retries=1, impersonate=True)
    assert f.post_with_outcome(f"http://pinned.test:{port}/form", {"a": "b"}) == (
        "posted a=b",
        "ok",
    )
    assert seen[-1]["content-type"] == "application/x-www-form-urlencoded"


def test_impersonating_get_segment_survives_a_pin_failure(
    pinned_server: tuple[int, list[dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, _ = pinned_server
    real = curl_requests.Session.request

    def elsewhere(self: Any, *a: Any, **k: Any) -> Any:
        resp = real(self, *a, **k)
        resp.primary_ip = "10.0.0.9"
        return resp

    monkeypatch.setattr(curl_requests.Session, "request", elsewhere)
    f = Fetcher(delay=0.0, retries=1, impersonate=True)
    assert f.get_segment(f"http://pinned.test:{port}/page") == (None, "blocked-ip")


def test_impersonating_session_refuses_an_unpinned_host() -> None:
    """The transport refuses a request made outside a _PinDNS block.

    Outside one, libcurl would resolve the name itself, reopening the rebinding window,
    so the transport refuses before sending anything.
    """
    with pytest.raises(RuntimeError):
        _ImpersonatingSession(MAX_BYTES, keep_cookies=True).get(
            "http://pinned.test/", timeout=1
        )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (curl_requests.exceptions.ConnectTimeout("t"), "timeout"),
        (curl_requests.exceptions.Timeout("t"), "timeout"),
        (curl_requests.exceptions.SSLError("t"), "conn-error"),
        (curl_requests.exceptions.ConnectionError("t"), "conn-error"),
    ],
)
def test_impersonating_errors_classify_like_requests_ones(
    exc: curl_requests.exceptions.RequestException, expected: str
) -> None:
    mapped = _as_requests_error(exc, "GET", "https://cam.example/x")
    assert _classify(mapped) == expected
    assert getattr(mapped.request, "url", None) == "https://cam.example/x"


def test_thread_imap_streams_in_order_without_running_ahead() -> None:
    """Results arrive in input order, and a slow consumer holds back the work.

    A consumer that hasn't taken the next result yet holds back the work: a scrape must
    not buffer every page body of a large sitemap before the first is parsed.
    """
    started: list[int] = []
    lock = threading.Lock()

    def work(i: int) -> int:
        with lock:
            started.append(i)
        return i * 10

    results = thread_imap(work, list(range(200)), workers=2)
    assert next(results) == 0
    time.sleep(0.2)  # ample time for an unbounded pool to finish all 200
    assert len(started) < 50
    assert list(results) == [i * 10 for i in range(1, 200)]
