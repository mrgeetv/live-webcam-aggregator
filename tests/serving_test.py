from __future__ import annotations

from urllib.parse import quote

from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.models import CatalogueEntry
from webcam_aggregator.serving import (
    rewrite_manifest,
    render_playlist,
    serve_segment,
    serve_stream,
    serve_child_manifest,
    truncate_to_live_edge,
)
from webcam_aggregator.signing import sign

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "https://cams.example"
ENTRY_ID = "ab12"


def _entry(
    entry_id: str = ENTRY_ID,
    title: str = "Miami",
    category: str = "Beaches",
    target_url: str = "https://origin.example/cam/playlist.m3u8",
) -> CatalogueEntry:
    return CatalogueEntry(
        id=entry_id,
        title=title,
        category=category,
        source="test",
        source_page_url="https://origin.example/cam",
        target_url=target_url,
    )


def _make_cache(resolved: Resolved | None) -> ResolveCache:
    """Real ResolveCache with an injected resolver returning a fixed Resolved."""

    def _resolve(_entry_id: str, _target_url: str) -> Resolved:
        if resolved is None:
            raise RuntimeError("resolve failed")
        return resolved

    return ResolveCache(_resolve, clock=lambda: 0.0)


# ---------------------------------------------------------------------------
# 1. render_playlist
# ---------------------------------------------------------------------------


def test_render_playlist_extinf_line() -> None:
    entries = [_entry()]
    out = render_playlist(entries, base_url=BASE)
    assert (
        f'#EXTINF:-1 tvg-id="{ENTRY_ID}" tvg-name="Miami" group-title="Beaches",Miami'
        in out
    )


def test_render_playlist_tvg_id_equals_stream_id() -> None:
    """tvg-id must equal the /stream/<id> id so players keep favourites linked to the
    right cam across rebuilds (the count/order changing must not re-link them)."""
    out = render_playlist([_entry()], base_url=BASE)
    assert f'tvg-id="{ENTRY_ID}"' in out
    assert f"{BASE}/stream/{ENTRY_ID}" in out


def test_render_playlist_stream_url() -> None:
    entries = [_entry()]
    out = render_playlist(entries, base_url=BASE)
    assert f"{BASE}/stream/{ENTRY_ID}" in out


# ---------------------------------------------------------------------------
# 2. rewrite_manifest — YouTube master (absolute variant URLs → .m3u8)
# ---------------------------------------------------------------------------

YT_UPSTREAM = "https://manifest.googlevideo.com/api/manifest/hls_playlist/master.m3u8"

YT_MASTER = """\
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=500000
https://manifest.googlevideo.com/api/manifest/hls_variant/itag/234/hls_variant/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000
https://manifest.googlevideo.com/api/manifest/hls_variant/itag/299/hls_variant/index.m3u8
"""

YT_VARIANT_1 = "https://manifest.googlevideo.com/api/manifest/hls_variant/itag/234/hls_variant/index.m3u8"
YT_VARIANT_2 = "https://manifest.googlevideo.com/api/manifest/hls_variant/itag/299/hls_variant/index.m3u8"


