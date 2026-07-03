from __future__ import annotations

import logging
import socket
import time
from unittest.mock import patch

import pytest

from webcam_aggregator.fetch import (
    Fetcher,
    _pin,  # pyright: ignore[reportPrivateUsage]
    _PinDNS,  # pyright: ignore[reportPrivateUsage]
    _referer_for,  # pyright: ignore[reportPrivateUsage]
    _resolve_validated_ip,  # pyright: ignore[reportPrivateUsage]
    resolve_scrape_workers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_getaddrinfo(ip: str):
    """Return a monkeypatch for socket.getaddrinfo that resolves to a single IP."""
    return [(None, None, None, None, (ip, 0))]


def _url_is_safe(url: str) -> bool:
    """Test predicate over the real gate: True iff the URL resolves to a non-private
    IP (i.e. the Fetcher would be allowed to fetch it)."""
    return _resolve_validated_ip(url) is not None


# ---------------------------------------------------------------------------
# URL safety (_resolve_validated_ip) — private/reserved addresses must be blocked
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
    """get_segment must return None for private/unsafe URLs without making a request."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("127.0.0.1"),
    )
    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("http://127.0.0.1/seg.ts") is None


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
    result = f.get_segment(
        "https://cdn.balticlivecam.com/seg.ts", range_header="bytes=0-65535"
    )
    assert result is not None
    status, ct, cr, body = result
    assert status == 206
    assert ct == "video/mp2t"
    assert cr == "bytes 0-65535/1000000"
    assert body == b"helloworld"


def test_get_segment_refuses_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_segment must return None when the segment URL redirects."""
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
    assert f.get_segment("https://cdn.balticlivecam.com/seg.ts") is None


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


def test_get_segment_request_exception_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RequestException in get_segment → None."""
    import requests

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    monkeypatch.setattr(
        "requests.Session.get",
        lambda _self, _url, **_k: (_ for _ in ()).throw(
            requests.RequestException("timeout")
        ),
    )
    f = Fetcher(delay=0.0, retries=1)
    assert f.get_segment("https://cdn.example/seg.ts") is None


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

        def raise_for_status(self) -> None: ...

        def iter_content(self, _n: int) -> object:
            # Return one chunk larger than MAX_BYTES
            return iter([b"x" * (MAX_BYTES + 1)])

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _BigResp())
    f = Fetcher(delay=0.0, retries=1)
    assert f.get("https://cdn.example/big.m3u8") is None


def test_get_respects_custom_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised byte_cap accepts a body the default cap would reject — so the manifest
    fetcher can pull large DVR playlists."""
    from webcam_aggregator.fetch import MAX_BYTES

    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    big = b"#EXTM3U\n" + b"x" * MAX_BYTES  # just over the default 8 MB ceiling

    class _BigResp:
        is_redirect: bool = False
        is_permanent_redirect: bool = False

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


def test_get_segment_oversized_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Segment exceeding SEGMENT_MAX_BYTES ceiling → get_segment returns None."""
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
    assert f.get_segment("https://cdn.example/seg.ts") is None


# ---------------------------------------------------------------------------
# Fetcher.post
# ---------------------------------------------------------------------------


def test_post_blocks_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """post must return None for private/loopback URLs without calling the session."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("127.0.0.1"),
    )
    called = [False]

    def fake_post(_self: object, _url: str, **_k: object) -> None:
        called[0] = True

    monkeypatch.setattr("requests.Session.post", fake_post)
    f = Fetcher(delay=0.0, retries=1)
    assert f.post("http://127.0.0.1/admin-ajax.php", {"action": "auth_token"}) is None
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
    result = f.post(
        "https://example.com/wp-admin/admin-ajax.php",
        {"action": "auth_token", "id": "42"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert result == "response body"
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
    f.post(
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
        f.post("https://example.com/wp-admin/admin-ajax.php", {"action": "x"}) is None
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


def test_resolve_validated_ip_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any private/loopback resolved IP poisons the host → no IP returned."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("10.0.0.1"),
    )
    assert _resolve_validated_ip("http://internal.example/") is None


def test_resolve_validated_ip_returns_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """A safe public host resolves to a concrete IP that the caller will pin to."""
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: _mock_getaddrinfo("93.184.216.34"),
    )
    assert _resolve_validated_ip("https://example.com/x") == "93.184.216.34"


def test_pin_dns_pins_getaddrinfo_then_clears() -> None:
    """Inside `with _PinDNS(host, ip)`, getaddrinfo resolves the host to the pinned
    IP (so the socket dials it); the hostname stays in the URL, so urllib3 still does
    SNI/Host/cert against it. The pin is cleared on exit. Scheme-agnostic by design."""
    host = "cam.example"
    with _PinDNS(host, "203.0.113.7"):
        infos = socket.getaddrinfo(host, 443)
        assert any(info[4][0] == "203.0.113.7" for info in infos)
    assert not getattr(_pin, "map", {})  # pin cleared on exit


class _PinStubResp:
    is_redirect: bool = False
    is_permanent_redirect: bool = False

    def raise_for_status(self) -> None: ...

    def iter_content(self, _n: int) -> object:
        return iter([b"#EXTM3U"])

    def close(self) -> None: ...


def test_fetcher_pins_to_validated_ip_not_a_second_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebinding simulation: the resolver returns a PUBLIC IP at validation time;
    the connection must be pinned to THAT ip (host kept for SNI/Host) via _PinDNS,
    never a fresh lookup at connect time."""
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
    """End-to-end proof (offline, real sockets): stand up an HTTPS server on
    127.0.0.1:0 with a self-signed cert for hostname 'pinned.test'. Resolver maps
    'pinned.test' -> 127.0.0.1. The Fetcher must connect to the pinned IP yet
    complete the TLS handshake by validating the cert AGAINST THE HOSTNAME.

    This is the regression guard for the hard requirement: connecting to the IP
    must not weaken TLS. If cert validation were done against the IP (or disabled),
    this fetch would fail."""
    import http.server
    import ssl
    import tempfile
    import threading

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
        # _resolve_validated_ip would normally reject loopback; bypass ONLY the IP-safety
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
