from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from urllib.parse import quote

import pytest

from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.models import CatalogueEntry
from webcam_aggregator.serving import (
    _proxy_segments_for,  # pyright: ignore[reportPrivateUsage]
    is_playable_manifest,
    loggable,
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


def test_render_playlist_neutralises_scraped_line_breaks_and_quotes() -> None:
    """A scraped title must not be able to add a playlist line or end its attribute."""
    title = 'Owl  "cam"\r\nhttps://evil.example/x.m3u8\u2028more\x00'
    out = render_playlist([_entry(title=title, category="Ani\nmals")], base_url=BASE)
    lines = out.splitlines()
    assert len(lines) == 3  # header, #EXTINF, the stream URL: nothing injected
    assert lines[1] == (
        f'#EXTINF:-1 tvg-id="{ENTRY_ID}" '
        "tvg-name=\"Owl 'cam' https://evil.example/x.m3u8 more\" "
        "group-title=\"Ani mals\",Owl 'cam' https://evil.example/x.m3u8 more"
    )
    assert lines[2] == f"{BASE}/stream/{ENTRY_ID}"


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


def test_rewrite_manifest_rewrites_uri_attributes_on_tag_lines() -> None:
    """Wowza's separate audio rendition, an I-frame playlist, a key and an fMP4 init
    segment are referenced from URI="…" on a tag line; left relative, the player
    resolves them against /stream/<id> and gets a 404."""
    master = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="chunklist_w1_ao.m3u8"\n'
        '#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=1,URI="iframes.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="a"\n'
        "chunklist_w1_vo.m3u8\n"
    )
    out = rewrite_manifest(
        master, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    audio = quote("https://cdn.x/cam/chunklist_w1_ao.m3u8", safe="")
    assert f'URI="{BASE}/stream/{ENTRY_ID}/m?u={audio}&sig=' in out
    iframes = quote("https://cdn.x/cam/iframes.m3u8", safe="")
    assert f'BANDWIDTH=1,URI="{BASE}/stream/{ENTRY_ID}/m?u={iframes}&sig=' in out

    media = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:2,\nseg1.m4s\n"
    )
    out = rewrite_manifest(
        media, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.x/cam/key.bin"' in out
    assert '#EXT-X-MAP:URI="https://cdn.x/cam/init.mp4"' in out
    init = quote("https://cdn.x/cam/init.mp4", safe="")
    out = rewrite_manifest(
        media,
        upstream_url=WOWZA_UPSTREAM,
        entry_id=ENTRY_ID,
        base_url=BASE,
        proxy_segments=True,
    )
    assert f'#EXT-X-MAP:URI="{BASE}/stream/{ENTRY_ID}/s?u={init}&sig=' in out


def test_rewrite_manifest_rewrites_reference_attributes_not_named_uri() -> None:
    """Content steering's SERVER-URI, an interstitial's X-ASSET-LIST and a preload's
    X-URI may be relative too (RFC 8216bis)."""
    master = (
        "#EXTM3U\n"
        '#EXT-X-CONTENT-STEERING:SERVER-URI="steer.json",PATHWAY-ID="a"\n'
        '#EXT-X-DATERANGE:ID="ad",CLASS="com.apple.hls.interstitial",'
        'START-DATE="2020-01-01T00:00:00Z",X-ASSET-LIST="ads.json"\n'
        '#EXT-X-DATERANGE:ID="p",CLASS="com.apple.hls.preload",'
        'START-DATE="2020-01-01T00:00:00Z",X-URI="next.m3u8"\n'
    )
    out = rewrite_manifest(
        master, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert 'SERVER-URI="https://cdn.x/cam/steer.json",PATHWAY-ID="a"' in out
    assert 'X-ASSET-LIST="https://cdn.x/cam/ads.json"' in out
    preload = quote("https://cdn.x/cam/next.m3u8", safe="")
    assert f'X-URI="{BASE}/stream/{ENTRY_ID}/m?u={preload}&sig=' in out


def test_rewrite_manifest_leaves_malformed_attributes_alone() -> None:
    """Only a whole attribute with a quoted value is a reference: an unquoted value
    with a stray quote, and a name that merely ends in URI, pass through untouched."""
    media = '#EXTM3U\n#EXT-X-MAP:URI=init.mp4"\n#EXT-X-FOO:xURI="a.mp4"\n'
    out = rewrite_manifest(
        media, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert '#EXT-X-MAP:URI=init.mp4"\n' in out
    assert '#EXT-X-FOO:xURI="a.mp4"\n' in out


def test_rewrite_manifest_uri_rewrite_stays_inside_tag_attributes() -> None:
    """A URI="…" inside an #EXTINF title is text, and an unbalanced quote must not
    swallow the lines after it."""
    media = (
        "#EXTM3U\n"
        '#EXTINF:2,title URI="a.ts"\n'
        "seg1.ts\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="k1\n'
        '#EXT-X-MAP:URI="init.mp4"\n'
    )
    out = rewrite_manifest(
        media, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert '#EXTINF:2,title URI="a.ts"' in out
    assert '#EXT-X-MAP:URI="https://cdn.x/cam/init.mp4"' in out


def test_rewrite_manifest_reads_a_quoted_value_whole() -> None:
    """A rendition NAME is free text: `,URI="` inside it is not the URI attribute."""
    master = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,NAME="a,URI=",URI="ao.m3u8"\n'
    out = rewrite_manifest(
        master, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    audio = quote("https://cdn.x/cam/ao.m3u8", safe="")
    assert f'NAME="a,URI=",URI="{BASE}/stream/{ENTRY_ID}/m?u={audio}&sig=' in out


def test_rewrite_manifest_off_site_uri_attribute_is_not_proxied() -> None:
    master = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="https://evil.example/a.m3u8"\n'
    out = rewrite_manifest(
        master, upstream_url=WOWZA_UPSTREAM, entry_id=ENTRY_ID, base_url=BASE
    )
    assert 'URI="https://evil.example/a.m3u8"' in out


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


_EMPTY_STUB = (
    # What hd-auth.skylinewebcams.com returns for a lapsed/bogus token: HTTP 200,
    # well-formed HLS, and nothing in it. A #EXTM3U-only check reads this as live.
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-ALLOW-CACHE:NO\n"
    "#EXT-X-TARGETDURATION:0\n"
    "#EXT-X-MEDIA-SEQUENCE:0\n"
    "#EXT-X-ENDLIST\n"
)
_MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3.000,\nhttps://cdn.x/1.ts\n"
_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720\nhigh.m3u8\n"
)


def test_is_playable_manifest_needs_content_not_just_a_header() -> None:
    assert is_playable_manifest(_MEDIA)  # media playlist: has segments
    assert is_playable_manifest(_MASTER)  # master playlist: has variants
    # The whole point: an expired token's empty playlist must NOT read as live.
    assert not is_playable_manifest(_EMPTY_STUB)
    assert not is_playable_manifest("<?xml version='1.0'?><MPD></MPD>")  # DASH
    assert not is_playable_manifest("")
    assert not is_playable_manifest(None)


def test_serve_stream_empty_playlist_returns_502() -> None:
    """An expired token yields a 200 + empty playlist upstream; serving it would
    hand the player a stream it can never start."""
    resolved = Resolved(
        url="https://hd-auth.x/live.m3u8?a=stale", stream_type="hls", ttl_seconds=60
    )
    status, _, body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: _entry()},
        cache=_make_cache(resolved),
        fetch=lambda _u: _EMPTY_STUB,
        base_url=BASE,
    )
    assert status == 502
    assert b"playable" in body


def test_serve_stream_serves_a_master_playlist() -> None:
    """The stub check must not reject master playlists — they carry variants, not
    segments, and are what most CDN top-level URLs return."""
    resolved = Resolved(
        url="https://cdn.x/master.m3u8", stream_type="hls", ttl_seconds=60
    )
    status, _, _body = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: _entry()},
        cache=_make_cache(resolved),
        fetch=lambda _u: _MASTER,
        base_url=BASE,
    )
    assert status == 200


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