def test_rewrite_manifest_yt_master_variant_lines_become_proxy_urls() -> None:
    out = rewrite_manifest(
        YT_MASTER, upstream_url=YT_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    expected_1 = f"{BASE}/stream/{ENTRY_ID}/m?u={quote(YT_VARIANT_1, safe='')}&sig="
    expected_2 = f"{BASE}/stream/{ENTRY_ID}/m?u={quote(YT_VARIANT_2, safe='')}&sig="
    assert expected_1 in out
    assert expected_2 in out


def test_rewrite_manifest_yt_master_comment_lines_unchanged() -> None:
    out = rewrite_manifest(
        YT_MASTER, upstream_url=YT_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert "#EXTM3U" in out
    assert "#EXT-X-VERSION:3" in out
    assert "#EXT-X-STREAM-INF:BANDWIDTH=500000" in out


def test_rewrite_manifest_yt_master_raw_upstream_urls_absent() -> None:
    """Original absolute URLs must no longer appear as bare non-comment lines."""
    out = rewrite_manifest(
        YT_MASTER, upstream_url=YT_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    for line in lines:
        assert line.startswith(f"{BASE}/stream/"), f"unexpected line: {line!r}"


# ---------------------------------------------------------------------------
# 3. rewrite_manifest — Wowza media (relative refs)
# ---------------------------------------------------------------------------

WOWZA_UPSTREAM = "https://cdn.x/cam/playlist.m3u8"

WOWZA_MEDIA = """\
#EXTM3U
chunklist_w1.m3u8
media_1.ts
"""


def test_rewrite_manifest_wowza_child_m3u8_becomes_proxy_url() -> None:
    absolute_child = "https://cdn.x/cam/chunklist_w1.m3u8"
    expected = f"{BASE}/stream/{ENTRY_ID}/m?u={quote(absolute_child, safe='')}&sig="
    out = rewrite_manifest(
        WOWZA_MEDIA, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert expected in out


def test_rewrite_manifest_wowza_segment_becomes_absolute_direct() -> None:
    absolute_seg = "https://cdn.x/cam/media_1.ts"
    out = rewrite_manifest(
        WOWZA_MEDIA, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert absolute_seg in out
    # Must NOT be routed through us
    assert f"{BASE}/stream/" not in out.replace(
        f"{BASE}/stream/{ENTRY_ID}/m?u=", ""
    ).replace(absolute_seg, "")


def test_rewrite_manifest_wowza_segment_not_proxied() -> None:
    """Segment URL must appear verbatim (absolute), not wrapped in our /stream/ proxy path."""
    out = rewrite_manifest(
        WOWZA_MEDIA, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    segments = [ln for ln in lines if not ln.startswith(f"{BASE}/stream/")]
    assert any("media_1.ts" in seg for seg in segments)


# ---------------------------------------------------------------------------
# 4. serve_stream
# ---------------------------------------------------------------------------


def test_serve_stream_unknown_id_returns_404() -> None:
    cache = _make_cache(Resolved(url="u", stream_type="hls", ttl_seconds=60))
    status, _, _ = serve_stream(
        "unknown",
        catalogue={},
        cache=cache,
        fetch=lambda u: None,
        base_url=BASE,
    )
    assert status == 404


def test_serve_stream_cache_miss_returns_502() -> None:
    entry = _entry()
    cache = _make_cache(None)  # resolver raises → negative cache → returns None
    status, _, _ = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: None,
        base_url=BASE,
    )
    assert status == 502


def test_serve_stream_non_hls_manifest_returns_502() -> None:
    # a DASH .mpd / XML / error body must not be served as HLS
    resolved = Resolved(
        url="https://x.example/manifest.mpd", stream_type="hls", ttl_seconds=60
    )
    cache = _make_cache(resolved)
    entry = _entry()
    status, _, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: "<?xml version='1.0'?><MPD></MPD>",
        base_url=BASE,
    )
    assert status == 502
    assert b"HLS" in body


def test_serve_stream_youtube_redirects_when_not_proxied() -> None:
    yt = "https://manifest.googlevideo.com/api/manifest/hls_playlist/x/index.m3u8"
    cache = _make_cache(Resolved(url=yt, stream_type="hls", ttl_seconds=60))
    status, loc, _ = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: _entry()},
        cache=cache,
        fetch=lambda _u: "should-not-be-fetched",
        base_url=BASE,
    )  # proxy_youtube defaults to False → 302 direct, no fetch
    assert status == 302
    assert loc == yt


def test_serve_stream_youtube_proxied_when_enabled() -> None:
    yt = "https://manifest.googlevideo.com/api/manifest/hls_playlist/x/index.m3u8"
    cache = _make_cache(Resolved(url=yt, stream_type="hls", ttl_seconds=60))
    status, ct, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: _entry()},
        cache=cache,
        fetch=lambda _u: "#EXTM3U\n#EXTINF:5.0,\nhttps://x.googlevideo.com/seg.ts\n",
        base_url=BASE,
        proxy_youtube=True,
    )
    assert status == 200
    assert "mpegurl" in ct
    assert "#EXTM3U" in body.decode()


def test_serve_stream_hls_returns_200_rewritten_body() -> None:
    manifest = "#EXTM3U\nrelative_chunk.m3u8\n"
    resolved = Resolved(
        url="https://cdn.x/cam/playlist.m3u8",
        stream_type="hls",
        ttl_seconds=60,
    )
    cache = _make_cache(resolved)
    entry = _entry(target_url="https://origin.example/cam/playlist.m3u8")
    status, ct, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: manifest,
        base_url=BASE,
    )
    assert status == 200
    assert "mpegurl" in ct
    text = body.decode()
    expected_proxied = f"{BASE}/stream/{ENTRY_ID}/m?u={quote('https://cdn.x/cam/relative_chunk.m3u8', safe='')}&sig="
    assert expected_proxied in text


