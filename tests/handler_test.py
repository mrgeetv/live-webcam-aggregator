"""Handler routing branch tests — all offline, no network calls."""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from webcam_aggregator.app import LiveCatalogue, make_handler, source_status_for
from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.catalogue import Scheduler
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.models import Candidate
from webcam_aggregator.signing import sign
from webcam_aggregator.store import open_storeless

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_YT_VID = "bbbbbbbbbbb"
_YT_URL = f"https://www.youtube.com/watch?v={_YT_VID}"
_BASE_URL = "http://localhost"

_CANDIDATE = Candidate(
    title="Test Cam",
    angle_key=None,
    category="Nature",
    source="fake",
    source_page_url="https://fake.example/cam/2",
    target_url=_YT_URL,
    predisc_key=f"yt:{_YT_VID}",
)

_CHILD_MANIFEST = "#EXTM3U\n#EXTINF:2.0,\nseg_a.ts\n"
_CDN_MANIFEST = "#EXTM3U\nchunk.m3u8\nseg_b.ts\n"
_RESOLVED_URL = "https://cdn.example/cam/playlist.m3u8"


class _FakeSource:
    name: str = "fake"
    interval_hours: float = 6

    def discover(self) -> Iterator[Candidate]:
        yield _CANDIDATE


class _Clock:
    now: float = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _scheduler(catalogue: LiveCatalogue, clock: _Clock) -> Scheduler:
    return Scheduler(
        [_FakeSource()],
        {"fake": 6},
        drop_reason_for=lambda _c: None,
        youtube_live=lambda ids: {i: i for i in ids},
        on_merge=catalogue.swap,
        store=open_storeless(),
        clock=clock,
    )


def _make_catalogue(*, ready: bool = True) -> tuple[LiveCatalogue, str]:
    catalogue = LiveCatalogue()
    if ready:
        s = _scheduler(catalogue, _Clock())
        s.tick()
        assert s.finish_next(timeout=5)
        entry_id = next(iter(catalogue.snapshot().keys()))
    else:
        entry_id = "irrelevant"
    return catalogue, entry_id


def _make_cache() -> ResolveCache:
    def resolve(_entry_id: str, _target_url: str) -> Resolved:
        return Resolved(url=_RESOLVED_URL, stream_type="hls", ttl_seconds=3600)

    return ResolveCache(resolve, clock=time.monotonic)


def _manifest_fetch(url: str) -> tuple[str | None, str]:
    if url == _RESOLVED_URL:
        return _CDN_MANIFEST, "ok"
    # Child manifest fetch
    if "chunk.m3u8" in url:
        return _CHILD_MANIFEST, "ok"
    return None, "http-404"


def _segment_fetch(
    _url: str, _range_header: str | None
) -> tuple[tuple[int, str, str | None, bytes] | None, str]:
    return (200, "video/mp2t", None, b"fakesegment"), "ok"


def _healthy_status(name: str = "fake", kept: int = 1) -> dict[str, Any]:
    return {
        "sources": {name: {"kept": kept, "discovered": kept, "crashed": False}},
        "unhealthy": [],
        "ready": True,
    }