class _Clock:
    t: float = 0.0

    def __call__(self) -> float:
        return self.t


_YT_MANIFEST = "#EXTM3U\n#EXTINF:5.0,\nhttps://x.googlevideo.com/seg.ts\n"


class _YtResolver:
    """Each resolve mints a distinct googlevideo URL (~6h token, as real ones).
    Call numbers in `fail` raise; call number `hold` blocks until `release` is set."""

    calls: int
    fail: tuple[int, ...]
    hold: int | None
    started: threading.Event
    release: threading.Event

    def __init__(self, *, fail: tuple[int, ...] = (), hold: int | None = None) -> None:
        self.calls = 0
        self.fail = fail
        self.hold = hold
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, _entry_id: str, _target_url: str) -> Resolved:
        self.calls += 1
        n = self.calls
        if n == self.hold:
            self.started.set()
            self.release.wait(5)
        if n in self.fail:
            raise RuntimeError("yt-dlp failed")
        yt = f"https://manifest.googlevideo.com/api/manifest/hls_playlist/r{n}/index.m3u8"
        return Resolved(url=yt, stream_type="hls", ttl_seconds=21600)


def _youtube_cache(clock: _Clock, resolver: _YtResolver | None = None) -> ResolveCache:
    return ResolveCache(resolver or _YtResolver(), clock=clock)