def test_serve_stream_mp4_returns_302_with_location() -> None:
    mp4_url = "https://cdn.x/video.mp4"
    resolved = Resolved(url=mp4_url, stream_type="mp4", ttl_seconds=60)
    cache = _make_cache(resolved)
    entry = _entry()
    status, location, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: None,
        base_url=BASE,
    )
    assert status == 302
    assert location == mp4_url
    assert body == b""


def test_serve_stream_pixelcaster_hls_passthrough_302_no_proxy() -> None:
    # IP-bound session host: hand the player the original URL, never proxy/fetch it
    px_url = "https://cs9.pixelcaster.com/live/cam.stream/playlist.m3u8"
    resolved = Resolved(url=px_url, stream_type="hls", ttl_seconds=None)
    cache = _make_cache(resolved)
    entry = _entry()

    def _fail_fetch(_u: str) -> str | None:
        raise AssertionError("a direct-playback host must not be proxied/fetched")

    status, location, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=_fail_fetch,
        base_url=BASE,
    )
    assert status == 302
    assert location == px_url
    assert body == b""


# ---------------------------------------------------------------------------
# 5. serve_child_manifest
# ---------------------------------------------------------------------------


def test_serve_child_manifest_fetch_failure_returns_502() -> None:
    url = "https://cdn.x/cam/chunklist.m3u8"
    status, _, _ = serve_child_manifest(
        ENTRY_ID,
        url,
        sign(url),
        fetch=lambda u: None,
        base_url=BASE,
    )
    assert status == 502


def test_serve_child_manifest_returns_200_rewritten_body() -> None:
    media_manifest = "#EXTM3U\nseg_001.ts\n"
    upstream_url = "https://cdn.x/cam/chunklist.m3u8"
    status, ct, body = serve_child_manifest(
        ENTRY_ID,
        upstream_url,
        sign(upstream_url),
        fetch=lambda u: media_manifest,
        base_url=BASE,
    )
    assert status == 200
    assert "mpegurl" in ct
    text = body.decode()
    # Segment must be absolute direct (not proxied)
    assert "https://cdn.x/cam/seg_001.ts" in text
    assert f"{BASE}/stream/" not in text


# ---------------------------------------------------------------------------
# 6. serve_child_manifest — HMAC signature enforcement
# ---------------------------------------------------------------------------


def test_serve_child_manifest_valid_sig_returns_200() -> None:
    url = "https://cdn.x/cam/chunklist.m3u8"
    media_manifest = "#EXTM3U\nseg_001.ts\n"
    status, ct, _ = serve_child_manifest(
        ENTRY_ID,
        url,
        sign(url),
        fetch=lambda u: media_manifest,
        base_url=BASE,
    )
    assert status == 200
    assert "mpegurl" in ct


def test_serve_child_manifest_bad_sig_returns_403() -> None:
    url = "https://cdn.x/cam/chunklist.m3u8"
    status, _, body = serve_child_manifest(
        ENTRY_ID,
        url,
        "0" * 32,  # wrong sig
        fetch=lambda u: "#EXTM3U\n",
        base_url=BASE,
    )
    assert status == 403
    assert b"bad signature" in body


def test_serve_child_manifest_empty_sig_returns_403() -> None:
    url = "https://cdn.x/cam/chunklist.m3u8"
    status, _, body = serve_child_manifest(
        ENTRY_ID,
        url,
        "",  # missing/empty sig
        fetch=lambda u: "#EXTM3U\n",
        base_url=BASE,
    )
    assert status == 403
    assert b"bad signature" in body


