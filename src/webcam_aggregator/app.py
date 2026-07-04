from __future__ import annotations

import json
import logging
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override

import psutil

from . import config
from .cache import ResolveCache
from .catalogue import Hist, build_catalogue
from .extractors.base import Extractor, Resolved
from .extractors.baltic import BalticResolver
from .extractors.direct_hls import DirectHls
from .extractors.earthcam import EarthcamResolver
from .extractors.ipcamlive import IpcamliveResolver
from .extractors.metatag import MetaTagExtractor
from .extractors.skyline import SkylineResolver
from .extractors.ytdlp import YtDlpExtractor
from .fetch import MANIFEST_MAX_BYTES, Fetcher, FetcherPostProtocol
from .models import Candidate, CatalogueEntry
from .registry import Registry
from .serving import render_playlist, serve_child_manifest, serve_segment, serve_stream
from .sources.camscape import CamscapeSource
from .sources.camsecure import CamSecureSource
from .sources.cxtvlive import CxtvliveSource
from .sources.earthcam import EarthCamSource
from .sources.explore import ExploreOrgSource
from .sources.skyline import SkylineSource
from .sources.wildlife_trusts import WildlifeTrustsSource
from .sources.worldcams import WorldcamsSource
from .sources.youtube_api import YoutubeApiSource

log = logging.getLogger("webcam-aggregator")
_HLS_CT = "application/vnd.apple.mpegurl"


def _total_rss() -> int:
    """RSS of this process plus any live child processes (yt-dlp/deno spawned per
    resolve), so /health reflects the container footprint, not just the parent."""
    p = psutil.Process()
    total = p.memory_info().rss
    try:
        for child in p.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass  # child exited between listing and measuring
    except psutil.Error:
        pass
    return total


def origin_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.hostname}/"


def _resolver_get(fetcher: Fetcher) -> Callable[[str], str]:
    def get_text(url: str) -> str:
        body = fetcher.get(url)
        if body is None:
            raise ValueError(f"resolver fetch failed: {url}")
        return body

    return get_text


def _baltic_post(fetcher: FetcherPostProtocol) -> Callable[[str, dict[str, str]], str]:
    def post(url: str, data: dict[str, str]) -> str:
        # baltic's admin-ajax POST needs an XHR header and Referer = the SITE
        # ORIGIN (not the ajax URL) or it 403s silently.
        body = fetcher.post(
            url,
            data,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": origin_of(url)},
        )
        if body is None:
            raise ValueError(f"resolver post failed: {url}")
        return body

    return post


def _is_ytdlp(u: str) -> bool:
    return any(
        h in u
        for h in ("youtube.com", "youtu.be", "twitch.tv", "dailymotion.com", "earthcam")
    )


def build_registry(extractors: dict[str, Extractor]) -> Registry:
    rules: list[tuple[Callable[[str], bool], str]] = [
        (lambda u: "balticlivecam.com" in u, "baltic"),
        (lambda u: "ipcamlive.com/player/player.php" in u, "ipcamlive"),
        (lambda u: "webtv.feratel.com" in u, "metatag"),
        (lambda u: "skylinewebcams.com/en/webcam/" in u, "skyline"),
        (lambda u: "earthcam." in u, "earthcam"),
        (lambda u: "twitch.tv/" in u, "ytdlp"),
        (lambda u: _is_ytdlp(u), "ytdlp"),
        (lambda u: ".m3u8" in u or "worldcams.tv/player?url=" in u, "direct"),
    ]
    for _predicate, name in rules:
        if name not in extractors:
            raise ValueError(f"registry rule references unknown extractor {name!r}")
    return Registry(rules)


def make_resolve(
    registry: Registry, extractors: dict[str, Extractor]
) -> Callable[[str, str], Resolved]:
    def resolve(_entry_id: str, target_url: str) -> Resolved:
        name = registry.match(target_url)
        if name is None:
            log.debug("no extractor matched target %s", target_url)
            raise ValueError(f"no extractor for {target_url}")
        return extractors[name].resolve(target_url)

    return resolve


class CatalogueStore:
    _snapshot: dict[str, CatalogueEntry]
    ready: bool

    def __init__(self) -> None:
        self._snapshot = {}
        self.ready = False

    def swap(self, entries: list[CatalogueEntry]) -> None:
        self._snapshot = {e.id: e for e in entries}  # atomic rebind
        self.ready = True

    def snapshot(self) -> dict[str, CatalogueEntry]:
        return self._snapshot


def make_is_alive(
    resolve: Callable[[str, str], Resolved],
    fetch: Callable[[str], str | None],
) -> Callable[[Candidate], bool]:
    def is_alive(c: Candidate) -> bool:
        try:
            r = resolve("probe", c.target_url)
        except Exception as exc:
            log.debug("liveness resolve failed for %s: %s", c.target_url, exc)
            return False
        if r.stream_type != "hls":
            return True  # mp4/other: trust the resolve
        # Actually fetch the HLS manifest — DirectHls/ipcamlive resolve without
        # fetching, so this is what catches offline (404) and DASH streams.
        manifest = fetch(r.url)
        if not manifest or "#EXTM3U" not in manifest:
            log.debug("liveness: dead/non-HLS manifest %s -> %s", c.target_url, r.url)
            return False
        return True

    return is_alive