def _play(
    cache: ResolveCache,
    *,
    proxy_youtube: bool = False,
    fetch: Callable[[str], str | None] = lambda _u: _YT_MANIFEST,
) -> str:
    _status, loc, _ = serve_stream(
        ENTRY_ID,
        catalogue={ENTRY_ID: _entry()},
        cache=cache,
        fetch=fetch,
        base_url=BASE,
        proxy_youtube=proxy_youtube,
    )
    return loc


def test_serve_stream_youtube_retry_gets_fresh_resolve() -> None:
    # The player fetches googlevideo itself, so a dead URL never reaches the server;
    # the player coming back for the same stream is the only sign it failed.
    clock = _Clock()
    cache = _youtube_cache(clock)
    first = _play(cache)
    clock.t = 40.0
    second = _play(cache)
    assert second != first
    clock.t = 100.0
    assert _play(cache) != second  # back again: the fresh URL failed too


def test_serve_stream_youtube_startup_double_open_keeps_resolve() -> None:
    # TiviMate, IPTV Player and others open each stream twice on start-up.
    clock = _Clock()
    cache = _youtube_cache(clock)
    first = _play(cache)
    clock.t = 200.0
    assert _play(cache) == first  # a later play of an old entry
    clock.t = 204.9
    assert _play(cache) == first


def test_serve_stream_youtube_rapid_retries_count_from_burst_start() -> None:
    # A player retrying faster than the start-up floor still gets a fresh resolve.
    clock = _Clock()
    cache = _youtube_cache(clock)
    first = _play(cache)
    clock.t = 200.0
    assert _play(cache) == first  # a later play, outside the window
    for t in (201.0, 202.0, 204.9):
        clock.t = t
        assert _play(cache) == first
    clock.t = 205.0
    assert _play(cache) != first


def test_serve_stream_youtube_retry_burst_resolves_at_most_every_30s() -> None:
    clock = _Clock()
    cache = _youtube_cache(clock)
    first = _play(cache)
    clock.t = 10.0
    assert _play(cache) == first  # resolved 10s ago: too young to be the dud
    clock.t = 35.0
    second = _play(cache)
    assert second != first
    clock.t = 50.0
    assert _play(cache) == second  # the fresh resolve stands through the burst


def test_serve_stream_youtube_repeated_retries_back_off() -> None:
    # Retries that keep coming (a leashed client, yt-dlp failing) double the wait
    # between fresh resolves; a quiet spell resets it.
    clock = _Clock()
    resolver = _YtResolver()
    cache = _youtube_cache(clock, resolver)
    _play(cache)
    for t, resolves in (
        (40.0, 2),  # 1st retry: entry >=30s old
        (80.0, 2),  # 2nd needs 60s
        (100.0, 3),
        (200.0, 3),  # 3rd needs 120s
        (220.0, 4),
        (341.0, 4),  # quiet for over the window: a new play, backoff reset
        (380.0, 5),  # first retry of the new run needs only 30s again
    ):
        clock.t = t
        _play(cache)
        assert resolver.calls == resolves, t