def test_rewrite_manifest_output_contains_sig_param() -> None:
    """rewrite_manifest must append &sig= to every child-manifest proxy URL."""
    out = rewrite_manifest(
        YT_MASTER, upstream_url=YT_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    # Every non-comment, non-empty line must contain &sig=
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    for line in lines:
        assert "&sig=" in line, f"missing &sig= in: {line!r}"


# ---------------------------------------------------------------------------
# 7. rewrite_manifest — proxy_segments flag
# ---------------------------------------------------------------------------

WOWZA_WITH_SEGMENT = """\
#EXTM3U
chunklist_w1.m3u8
media_1.ts
"""

WOWZA_UPSTREAM_2 = "https://cdn.x/cam/playlist.m3u8"


def test_rewrite_manifest_proxy_segments_true_routes_ts_through_s() -> None:
    """With proxy_segments=True, .ts refs must become /stream/<id>/s?u=…&sig=…"""
    absolute_seg = "https://cdn.x/cam/media_1.ts"
    expected_prefix = (
        f"{BASE}/stream/{ENTRY_ID}/s?u={quote(absolute_seg, safe='')}&sig="
    )
    out = rewrite_manifest(
        WOWZA_WITH_SEGMENT,
        upstream_url=WOWZA_UPSTREAM_2,
        entry_id=ENTRY_ID,
        base_url=BASE,
        proxy_segments=True,
    )
    assert expected_prefix in out
    # The child .m3u8 must still go through /m
    expected_child_prefix = f"{BASE}/stream/{ENTRY_ID}/m?u="
    assert expected_child_prefix in out


def test_rewrite_manifest_proxy_segments_false_ts_stays_absolute() -> None:
    """With proxy_segments=False (default), segment refs must remain absolute."""
    absolute_seg = "https://cdn.x/cam/media_1.ts"
    out = rewrite_manifest(
        WOWZA_WITH_SEGMENT,
        upstream_url=WOWZA_UPSTREAM_2,
        entry_id=ENTRY_ID,
        base_url=BASE,
        proxy_segments=False,
    )
    assert absolute_seg in out
    assert f"{BASE}/stream/{ENTRY_ID}/s?" not in out


def test_rewrite_manifest_default_proxy_segments_same_as_false() -> None:
    """proxy_segments defaults to False — existing callers unaffected."""
    out_default = rewrite_manifest(
        WOWZA_WITH_SEGMENT,
        upstream_url=WOWZA_UPSTREAM_2,
        entry_id=ENTRY_ID,
        base_url=BASE,
    )
    out_false = rewrite_manifest(
        WOWZA_WITH_SEGMENT,
        upstream_url=WOWZA_UPSTREAM_2,
        entry_id=ENTRY_ID,
        base_url=BASE,
        proxy_segments=False,
    )
    assert out_default == out_false


# ---------------------------------------------------------------------------
# 8. serve_stream — baltic segments proxied through /s
# ---------------------------------------------------------------------------

BALTIC_MANIFEST = "#EXTM3U\nseg_001.ts\n"
BALTIC_URL = "https://edge01.balticlivecam.com/stream/cam/playlist.m3u8"


def test_serve_stream_baltic_segments_proxied_via_s() -> None:
    """Resolved URL on balticlivecam.com → segment lines become /stream/<id>/s?u=…"""
    resolved = Resolved(url=BALTIC_URL, stream_type="hls", ttl_seconds=60)
    cache = _make_cache(resolved)
    entry = _entry(target_url="https://balticlivecam.com/cam")

    status, ct, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: BALTIC_MANIFEST,
        base_url=BASE,
    )
    assert status == 200
    assert "mpegurl" in ct
    text = body.decode()
    absolute_seg = "https://edge01.balticlivecam.com/stream/cam/seg_001.ts"
    expected_prefix = (
        f"{BASE}/stream/{ENTRY_ID}/s?u={quote(absolute_seg, safe='')}&sig="
    )
    assert expected_prefix in text


def test_serve_stream_enhd_es_segments_proxied_via_s() -> None:
    """enhd.es resolves fine server-side but 403s the player's '+'-re-encoded
    segment fetch, so its segments must be relayed through /s."""
    enhd_url = "https://stream.enhd.es/live/+Cam.stream/chunklist.m3u8"
    manifest = (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10.0,\n"
        "https://stream.enhd.es/live/+Cam.stream/media_1.ts\n"
    )
    resolved = Resolved(url=enhd_url, stream_type="hls", ttl_seconds=60)
    cache = _make_cache(resolved)
    entry = _entry(target_url="https://en.in2thebeach.es/cam")

    status, _ct, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda u: manifest,
        base_url=BASE,
    )
    assert status == 200
    absolute_seg = "https://stream.enhd.es/live/+Cam.stream/media_1.ts"
    expected_prefix = (
        f"{BASE}/stream/{ENTRY_ID}/s?u={quote(absolute_seg, safe='')}&sig="
    )
    assert expected_prefix in body.decode()


# ---------------------------------------------------------------------------
# 9. serve_segment
# ---------------------------------------------------------------------------


def test_serve_segment_valid_sig_relays_result() -> None:
    url = "https://edge01.balticlivecam.com/stream/cam/seg_001.ts"
    sig = sign(url)

    def fake_fetch(u: str, _r: str | None) -> tuple[int, str, str | None, bytes] | None:
        assert u == url
        return (206, "video/mp2t", "bytes 0-10/100", b"data")

    status, ct, cr, body = serve_segment(
        ENTRY_ID, url, sig, fetch_segment=fake_fetch, range_header="bytes=0-10"
    )
    assert status == 206
    assert ct == "video/mp2t"
    assert cr == "bytes 0-10/100"
    assert body == b"data"


