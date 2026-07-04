from __future__ import annotations

import http.client
import json
import time
from collections.abc import Iterable, Iterator
from http.server import ThreadingHTTPServer

from webcam_aggregator.app import CatalogueStore, make_handler
from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.catalogue import build_catalogue
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.models import Candidate

# ---------------------------------------------------------------------------
# Fake source
# ---------------------------------------------------------------------------

_YT_VID = "aaaaaaaaaaa"
_YT_URL = f"https://www.youtube.com/watch?v={_YT_VID}"

_CANDIDATE = Candidate(
    title="Cam A",
    angle_key=None,
    category="Beaches",
    source="fake",
    source_page_url="https://fake.example/cam/1",
    target_url=_YT_URL,
    predisc_key=f"yt:{_YT_VID}",
)


class FakeSource:
    name: str = "fake"

    def discover(self) -> Iterator[Candidate]:
        yield _CANDIDATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMALL_MANIFEST = "#EXTM3U\nchunklist_w1.m3u8\nseg_1.ts\n"
_BASE_URL = "http://localhost"


def _fixed_resolved() -> Resolved:
    return Resolved(
        url="https://cdn.x/cam/playlist.m3u8", stream_type="hls", ttl_seconds=3600
    )


def _make_cache() -> ResolveCache:
    def resolve(_entry_id: str, _target_url: str) -> Resolved:
        return _fixed_resolved()

    return ResolveCache(resolve, clock=time.monotonic)


def _manifest_fetch(_url: str) -> str | None:
    return _SMALL_MANIFEST


def _youtube_live(ids: Iterable[str]) -> dict[str, str]:
    return {i: i for i in ids}


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


def test_end_to_end_server() -> None:
    # Build catalogue
    store = CatalogueStore()
    cache = _make_cache()

    entries = build_catalogue(
        [FakeSource()],
        is_alive=lambda c: True,
        youtube_live=_youtube_live,
        history={},
    )
    store.swap(entries)

    assert len(entries) == 1, f"expected 1 entry, got {len(entries)}"
    entry_id = list(store.snapshot().keys())[0]

    # Start server on port 0 (OS assigns free port)
    handler_cls = make_handler(
        store,
        cache,
        _BASE_URL,
        _manifest_fetch,
        source_status=lambda: {
            "sources": {"youtube-api": {"kept": 1, "discovered": 1, "crashed": False}},
            "unhealthy": [],
        },
        segment_fetch=lambda u, r: (200, "video/mp2t", None, b"seg"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]

    import threading

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        # --- /playlist.m3u8 → 200 with group-title and /stream/ URL ---
        conn.request("GET", "/playlist.m3u8")
        resp = conn.getresponse()
        assert resp.status == 200, f"playlist status={resp.status}"
        playlist_body = resp.read().decode()
        assert "/stream/" in playlist_body, f"no /stream/ in playlist:\n{playlist_body}"
        assert (
            'group-title="Beaches"' in playlist_body
        ), f'no group-title="Beaches" in playlist:\n{playlist_body}'

        # --- /stream/<id> → 200 rewritten manifest ---
        conn.request("GET", f"/stream/{entry_id}")
        resp = conn.getresponse()
        assert resp.status == 200, f"/stream status={resp.status}"
        stream_body = resp.read().decode()
        # The chunklist_w1.m3u8 (child manifest) must be proxied via /stream/<id>/m?u=
        assert (
            f"/stream/{entry_id}/m?u=" in stream_body
        ), f"no child-manifest proxy in stream body:\n{stream_body}"
        # The segment must be an absolute URL pointing at the CDN
        assert (
            "https://cdn.x/cam/seg_1.ts" in stream_body
        ), f"no absolute segment URL in stream body:\n{stream_body}"

        # --- /health → 200 JSON with streams: 1 ---
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200, f"/health status={resp.status}"
        health = json.loads(resp.read())
        assert health["streams"] == 1, f"expected streams=1, got {health}"
        assert health["ready"] is True
        assert health["healthy"] is True, f"expected healthy, got {health}"
        assert health["unhealthy_sources"] == [], f"bad unhealthy: {health}"
        assert health["sources"]["youtube-api"]["kept"] == 1, f"bad sources: {health}"

        conn.close()
    finally:
        server.shutdown()