@pytest.mark.parametrize(
    "second_open_at",
    [41.0, 46.0],  # the retry's own start-up double-open; a second retry
)
def test_serve_stream_youtube_open_during_fresh_resolve_gets_its_result(
    second_open_at: float,
) -> None:
    clock = _Clock()
    resolver = _YtResolver(hold=2)
    cache = _youtube_cache(clock, resolver)
    first = _play(cache)
    clock.t = 40.0
    got: dict[str, str] = {}
    a = threading.Thread(target=lambda: got.__setitem__("a", _play(cache)))
    a.start()
    assert resolver.started.wait(5)
    clock.t = second_open_at
    b = threading.Thread(target=lambda: got.__setitem__("b", _play(cache)))
    b.start()
    b.join(0.2)  # let b reach the in-flight resolve before it completes
    resolver.release.set()
    a.join(5)
    b.join(5)
    assert got["a"] == got["b"] != first
    assert resolver.calls == 2


def test_serve_stream_youtube_failed_fresh_resolve_keeps_old_url() -> None:
    # The "retry" may be a second viewer of a working cam: a yt-dlp failure on
    # the fresh resolve must not turn it into a 502 for everyone.
    clock = _Clock()
    resolver = _YtResolver(fail=(2,))
    cache = _youtube_cache(clock, resolver)
    first = _play(cache)
    clock.t = 40.0
    assert _play(cache) == first
    clock.t = 50.0
    assert _play(cache) == first
    assert resolver.calls == 2  # the failed attempt also holds off the next one


def test_serve_stream_youtube_replay_after_window_keeps_resolve() -> None:
    clock = _Clock()
    cache = _youtube_cache(clock)
    first = _play(cache)
    clock.t = 121.0
    assert _play(cache) == first


def test_serve_stream_youtube_proxied_refreshes_keep_resolve() -> None:
    # Proxied, the player re-fetches /stream/<id> as its live playlist every few
    # seconds — normal playback, not a retry.
    clock = _Clock()
    cache = _youtube_cache(clock)
    fetched: list[str] = []

    def _fetch(u: str) -> str:
        fetched.append(u)
        return _YT_MANIFEST

    for t in (0.0, 40.0, 80.0):
        clock.t = t
        _play(cache, proxy_youtube=True, fetch=_fetch)
    assert len(set(fetched)) == 1


def test_serve_stream_hls_returns_200_rewritten_body() -> None:
    manifest = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nrelative_chunk.m3u8\n"
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


def test_serve_stream_rejects_unsafe_resolved_url() -> None:
    # a scraped og:video with a CRLF (or a non-http scheme) must never be reflected
    # into a 302 Location — send_header does no sanitising, so this would split the
    # response / be an open redirect
    for bad in (
        "https://cdn.x/v.mp4\r\nX-Injected: 1",
        "javascript:alert(1)",
        "http://cdn.x/\n\nHTTP/1.1 200 OK",
    ):
        cache = _make_cache(Resolved(url=bad, stream_type="mp4", ttl_seconds=None))
        status, _ct, body = serve_stream(
            ENTRY_ID,
            catalogue={ENTRY_ID: _entry()},
            cache=cache,
            fetch=lambda _u: None,
            base_url=BASE,
        )
        assert status == 502
        assert body == b"unsafe upstream URL"


def test_new_proxy_segment_hosts_relay_but_website_host_does_not() -> None:
    # the load-bearing new entries: segments 403 without relay (code comments say so)
    assert _proxy_segments_for("https://video-auth1.iol.pt/auth-beachcam/x/seg.ts")
    assert _proxy_segments_for("https://live.hdontap.com/hls/hosb1/x/seg.ts")
    assert _proxy_segments_for("https://edge01.amsterdam.nginx.hdontap.com/x/seg.ts")
    assert _proxy_segments_for("https://use01-smr05-relay.ozolio.com/x/seg.ts")
    assert _proxy_segments_for("https://hoktastream1.webcamera.pl/x/seg.ts")
    assert _proxy_segments_for(
        "https://mediamtx-0.euw4.gcp.prod.explore.org:8888/44_muxed/seg0.ts"
    )
    # but explore's own website and API hosts are not relayed
    assert not _proxy_segments_for("https://omega.explore.org/api/x.json")
    # but the hdontap CAM-PAGE website host is NOT relayed — the resolver reads its
    # JSON, so relaying it would let a tampered page steer signed segment fetches
    assert not _proxy_segments_for("https://hdontap.com/stream/1/cam/")


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