def make_handler(
    store: CatalogueStore,
    cache: ResolveCache,
    base_url: str,
    manifest_fetch: Callable[[str], str | None],
    source_status: Callable[[], dict[str, Any]],
    segment_fetch: Callable[
        [str, str | None], tuple[int, str, str | None, bytes] | None
    ],
    proxy_youtube: bool = False,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/playlist.m3u8":
                if not store.ready:
                    self._respond(503, "text/plain", b"not ready yet")
                    return
                body = render_playlist(
                    list(store.snapshot().values()), base_url=base_url
                )
                self._respond(200, _HLS_CT, body.encode())
                return

            if path.endswith("/m") and path.startswith("/stream/"):
                # /stream/<id>/m?u=<url>&sig=<hmac>
                entry_id = path[len("/stream/") : -len("/m")]
                u_list = qs.get("u", [])
                if not u_list:
                    self._respond(400, "text/plain", b"missing u= param")
                    return
                sig_list = qs.get("sig", [])
                if not sig_list:
                    self._respond(403, "text/plain", b"bad signature")
                    return
                status, ct, body = serve_child_manifest(
                    entry_id,
                    u_list[0],
                    sig_list[0],
                    fetch=manifest_fetch,
                    base_url=base_url,
                )
                self._respond(status, ct, body)
                return

            if path.endswith("/s") and path.startswith("/stream/"):
                # /stream/<id>/s?u=<url>&sig=<hmac>
                entry_id = path[len("/stream/") : -len("/s")]
                u_list = qs.get("u", [])
                if not u_list:
                    self._respond(400, "text/plain", b"missing u= param")
                    return
                sig_list = qs.get("sig", [])
                if not sig_list:
                    self._respond(403, "text/plain", b"bad signature")
                    return
                range_header = self.headers.get("Range")
                seg_status, seg_ct, seg_cr, seg_body = serve_segment(
                    entry_id,
                    u_list[0],
                    sig_list[0],
                    fetch_segment=segment_fetch,
                    range_header=range_header,
                )
                self.send_response(seg_status)
                self.send_header("Content-Type", seg_ct)
                self.send_header("Accept-Ranges", "bytes")
                if seg_cr is not None:
                    self.send_header("Content-Range", seg_cr)
                self.send_header("Content-Length", str(len(seg_body)))
                self.end_headers()
                self.wfile.write(seg_body)
                return

            if path.startswith("/stream/"):
                entry_id = path[len("/stream/") :]
                status, ct_or_loc, body = serve_stream(
                    entry_id,
                    catalogue=store.snapshot(),
                    cache=cache,
                    fetch=manifest_fetch,
                    base_url=base_url,
                    proxy_youtube=proxy_youtube,
                )
                if status == 302:
                    self.send_response(302)
                    self.send_header("Location", ct_or_loc)
                    self.end_headers()
                    return
                self._respond(status, ct_or_loc, body)
                return

            if path == "/health":
                snapshot = store.snapshot()
                st = source_status()
                unhealthy = st["unhealthy"]
                # Single rollup for a JSON-query uptime monitor ($.healthy == true).
                # False during the cold-start build (not ready yet) and whenever any source
                # crashed or returned 0 cams this cycle — even while the empty-guard is
                # still masking that failure in the served playlist.
                healthy = store.ready and not unhealthy
                payload = {
                    "ready": store.ready,
                    "healthy": healthy,
                    "streams": len(snapshot),
                    "unhealthy_sources": unhealthy,
                    "sources": st["sources"],
                    "rss_mb": round(_total_rss() / 1048576, 1),
                }
                self._respond(200, "application/json", json.dumps(payload).encode())
                return

            self._respond(404, "text/plain", b"not found")

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: Any) -> None:
            log.debug(format, *args)

    return Handler