def test_serve_segment_bad_sig_returns_403() -> None:
    url = "https://edge01.balticlivecam.com/stream/cam/seg_001.ts"
    status, _, cr, body = serve_segment(
        ENTRY_ID,
        url,
        "0" * 32,
        fetch_segment=lambda u, r: (200, "video/mp2t", None, b"data"),
    )
    assert status == 403
    assert b"bad signature" in body
    assert cr is None


def test_serve_segment_empty_sig_returns_403() -> None:
    url = "https://edge01.balticlivecam.com/stream/cam/seg_001.ts"
    status, _, _cr, body = serve_segment(
        ENTRY_ID,
        url,
        "",
        fetch_segment=lambda u, r: (200, "video/mp2t", None, b"data"),
    )
    assert status == 403
    assert b"bad signature" in body


def test_serve_segment_fetch_failure_returns_502() -> None:
    url = "https://edge01.balticlivecam.com/stream/cam/seg_001.ts"
    sig = sign(url)
    status, _, cr, body = serve_segment(
        ENTRY_ID,
        url,
        sig,
        fetch_segment=lambda u, r: None,
    )
    assert status == 502
    assert b"segment fetch failed" in body
    assert cr is None


def test_rewrite_manifest_offsite_ref_passed_through_not_proxied() -> None:
    """Open-proxy guard: a child ref on a DIFFERENT site than the upstream must be
    passed through as-is, never signed/proxied through us. Same-site refs still are."""
    text = (
        "#EXTM3U\n"
        "https://evil.example/inject.m3u8\n"  # off-site → must NOT be proxied
        "child.m3u8\n"  # same-site relative to YT_UPSTREAM → proxied
    )
    out = rewrite_manifest(
        text, upstream_url=YT_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert "https://evil.example/inject.m3u8" in out  # passed through verbatim
    assert quote("https://evil.example/inject.m3u8", safe="") not in out  # not signed
    assert f"{BASE}/stream/{ENTRY_ID}/m?u=" in out  # same-site child IS proxied


# ---------------------------------------------------------------------------
# DVR live-edge truncation
# ---------------------------------------------------------------------------


def _dvr_playlist(n: int, target: int = 1, media_seq: int = 1000) -> str:
    head = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{target}\n"
        f"#EXT-X-MEDIA-SEQUENCE:{media_seq}\n"
        "#EXT-X-DISCONTINUITY-SEQUENCE:5\n"
    )
    segs = "".join(f"#EXTINF:{target}.0,\nhttps://cdn.x/seg_{i}.ts\n" for i in range(n))
    return head + segs


def test_truncate_dvr_keeps_live_edge() -> None:
    out = truncate_to_live_edge(
        _dvr_playlist(300, target=1, media_seq=1000), window=120.0
    )
    segs = [ln for ln in out.splitlines() if ln.endswith(".ts")]
    assert len(segs) == 120  # 120s window of 1s segments
    assert segs[-1] == "https://cdn.x/seg_299.ts"  # live edge kept
    assert segs[0] == "https://cdn.x/seg_180.ts"  # rewind buffer dropped
    assert "seg_179.ts" not in out
    assert "#EXT-X-MEDIA-SEQUENCE:1180" in out  # advanced by the 180 dropped


def test_truncate_short_playlist_unchanged() -> None:
    pl = _dvr_playlist(50, target=5)  # under the DVR threshold
    assert truncate_to_live_edge(pl) == pl  # returned verbatim


def test_truncate_master_unchanged() -> None:
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://cdn.x/v.m3u8\n"
    assert truncate_to_live_edge(master) == master


def test_serve_stream_truncates_dvr() -> None:
    resolved = Resolved(
        url="https://cdn.x/index.m3u8", stream_type="hls", ttl_seconds=60
    )
    cache = _make_cache(resolved)
    entry = _entry(target_url="https://cdn.x/cam")
    status, _ct, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: entry},
        cache=cache,
        fetch=lambda _u: _dvr_playlist(500, target=1),
        base_url=BASE,
    )
    assert status == 200
    segs = [ln for ln in body.decode().splitlines() if ln.endswith(".ts")]
    assert len(segs) == 120  # truncated from 500 to the live edge