BALTIC_MANIFEST = "#EXTM3U\n#EXTINF:6.000,\nseg_001.ts\n"
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


def _segs(start: int, stop: int) -> str:
    return "".join(f"#EXTINF:1.0,\nseg_{i}.m4s\n" for i in range(start, stop))


_HEAD = "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:1\n"


def test_truncate_keeps_key_and_map_in_force_at_the_cut() -> None:
    pl = (
        _HEAD
        + "#EXT-X-MEDIA-SEQUENCE:0\n"
        + '#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXT-X-MAP:URI="init.mp4"\n'
        + _segs(0, 300)
    )
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    first = out.index("seg_180.m4s")  # the cut: segments 0-179 dropped
    assert "seg_179.m4s" not in out
    assert '#EXT-X-KEY:METHOD=AES-128,URI="k.key"' in out[:first]
    assert '#EXT-X-MAP:URI="init.mp4"' in out[:first]


def test_truncate_carries_the_latest_key_and_map_not_the_first() -> None:
    pl = (
        _HEAD
        + '#EXT-X-MAP:URI="a.mp4"\n#EXT-X-KEY:METHOD=AES-128,URI="a.key"\n'
        + _segs(0, 100)
        + '#EXT-X-DISCONTINUITY\n#EXT-X-MAP:URI="b.mp4"\n'
        + '#EXT-X-KEY:METHOD=AES-128,URI="b.key"\n'
        + _segs(100, 250)
        + '#EXT-X-MAP:URI="c.mp4"\n'  # changes again inside the kept window
        + _segs(250, 300)
    )
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    head = out[: out.index("seg_180.m4s")]
    # in force at the cut = the last of each tag before the first kept segment
    assert [ln for ln in head if ln.startswith("#EXT-X-MAP")][-1].endswith('"b.mp4"')
    assert [ln for ln in head if ln.startswith("#EXT-X-KEY")][-1].endswith('"b.key"')
    assert out.index("seg_249.m4s") < out.index('#EXT-X-MAP:URI="c.mp4"')
    assert out.index('#EXT-X-MAP:URI="c.mp4"') < out.index("seg_250.m4s")


def test_truncate_keeps_the_key_that_encrypted_the_init_section() -> None:
    # The key in force at a MAP encrypts its init section; the segments after a
    # later key use that one. Carried in the wrong order, the init gets the wrong key.
    k1 = '#EXT-X-KEY:METHOD=AES-128,URI="k1.key",IV=0x1'
    k2 = '#EXT-X-KEY:METHOD=AES-128,URI="k2.key"'
    pl = (
        _HEAD
        + f'{k1}\n#EXT-X-MAP:URI="init.mp4"\n'
        + _segs(0, 100)
        + f"{k2}\n"
        + _segs(100, 300)
    )
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    first = out.index("seg_180.m4s")
    carried = [ln for ln in out[:first] if ln.startswith(("#EXT-X-KEY", "#EXT-X-MAP"))]
    assert carried == [k1, '#EXT-X-MAP:URI="init.mp4"', k2]


def test_truncate_keeps_every_key_format_in_force() -> None:
    keys = [
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://a",KEYFORMAT="com.apple.streamingkeydelivery"',
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="data:,b",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"',
    ]
    pl = _HEAD + "\n".join(keys) + "\n" + _segs(0, 300)
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    assert all(k in out[: out.index("seg_180.m4s")] for k in keys)


def test_truncate_reads_playlist_tags_wherever_they_sit() -> None:
    # Tag order is free: a playlist tag after a segment tag still describes the
    # playlist, and losing MEDIA-SEQUENCE renumbers every kept segment.
    pl = (
        _HEAD
        + '#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXT-X-MEDIA-SEQUENCE:1000\n'
        + _segs(0, 300)
    )
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    assert [ln for ln in out if ln.startswith("#EXT-X-MEDIA-SEQUENCE")] == [
        "#EXT-X-MEDIA-SEQUENCE:1180"
    ]