class _QuietHTTPServer(ThreadingHTTPServer):
    @override
    def handle_error(self, request: Any, client_address: Any) -> None:
        # A player closing a stream mid-write is normal, not an error to dump.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def run_http_server(
    handler_cls: type[BaseHTTPRequestHandler], port: int = 8000
) -> None:
    server = _QuietHTTPServer(("", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info("HTTP server listening on port %d", port)


def build_app(
    cfg: config.Config,
) -> tuple[
    CatalogueStore,
    ResolveCache,
    Callable[[], None],
    Callable[[], dict[str, Any]],
]:
    fetcher = Fetcher()
    resolver_fetcher = Fetcher(delay=0.0, retries=2)
    rget = _resolver_get(resolver_fetcher)

    extractors: dict[str, Extractor] = {
        "ytdlp": YtDlpExtractor(),
        "direct": DirectHls(),
        "metatag": MetaTagExtractor(rget),
        "baltic": BalticResolver(rget, _baltic_post(resolver_fetcher)),
        "ipcamlive": IpcamliveResolver(rget),
        "skyline": SkylineResolver(rget),
        "earthcam": EarthcamResolver(rget),
    }
    registry = build_registry(extractors)
    resolve = make_resolve(registry, extractors)
    cache: ResolveCache = ResolveCache(resolve, clock=time.monotonic)

    try:
        import googleapiclient.discovery

        yt_client = googleapiclient.discovery.build(
            "youtube", "v3", developerKey=cfg.youtube_api_key
        )
    except Exception:
        log.exception("YouTube client init failed; youtube-api source disabled")
        yt_client = None

    yt_source = (
        YoutubeApiSource(yt_client, cfg.search_query) if yt_client is not None else None
    )
    active_sources: list[Any] = [
        s
        for s in (
            yt_source,
            WorldcamsSource(fetcher),
            CxtvliveSource(fetcher),
            SkylineSource(fetcher),
            CamscapeSource(fetcher),
            EarthCamSource(fetcher),
            CamSecureSource(fetcher),
            ExploreOrgSource(fetcher),
            WildlifeTrustsSource(fetcher),
        )
        if s is not None
    ]

    store = CatalogueStore()
    history: dict[str, Hist] = {}
    # delay=0: liveness verify-fetches hit CDNs (not the scraped sites) and run
    # concurrently, so politeness spacing isn't needed here.
    probe_fetcher = Fetcher(delay=0.0, retries=1)
    is_alive = make_is_alive(resolve, probe_fetcher.get)

    def youtube_live(ids: Any) -> dict[str, str]:
        if yt_source is None:
            return {}
        try:
            return yt_source.live_ids(ids)
        except Exception:
            # A YouTube quota/transient error must not abort the whole rebuild —
            # just treat YT cams as offline this cycle (scrapers still build).
            log.exception("youtube live_ids failed; treating YT cams as offline")
            return {}

    expected_sources = [s.name for s in active_sources]

    def source_status() -> dict[str, Any]:
        # Per-source RAW outcome of the last rebuild + the sources that failed this cycle
        # (crashed, or 0 kept), for /health monitoring. The values are the raw result
        # even when the empty-guard is masking a failure in the served playlist, so a
        # dying source surfaces here immediately. Cold-start (nothing recorded yet) shows
        # all sources at zero; the top-level `ready` distinguishes that from a real 0.
        # Copy defensively: build_catalogue mutates `history` from the rebuild thread,
        # so a live /health request must not crash on "changed size during iteration".
        try:
            hist = dict(history)
        except RuntimeError:
            hist = {}
        sources: dict[str, Any] = {}
        unhealthy: list[str] = []
        for name in expected_sources:
            h = hist.get(name)
            if h is None:
                # Not attempted yet (cold start, before the first rebuild records it):
                # placeholder zeros, never counted unhealthy — `ready` gates that window.
                sources[name] = {"kept": 0, "discovered": 0, "crashed": False}
                continue
            sources[name] = {
                "kept": h.last_raw_kept,
                "discovered": h.last_discovered,
                "crashed": h.last_crashed,
            }
            if h.last_crashed or h.last_raw_kept == 0:
                unhealthy.append(name)
        return {"sources": sources, "unhealthy": unhealthy}

    def rebuild_once() -> None:
        log.info("starting catalogue rebuild")
        entries = build_catalogue(
            active_sources,
            is_alive=is_alive,
            youtube_live=youtube_live,
            history=history,
            exclude_categories=cfg.exclude_categories,
            max_parallel_sources=cfg.max_parallel_sources,
        )
        store.swap(entries)
        log.info("catalogue rebuilt: %d entries", len(entries))

    return store, cache, rebuild_once, source_status


def main() -> None:
    # Root stays at WARNING so third-party libs (googleapiclient, urllib3) never
    # log request URLs at DEBUG — those carry the API key as a query param. Only
    # our own loggers honour LOG_LEVEL.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    cfg = config.load()
    logging.getLogger("webcam-aggregator").setLevel(
        getattr(logging, cfg.log_level, logging.INFO)
    )
    store, cache, rebuild_once, source_status = build_app(cfg)

    manifest_fetcher = Fetcher(delay=0.0, retries=1, byte_cap=MANIFEST_MAX_BYTES)
    handler_cls = make_handler(
        store,
        cache,
        cfg.public_base_url,
        manifest_fetch=manifest_fetcher.get,
        source_status=source_status,
        segment_fetch=manifest_fetcher.get_segment,
        proxy_youtube=cfg.proxy_youtube,
    )
    run_http_server(handler_cls)

    while True:
        try:
            rebuild_once()
        except Exception:
            log.exception("catalogue rebuild failed; will retry next cycle")
        time.sleep(cfg.catalogue_interval_hours * 3600)
