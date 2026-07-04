"""Handler routing branch tests — all offline, no network calls."""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from typing import Any

from webcam_aggregator.app import CatalogueStore, make_handler
from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.catalogue import build_catalogue
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.models import Candidate
from webcam_aggregator.signing import sign

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

_CHILD_MANIFEST = "#EXTM3U\nseg_a.ts\n"
_CDN_MANIFEST = "#EXTM3U\nchunk.m3u8\nseg_b.ts\n"
_RESOLVED_URL = "https://cdn.example/cam/playlist.m3u8"


class _FakeSource:
    name: str = "fake"

    def discover(self):  # type: ignore[override]
        yield _CANDIDATE


def _make_store(ready: bool = True) -> tuple[CatalogueStore, str]:
    store = CatalogueStore()
    if ready:
        entries = build_catalogue(
            [_FakeSource()],
            is_alive=lambda c: True,
            youtube_live=lambda ids: {i: i for i in ids},
            history={},
        )
        store.swap(entries)
        entry_id = list(store.snapshot().keys())[0]
    else:
        entry_id = "irrelevant"
    return store, entry_id


def _make_cache() -> ResolveCache:
    def resolve(_entry_id: str, _target_url: str) -> Resolved:
        return Resolved(url=_RESOLVED_URL, stream_type="hls", ttl_seconds=3600)

    return ResolveCache(resolve, clock=time.monotonic)


def _manifest_fetch(url: str) -> str | None:
    if url == _RESOLVED_URL:
        return _CDN_MANIFEST
    # Child manifest fetch
    if "chunk.m3u8" in url:
        return _CHILD_MANIFEST
    return None


def _segment_fetch(
    _url: str, _range_header: str | None
) -> tuple[int, str, str | None, bytes] | None:
    return (200, "video/mp2t", None, b"fakesegment")


def _healthy_status(name: str = "fake", kept: int = 1) -> dict[str, Any]:
    return {
        "sources": {name: {"kept": kept, "discovered": kept, "crashed": False}},
        "unhealthy": [],
    }


def _start_server(
    store: CatalogueStore,
    cache: ResolveCache | None = None,
    source_status: dict[str, Any] | None = None,
) -> tuple[ThreadingHTTPServer, int]:
    if cache is None:
        cache = _make_cache()
    ss = source_status or _healthy_status()
    handler_cls = make_handler(
        store,
        cache,
        _BASE_URL,
        _manifest_fetch,
        source_status=lambda: ss,
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
    store, _ = _make_store()
    server, port = _start_server(store)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/nope")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()
    finally:
        server.shutdown()


def test_playlist_not_ready_returns_503() -> None:
    """CatalogueStore with ready=False → 503 on /playlist.m3u8."""
    store, _ = _make_store(ready=False)
    server, port = _start_server(store)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/playlist.m3u8")
        resp = conn.getresponse()
        assert resp.status == 503
        conn.close()
    finally:
        server.shutdown()


def test_child_manifest_missing_sig_returns_403() -> None:
    store, entry_id = _make_store()
    server, port = _start_server(store)
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
    store, entry_id = _make_store()
    server, port = _start_server(store)
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
    store, entry_id = _make_store()
    server, port = _start_server(store)
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
    store, entry_id = _make_store()
    server, port = _start_server(store)
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
    store, entry_id = _make_store()
    server, port = _start_server(store)
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
    store, _ = _make_store()
    server, port = _start_server(store, source_status=_healthy_status("fake", kept=3))
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
    finally:
        server.shutdown()


def test_health_unhealthy_when_source_failed() -> None:
    """A source that crashed / returned 0 this cycle flips healthy False and is named,
    even though the store is ready (the empty-guard is still masking it in the playlist).
    """
    store, _ = _make_store()  # ready=True
    status = {
        "sources": {"fake": {"kept": 0, "discovered": 0, "crashed": True}},
        "unhealthy": ["fake"],
    }
    server, port = _start_server(store, source_status=status)
    try:
        health = _get_health(port)
        assert health["ready"] is True
        assert health["healthy"] is False
        assert health["unhealthy_sources"] == ["fake"]
    finally:
        server.shutdown()


def test_health_not_healthy_during_cold_start() -> None:
    """Before the first build completes (not ready), healthy is False even with no
    failing sources — guards against a fresh container reporting a false all-clear."""
    store, _ = _make_store(ready=False)
    server, port = _start_server(store, source_status=_healthy_status())
    try:
        health = _get_health(port)
        assert health["ready"] is False
        assert health["healthy"] is False
    finally:
        server.shutdown()