def _start_server(
    catalogue: LiveCatalogue,
    cache: ResolveCache | None = None,
    source_status: dict[str, Any] | None = None,
    status_fn: Callable[[], dict[str, Any]] | None = None,
) -> tuple[ThreadingHTTPServer, int]:
    if cache is None:
        cache = _make_cache()
    ss = source_status or _healthy_status()
    handler_cls = make_handler(
        catalogue,
        cache,
        _BASE_URL,
        manifest_fetch=_manifest_fetch,
        source_status=status_fn or (lambda: ss),
        segment_fetch=_segment_fetch,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unknown_path_returns_404() -> None:
    catalogue, _ = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/nope")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()
    finally:
        server.shutdown()


def test_playlist_not_ready_returns_503() -> None:
    """A catalogue with no merge yet → 503 on /playlist.m3u8."""
    catalogue, _ = _make_catalogue(ready=False)
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/playlist.m3u8")
        resp = conn.getresponse()
        assert resp.status == 503
        conn.close()
    finally:
        server.shutdown()


def test_child_manifest_missing_sig_returns_403() -> None:
    catalogue, entry_id = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        encoded_url = urllib.parse.quote(_RESOLVED_URL, safe="")
        conn.request("GET", f"/stream/{entry_id}/m?u={encoded_url}")
        resp = conn.getresponse()
        assert resp.status == 403
        conn.close()
    finally:
        server.shutdown()


def test_child_manifest_missing_u_returns_400() -> None:
    catalogue, entry_id = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/stream/{entry_id}/m")
        resp = conn.getresponse()
        assert resp.status == 400
        conn.close()
    finally:
        server.shutdown()


def test_child_manifest_valid_sig_returns_200() -> None:
    """Valid sig on /stream/<id>/m → 200 with rewritten manifest."""
    catalogue, entry_id = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        child_url = "https://cdn.example/cam/chunk.m3u8"
        encoded_url = urllib.parse.quote(child_url, safe="")
        sig = sign(child_url)
        conn.request("GET", f"/stream/{entry_id}/m?u={encoded_url}&sig={sig}")
        resp = conn.getresponse()
        assert resp.status == 200, f"expected 200, got {resp.status}"
        body = resp.read().decode()
        assert "#EXTM3U" in body
        conn.close()
    finally:
        server.shutdown()


def test_segment_missing_sig_returns_403() -> None:
    catalogue, entry_id = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        encoded_url = urllib.parse.quote("https://cdn.example/cam/seg.ts", safe="")
        conn.request("GET", f"/stream/{entry_id}/s?u={encoded_url}")
        resp = conn.getresponse()
        assert resp.status == 403
        conn.close()
    finally:
        server.shutdown()


def test_segment_valid_sig_returns_segment() -> None:
    """Valid sig on /stream/<id>/s → relays the segment_fetch result."""
    catalogue, entry_id = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        seg_url = "https://cdn.example/cam/seg.ts"
        encoded_url = urllib.parse.quote(seg_url, safe="")
        sig = sign(seg_url)
        conn.request("GET", f"/stream/{entry_id}/s?u={encoded_url}&sig={sig}")
        resp = conn.getresponse()
        assert resp.status == 200, f"expected 200, got {resp.status}"
        body = resp.read()
        assert body == b"fakesegment"
        assert resp.getheader("Content-Type") == "video/mp2t"
        conn.close()
    finally:
        server.shutdown()


def _get_health(port: int) -> dict[str, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    assert resp.status == 200
    body = json.loads(resp.read())
    conn.close()
    return body


def test_health_returns_correct_json() -> None:
    catalogue, _ = _make_catalogue()
    server, port = _start_server(
        catalogue, source_status=_healthy_status("fake", kept=3)
    )
    try:
        health = _get_health(port)
        assert health["ready"] is True
        assert health["healthy"] is True
        assert health["unhealthy_sources"] == []
        assert isinstance(health["streams"], int)
        assert health["streams"] >= 1
        assert "rss_mb" in health
        assert health["sources"]["fake"] == {
            "kept": 3,
            "discovered": 3,
            "crashed": False,
        }
        # The exact payload shape — diagnostic detail belongs in the logs.
        assert set(health) == {
            "ready",
            "healthy",
            "streams",
            "unhealthy_sources",
            "sources",
            "rss_mb",
        }
    finally:
        server.shutdown()


def test_health_unhealthy_when_source_failed() -> None:
    """A source that crashed or returned 0 this cycle flips healthy to False.

    The source is named, even though the catalogue is serving (the empty-guard is still
    masking it in the playlist).
    """
    catalogue, _ = _make_catalogue()  # ready=True
    status = {
        "sources": {"fake": {"kept": 0, "discovered": 0, "crashed": True}},
        "unhealthy": ["fake"],
        "ready": True,
    }
    server, port = _start_server(catalogue, source_status=status)
    try:
        health = _get_health(port)
        assert health["ready"] is True
        assert health["healthy"] is False
        assert health["unhealthy_sources"] == ["fake"]
    finally:
        server.shutdown()


def test_health_not_healthy_during_cold_start() -> None:
    """Until every source has finished a refresh (not ready), healthy is False.

    That holds even with no failing sources, which guards against a fresh container
    reporting a false all-clear.
    """
    catalogue, _ = _make_catalogue()  # the playlist already serves the first refresh
    status = {**_healthy_status(), "ready": False}
    server, port = _start_server(catalogue, source_status=status)
    try:
        health = _get_health(port)
        assert health["ready"] is False
        assert health["healthy"] is False
    finally:
        server.shutdown()


def test_health_reports_each_sources_refresh() -> None:
    """The scheduler's per-source refresh times reach /health, and so does overdue."""
    clock = _Clock()
    catalogue = LiveCatalogue()
    s = _scheduler(catalogue, clock)
    server, port = _start_server(
        catalogue, source_status=None, status_fn=lambda: source_status_for(s, clock())
    )
    try:
        cold = _get_health(port)
        assert cold["ready"] is False
        assert cold["sources"]["fake"]["status"] == "unknown"
        assert cold["sources"]["fake"]["last_refresh_at"] is None
        assert cold["unhealthy_sources"] == []  # not run yet is not a failure

        s.tick()
        assert s.finish_next(timeout=5)
        clock.now = 1_000_000.0 + 7200  # two hours after the refresh
        health = _get_health(port)
        assert health["ready"] is True
        assert health["healthy"] is True
        assert health["sources"]["fake"] == {
            "kept": 1,
            "discovered": 1,
            "crashed": False,
            "status": "ok",
            "last_refresh_at": "1970-01-12T13:46:40Z",
            "last_refresh_seconds": 0.0,
            "interval_hours": 6.0,
        }

        clock.now = 1_000_000.0 + 12 * 3600 + 1  # just over 2x its 6h interval
        late = _get_health(port)
        assert late["sources"]["fake"]["status"] == "overdue"
        assert late["unhealthy_sources"] == ["fake"]
        assert late["healthy"] is False
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Access log — the uptime check polls /health every 60s (4k lines/day)
# ---------------------------------------------------------------------------


def _raw_get(port: int, path: str) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    conn.getresponse().read()
    conn.close()


def test_health_requests_are_not_access_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalogue, _ = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        with caplog.at_level(logging.DEBUG, logger="webcam-aggregator"):
            _get_health(port)
            _raw_get(port, "/playlist.m3u8")  # ordering barrier + positive control
    finally:
        server.shutdown()
    assert "/health" not in caplog.text
    assert "/playlist.m3u8" in caplog.text


def test_health_suppression_cannot_be_triggered_by_a_crafted_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raw-request-line substring test would let a client hide its own request."""
    catalogue, _ = _make_catalogue()
    server, port = _start_server(catalogue)
    try:
        with caplog.at_level(logging.DEBUG, logger="webcam-aggregator"):
            _raw_get(port, "/playlist.m3u8?x=/health")
    finally:
        server.shutdown()
    assert "/playlist.m3u8" in caplog.text