def test_truncate_keeps_tags_after_the_last_segment() -> None:
    tail = [
        '#EXT-X-PRELOAD-HINT:TYPE=PART,URI="part_300.m4s"',
        '#EXT-X-RENDITION-REPORT:URI="../audio/index.m3u8",LAST-MSN=299',
    ]
    pl = _HEAD + "#EXT-X-MEDIA-SEQUENCE:0\n" + _segs(0, 300) + "\n".join(tail) + "\n"
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    assert out[-3:] == ["seg_299.m4s", *tail]


def test_truncate_keeps_playlist_wide_tags() -> None:
    wide = ["#EXT-X-INDEPENDENT-SEGMENTS", '#EXT-X-DEFINE:NAME="tok",VALUE="abc"']
    pl = (
        _HEAD
        + "#EXT-X-MEDIA-SEQUENCE:0\n"
        + "\n".join(wide)
        + "\n"
        + "".join(f"#EXTINF:1.0,\nseg_{i}.m4s?t={{$tok}}\n" for i in range(300))
    )
    out = truncate_to_live_edge(pl, window=120.0).splitlines()
    assert all(w in out for w in wide)


def test_serve_child_manifest_rewrites_the_carried_map_and_key() -> None:
    # The trim runs before the URI rewrite, so a carried tag must still be rewritten:
    # left relative, a player resolves it against /stream/<id> and 404s.
    upstream_url = "https://cdn.x/cam/chunklist.m3u8"
    dvr = (
        _HEAD
        + '#EXT-X-MAP:URI="init.mp4"\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n'
        + _segs(0, 300)
    )
    status, _ct, body = serve_child_manifest(
        ENTRY_ID, upstream_url, sign(upstream_url), fetch=lambda _u: dvr, base_url=BASE
    )
    assert status == 200
    out = body.decode().splitlines()
    first = out.index("https://cdn.x/cam/seg_180.m4s")
    assert '#EXT-X-MAP:URI="https://cdn.x/cam/init.mp4"' in out[:first]
    assert '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.x/cam/k.key"' in out[:first]


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


# ---------------------------------------------------------------------------
# loggable() — CDN session tokens must not land in an INFO-level log
# ---------------------------------------------------------------------------


def test_loggable_strips_the_query_by_default() -> None:
    url = "https://hd-auth.skylinewebcams.com/live.m3u8?a=t68vrSECRETTOKEN"
    out = loggable(url)
    assert "t68vrSECRETTOKEN" not in out
    assert out == "https://hd-auth.skylinewebcams.com/live.m3u8"


def test_loggable_keeps_host_and_path_for_diagnosis() -> None:
    assert loggable("https://cdn.example/a/b/c.m3u8?tok=x") == (
        "https://cdn.example/a/b/c.m3u8"
    )


def test_loggable_keeps_the_full_url_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """User decision: allow the tokens when DEBUG is deliberately switched on."""
    url = "https://hd-auth.skylinewebcams.com/live.m3u8?a=t68vrSECRETTOKEN"
    with caplog.at_level(logging.DEBUG, logger="webcam-aggregator.serving"):
        assert loggable(url) == url


def test_serve_stream_does_not_log_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end over the real warning path, not just the helper."""
    entry = CatalogueEntry(
        id="e1",
        title="t",
        category="Other",
        source="skyline",
        target_url="https://www.skylinewebcams.com/en/webcam/x.html",
        source_page_url="https://www.skylinewebcams.com/en/webcam/x.html",
    )

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(
            url="https://hd-auth.skylinewebcams.com/live.m3u8?a=t68vrSECRETTOKEN",
            stream_type="hls",
            ttl_seconds=None,
        )

    cache = ResolveCache(resolve, clock=lambda: 0.0)
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.serving"):
        status, _ct, _body = serve_stream(
            "e1",
            catalogue={"e1": entry},
            cache=cache,
            fetch=lambda _u: None,  # force the manifest-fetch-failed warning
            base_url="http://x",
        )
    assert status == 502
    assert "t68vrSECRETTOKEN" not in caplog.text
    assert "hd-auth.skylinewebcams.com" in caplog.text
