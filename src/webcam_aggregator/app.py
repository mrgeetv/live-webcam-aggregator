from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, override

import psutil

from webcam_aggregator import config
from webcam_aggregator.cache import ResolveCache
from webcam_aggregator.catalogue import (
    Hist,
    build_catalogue,
    failure_counts,
    top_counts,
)
from webcam_aggregator.extractors.baltic import BalticResolver
from webcam_aggregator.extractors.direct_hls import DirectHls
from webcam_aggregator.extractors.earthcam import EarthcamResolver
from webcam_aggregator.extractors.hdontap import HdontapResolver
from webcam_aggregator.extractors.ipcamlive import IpcamliveResolver, is_alias_page
from webcam_aggregator.extractors.metatag import MetaTagExtractor
from webcam_aggregator.extractors.ozolio import OzolioResolver
from webcam_aggregator.extractors.skyline import SkylineResolver
from webcam_aggregator.extractors.viewsurf import ViewsurfResolver
from webcam_aggregator.extractors.wetmet import WetmetResolver
from webcam_aggregator.extractors.ytdlp import YtDlpExtractor
from webcam_aggregator.fetch import (
    MANIFEST_MAX_BYTES,
    Fetcher,
    FetcherPostProtocol,
    FetchStats,
    HostPacer,
)
from webcam_aggregator.logging_redaction import RedactingFilter, scrub, trim_urls
from webcam_aggregator.models import (
    DEAD_MANIFEST,
    NO_EXTRACTOR,
    RESOLVE_FAILED,
    Candidate,
    CatalogueEntry,
)
from webcam_aggregator.registry import Registry
from webcam_aggregator.serving import (
    SegmentResponse,
    first_variant,
    is_playable_manifest,
    render_playlist,
    serve_child_manifest,
    serve_segment,
    serve_stream,
)
from webcam_aggregator.sources.airportwebcams import AirportWebcamsSource
from webcam_aggregator.sources.beachcam import BeachcamSource
from webcam_aggregator.sources.camscape import CamscapeSource
from webcam_aggregator.sources.camsecure import CamSecureSource
from webcam_aggregator.sources.cxtvlive import CxtvliveSource
from webcam_aggregator.sources.earthcam import EarthCamSource
from webcam_aggregator.sources.explore import ExploreOrgSource
from webcam_aggregator.sources.feratel import FeratelSource
from webcam_aggregator.sources.hdontap import HdOnTapSource
from webcam_aggregator.sources.livefromiceland import LiveFromIcelandSource
from webcam_aggregator.sources.livespotting import LivespottingSource
from webcam_aggregator.sources.ozolio import OzolioSource
from webcam_aggregator.sources.resortcams import ResortCamsSource
from webcam_aggregator.sources.shareju import SharejuSource
from webcam_aggregator.sources.skyline import SkylineSource
from webcam_aggregator.sources.tomarigi import TomarigiSource
from webcam_aggregator.sources.viewsurf import ViewsurfSource
from webcam_aggregator.sources.webcamerapl import WebcameraPlSource
from webcam_aggregator.sources.whatsupcams import WhatsupcamsSource
from webcam_aggregator.sources.wildlife_trusts import WildlifeTrustsSource
from webcam_aggregator.sources.windy import WindySource
from webcam_aggregator.sources.worldcams import WorldcamsSource
from webcam_aggregator.sources.youtube_api import YoutubeApiSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from webcam_aggregator.extractors.base import Extractor, Resolved

log = logging.getLogger("webcam-aggregator")
_HLS_CT = "application/vnd.apple.mpegurl"


def _total_rss() -> int:
    """RSS of this process plus any live child processes (yt-dlp/deno spawned per
    resolve), so /health reflects the container footprint, not just the parent."""
    p = psutil.Process()
    total = p.memory_info().rss
    try:
        for child in p.children(recursive=True):
            # A child can exit between listing and measuring.
            with contextlib.suppress(psutil.Error):
                total += child.memory_info().rss
    except psutil.Error:
        pass
    return total


def origin_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.hostname}/"


def _resolver_get(fetcher: Fetcher) -> Callable[[str], str]:
    def get_text(url: str) -> str:
        body, outcome = fetcher.get_with_outcome(url)
        if body is None:
            # The outcome label, but no URL: the message feeds the aggregated
            # resolve-failure detail at INFO, where a URL would leak query tokens
            # AND split one failure mode into per-URL-prefix buckets (the detail is
            # truncated), making the count unreadable. The URL is already logged at
            # DEBUG by liveness, and serve-time failures log the cam's target.
            raise ValueError(f"resolver fetch failed: {outcome}")
        return body

    return get_text


def _baltic_post(fetcher: FetcherPostProtocol) -> Callable[[str, dict[str, str]], str]:
    def post(url: str, data: dict[str, str]) -> str:
        # baltic's admin-ajax POST needs an XHR header and Referer = the SITE
        # ORIGIN (not the ajax URL) or it 403s silently.
        body, outcome = fetcher.post_with_outcome(
            url,
            data,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": origin_of(url)},
        )
        if body is None:
            # No URL for the same reason as _resolver_get: the message becomes an
            # aggregated INFO detail.
            raise ValueError(f"resolver post failed: {outcome}")
        return body

    return post


def _is_ytdlp(u: str) -> bool:
    return any(
        h in u
        for h in ("youtube.com", "youtu.be", "twitch.tv", "dailymotion.com", "earthcam")
    )


def _host_ends(u: str, domain: str) -> bool:
    host = (urllib.parse.urlsplit(u).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _path(u: str) -> str:
    return urllib.parse.urlsplit(u).path


def build_registry(extractors: dict[str, Extractor]) -> Registry:
    # The provider extractors match on the target's HOST (+ a path marker), not a bare
    # substring: a scraped iframe src of `https://evil/?x=hdontap.com/stream/` must NOT
    # route an attacker page into a resolver that then fetches it and reflects its
    # content (the resolved URL is emitted verbatim in a 302 Location header).
    rules: list[tuple[Callable[[str], bool], str]] = [
        (lambda u: "balticlivecam.com" in u, "baltic"),
        (
            lambda u: "ipcamlive.com/player/player.php" in u or is_alias_page(u),
            "ipcamlive",
        ),
        # any feratel host (webtv. and the webtvfc. variant embeds carry) on a /webtv
        # path — both serve the og:video meta the metatag extractor reads
        (lambda u: _host_ends(u, "feratel.com") and "/webtv" in _path(u), "metatag"),
        (lambda u: "api.wetmet.net/widgets/stream/frame.php" in u, "wetmet"),
        (lambda u: "skylinewebcams.com/en/webcam/" in u, "skyline"),
        (lambda u: "earthcam." in u, "earthcam"),
        # the resolved live.hdontap.com/hls/… URL has no "/stream/" path, so it falls
        # through to DirectHls if it ever appears as a target
        (
            lambda u: _host_ends(u, "hdontap.com") and _path(u).startswith("/stream/"),
            "hdontap",
        ),
        (
            lambda u: _host_ends(u, "ozolio.com") and "/explore/" in _path(u),
            "ozolio",
        ),
        (
            lambda u: (
                _host_ends(u, "joada.net")
                and _path(u).startswith("/embeded/embeded.html")
            ),
            "viewsurf",
        ),
        (lambda u: "twitch.tv/" in u, "ytdlp"),
        (_is_ytdlp, "ytdlp"),
        (lambda u: ".m3u8" in u or "worldcams.tv/player?url=" in u, "direct"),
    ]
    for _predicate, name in rules:
        if name not in extractors:
            raise ValueError(f"registry rule references unknown extractor {name!r}")
    return Registry(rules)


class NoExtractorError(ValueError):
    """No registry rule matched the target URL.

    Its own type so liveness can tell "we have no code for this site" (a gap to fill)
    apart from "the extractor ran and failed" (a cam that is probably just off air)."""


def make_resolve(
    registry: Registry, extractors: dict[str, Extractor]
) -> Callable[[str, str], Resolved]:
    def resolve(_entry_id: str, target_url: str) -> Resolved:
        name = registry.match(target_url)
        if name is None:
            log.debug("no extractor matched target %s", target_url)
            raise NoExtractorError(f"no extractor for {target_url}")
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


# Reason labels live in models.py — see the note there. Returned instead of a bare
# False so the catalogue can count them per source; a dropped cam used to be recorded
# only in a per-cam DEBUG line, which meant nothing at all once DEBUG was off.
_DETAIL_CHARS = 80


# A first variant that fails with one of these is gone, or its origin can't produce
# it. Any other failure gets the benefit of the doubt: a 401/403 is a session or gate
# refusing THIS fetch (MediaMTX behaves as if it keeps one HLS session per client IP,
# so a build's concurrent probes 401 each other's variants), and shedding, gateway
# errors and timeouts say nothing about the cam.
_VARIANT_GONE = frozenset({"http-404", "http-410", "http-500"})


def make_liveness_check(
    resolve: Callable[[str, str], Resolved],
    fetch: Callable[[str], tuple[str | None, str]],
) -> Callable[[Candidate], str | None]:
    """Build the build-time liveness probe: None if the cam is live, else the reason
    it was dropped. `fetch` returns a body (None on failure) and its outcome label,
    as `Fetcher.get_with_outcome` does."""

    def check(c: Candidate) -> str | None:
        try:
            r = resolve("probe", c.target_url)
        except NoExtractorError:
            # Kept at DEBUG as well as counted: the aggregate says how many, but only
            # the URLs tell you what to write an extractor FOR.
            log.debug("no extractor for %s", c.target_url)
            return NO_EXTRACTOR
        except Exception as exc:
            log.debug("liveness resolve failed for %s: %s", c.target_url, exc)
            # The detail separates "yt-dlp is broken" from "this cam is off air".
            # It is aggregated at INFO, so any URL it quotes is trimmed, query and
            # all: a cam's id would split one failure mode into a bucket per cam.
            detail = trim_urls(str(exc), keep_ids=False)[:_DETAIL_CHARS]
            return f"{RESOLVE_FAILED}:{detail}"
        if r.stream_type != "hls":
            return None  # mp4/other: trust the resolve
        # Actually fetch the HLS manifest — DirectHls/ipcamlive resolve without
        # fetching, so this is what catches offline (404), DASH, and the empty
        # playlist a CDN serves for an expired token (a 200 that looks like HLS
        # until you notice it lists nothing — see serving.is_playable_manifest).
        manifest, _ = fetch(r.url)
        if manifest is None or not is_playable_manifest(manifest):
            log.debug("liveness: dead/non-HLS manifest %s -> %s", c.target_url, r.url)
            return DEAD_MANIFEST
        # A master can answer 200 while the variant behind it is gone (an empty
        # body, a header-only stub, a 404 chunklist), so judge the variant a player
        # starts on as well.
        if "#EXT-X-STREAM-INF" in manifest:
            variant = first_variant(manifest, r.url)
            if variant is None:
                log.debug("liveness: master names no variant %s", c.target_url)
                return DEAD_MANIFEST
            body, outcome = fetch(variant)
            if (
                body is not None and not is_playable_manifest(body)
            ) or outcome in _VARIANT_GONE:
                log.debug("liveness: dead variant %s -> %s", c.target_url, variant)
                return DEAD_MANIFEST
        return None

    return check


def make_handler(
    store: CatalogueStore,
    cache: ResolveCache,
    base_url: str,
    manifest_fetch: Callable[[str], tuple[str | None, str]],
    source_status: Callable[[], dict[str, Any]],
    segment_fetch: Callable[[str, str | None], tuple[SegmentResponse | None, str]],
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
                status, ct, body = serve_child_manifest(
                    entry_id,
                    u_list[0],
                    qs.get("sig", [""])[0],  # a missing sig fails verify, logged
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
                range_header = self.headers.get("Range")
                seg_status, seg_ct, seg_cr, seg_body = serve_segment(
                    entry_id,
                    u_list[0],
                    qs.get("sig", [""])[0],  # a missing sig fails verify, logged
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
                    "last_build_seconds": st["last_build_seconds"],
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
        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # The uptime check polls /health every 60s — 4k lines/day of pure noise
            # that buries everything else whenever DEBUG is on to investigate.
            # Match the PARSED path: testing a substring of the raw request line would
            # let /stream/x?y=/health suppress its own access-log entry.
            if urllib.parse.urlsplit(self.path).path == "/health":
                return
            super().log_request(code, size)

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


def _source_status_for(expected: list[str], hist: dict[str, Hist]) -> dict[str, Any]:
    """The /health `sources` block: per-source RAW outcome of the last rebuild plus the
    sources that failed this cycle (crashed, or 0 kept).

    Values are the raw result even when the empty-guard is masking a failure in the
    served playlist, so a dying source surfaces here immediately. Cold start (nothing
    recorded yet) lists every source at zero; the top-level `ready` distinguishes that
    from a real 0. Pure, so the payload shape is testable without building the app.

    WHAT-only (kept/discovered/crashed/status): the WHY — per-host fetch outcomes,
    drop reasons, no-extractor hosts, pacing — lives on the per-cycle log lines."""
    sources: dict[str, Any] = {}
    unhealthy: list[str] = []
    for name in expected:
        h = hist.get(name)
        if h is None:
            # Not attempted yet (cold start, before the first rebuild records it):
            # placeholder zeros, never counted unhealthy — `ready` gates that window.
            sources[name] = {
                "kept": 0,
                "discovered": 0,
                "crashed": False,
                "status": "unknown",
            }
            continue
        sources[name] = {
            "kept": h.last_raw_kept,
            "discovered": h.last_discovered,
            "crashed": h.last_crashed,
            "status": h.status,
        }
        if h.last_crashed or h.last_raw_kept == 0:
            unhealthy.append(name)
    return {"sources": sources, "unhealthy": unhealthy}


def build_app(
    cfg: config.Config,
) -> tuple[
    CatalogueStore,
    ResolveCache,
    Callable[[], None],
    Callable[[], dict[str, Any]],
]:
    # One pacer shared by every BUILD-side fetcher (sources, liveness resolver,
    # probe). Reactive: it is a no-op until a host answers 429/503, then that host
    # is spaced and its retries are gated past the shedding window so they succeed
    # instead of re-entering the burst. Serve-side fetchers stay unpaced.
    pacer = HostPacer()

    # The resolve path exists TWICE, from one factory: the build-time set (paced,
    # stats-wired — liveness hammers a source's own site a second time, so it must
    # be measured and paced) and the serve-time set (unpaced, stats deliberately
    # unwired as before — a build-inflicted cooldown must never stall a player's
    # /stream request, which holds a ResolveCache per-entry lock while resolving).
    serve_fetcher = Fetcher(delay=0.0, retries=2)
    resolver_stats = FetchStats()
    build_fetcher = Fetcher(delay=0.0, retries=2, stats=resolver_stats, pacer=pacer)

    ytdlp = YtDlpExtractor()  # stateless, shared across both sets
    direct = DirectHls()

    def _extractor_set(f: Fetcher) -> dict[str, Extractor]:
        # BOTH extractor sets come from this one factory, so an extractor added
        # here exists for the build-time liveness resolve AND the serve-time
        # resolve — two hand-maintained dicts would let one silently drift.
        rget = _resolver_get(f)
        return {
            "ytdlp": ytdlp,
            "direct": direct,
            "metatag": MetaTagExtractor(rget),
            "baltic": BalticResolver(rget, _baltic_post(f)),
            "ipcamlive": IpcamliveResolver(rget),
            "skyline": SkylineResolver(rget),
            "earthcam": EarthcamResolver(rget),
            "wetmet": WetmetResolver(rget),
            "hdontap": HdontapResolver(rget),
            "ozolio": OzolioResolver(rget),
            "viewsurf": ViewsurfResolver(rget),
        }

    serve_extractors = _extractor_set(serve_fetcher)
    build_extractors = _extractor_set(build_fetcher)
    registry = build_registry(serve_extractors)  # same keys in both (one factory)
    resolve_serve = make_resolve(registry, serve_extractors)
    resolve_build = make_resolve(registry, build_extractors)
    cache: ResolveCache = ResolveCache(resolve_serve, clock=time.monotonic)

    def _new_yt_client() -> Any:
        import googleapiclient.discovery

        # static_discovery=True uses the bundled discovery document, so rebuilding the
        # client after a wedged connection costs ~5 ms and no network call.
        return googleapiclient.discovery.build(
            "youtube",
            "v3",
            developerKey=cfg.youtube_api_key,
            static_discovery=True,
            cache_discovery=False,
        )

    try:
        _new_yt_client()  # fail fast at startup on a bad key/deps, as before
        yt_available = True
    except Exception as exc:
        # No log.exception: the traceback's last line is str(exc), and googleapiclient
        # puts the developer key in the discovery-doc URL it reports.
        log.warning(
            "YouTube client init failed; youtube-api source disabled — %s: %s",
            type(exc).__name__,
            scrub(str(exc)),
        )
        yt_available = False

    yt_source = (
        YoutubeApiSource(_new_yt_client, cfg.search_query) if yt_available else None
    )
    # One Fetcher — and one FetchStats — PER SOURCE. A single shared fetcher made it
    # impossible to say which source's fetches failed, and a process-global counter
    # would also collect serve-time player traffic between rebuilds, so the line meant
    # to explain an empty source would name hosts the build never touched.
    source_stats: dict[str, FetchStats] = {}

    def _source_fetcher(name: str, *, impersonate: bool = False) -> Fetcher:
        # impersonate=True for a site whose Cloudflare gate 403s the requests client
        # on its TLS/HTTP2 fingerprint (see fetch._ImpersonatingSession).
        stats = FetchStats()
        source_stats[name] = stats
        return Fetcher(stats=stats, pacer=pacer, impersonate=impersonate)

    active_sources: list[Any] = [
        s
        for s in (
            yt_source,  # googleapiclient, not Fetcher — no fetch stats to collect
            WorldcamsSource(_source_fetcher("worldcams")),
            CxtvliveSource(_source_fetcher("cxtvlive", impersonate=True)),
            SkylineSource(_source_fetcher("skyline")),
            CamscapeSource(_source_fetcher("camscape")),
            EarthCamSource(_source_fetcher("earthcam")),
            CamSecureSource(_source_fetcher("camsecure")),
            ExploreOrgSource(_source_fetcher("explore")),
            WildlifeTrustsSource(_source_fetcher("wildlife-trusts", impersonate=True)),
            LivespottingSource(_source_fetcher("livespotting")),
            LiveFromIcelandSource(_source_fetcher("livefromiceland")),
            BeachcamSource(_source_fetcher("beachcam")),
            ResortCamsSource(_source_fetcher("resortcams")),
            HdOnTapSource(_source_fetcher("hdontap")),
            OzolioSource(_source_fetcher("ozolio")),
            FeratelSource(_source_fetcher("feratel")),
            WhatsupcamsSource(_source_fetcher("whatsupcams")),
            ViewsurfSource(_source_fetcher("viewsurf")),
            WebcameraPlSource(_source_fetcher("webcamerapl")),
            AirportWebcamsSource(_source_fetcher("airportwebcams")),
            SharejuSource(_source_fetcher("shareju")),
            TomarigiSource(_source_fetcher("tomarigi")),
            WindySource(_source_fetcher("windy")),
        )
        if s is not None
    ]
    # The stats keys must match each source's own .name, or the per-source fetch line
    # silently reports nothing. Fail loudly at startup instead.
    unmapped = {s.name for s in active_sources} - set(source_stats) - {"youtube-api"}
    if unmapped:
        raise ValueError(f"source_stats key mismatch: {sorted(unmapped)}")

    store = CatalogueStore()
    history: dict[str, Hist] = {}
    # delay=0: liveness verify-fetches hit CDNs (not the scraped sites) and run
    # concurrently, so politeness spacing isn't needed here. retries=2 so a paced
    # 503 gets its post-cooldown retry (range(retries) means retries=1 is ZERO
    # retries), but retry_only_shedding keeps the dead-cam long tail (404s,
    # 20s timeouts) at one attempt each, exactly as before.
    probe_stats = FetchStats()
    probe_fetcher = Fetcher(
        delay=0.0,
        retries=2,
        stats=probe_stats,
        pacer=pacer,
        retry_only_shedding=True,
    )
    drop_reason_for = make_liveness_check(resolve_build, probe_fetcher.get_with_outcome)

    def youtube_live(ids: Any) -> dict[str, str]:
        if yt_source is None:
            return {}
        try:
            return yt_source.live_ids(ids)
        except Exception as exc:
            # A YouTube quota/transient error must not abort the whole rebuild —
            # just treat YT cams as offline this cycle (scrapers still build).
            # No log.exception: a traceback ends in str(exc), and HttpError's str
            # prints the request URI, which carries the developer key.
            log.warning(
                "youtube live_ids failed; treating YT cams as offline — %s: %s",
                type(exc).__name__,
                scrub(str(exc)),
            )
            return {}

    expected_sources = [s.name for s in active_sources]

    # The one cross-source number /health carries; everything else the build-side
    # diagnostics produce (liveness fetch outcomes, pacing counters) goes to the
    # per-cycle log lines only — see _source_status_for on why.
    diag: dict[str, Any] = {"last_build_seconds": None}

    def source_status() -> dict[str, Any]:
        # Copy defensively: build_catalogue mutates `history` from the rebuild thread,
        # so a live /health request must not crash on "changed size during iteration".
        try:
            hist = dict(history)
        except RuntimeError:
            hist = {}
        out = _source_status_for(expected_sources, hist)
        out.update(diag)
        return out

    def drain_source_stats(name: str) -> dict[str, dict[str, int]]:
        stats = source_stats.get(name)
        return stats.drain() if stats is not None else {}

    def _report_build_diag() -> None:
        # The liveness fetchers and pacer are cross-source, so they report here
        # rather than on a source's own line. One aggregate INFO line each, only
        # when something failed — a healthy cycle adds no noise.
        liveness = {"resolver": resolver_stats.drain(), "probe": probe_stats.drain()}
        pacing = pacer.drain()
        for label, fetches in liveness.items():
            failures = failure_counts(fetches)
            if failures:
                total = sum(sum(o.values()) for o in fetches.values())
                log.info(
                    "liveness %s: %d fetched, %d failed: %s",
                    label,
                    total,
                    sum(failures.values()),
                    top_counts(failures, 3),
                )
        if pacing:
            top_pace = sorted(
                pacing.items(), key=lambda kv: (-kv[1]["penalties"], kv[0])
            )[:3]
            log.info(
                "pacing: %s",
                ", ".join(
                    f"{int(v['penalties'])}x {h} ({v['backoff_seconds']:.0f}s backoff)"
                    for h, v in top_pace
                ),
            )

    def rebuild_once() -> None:
        log.info("starting catalogue rebuild")
        t0 = time.monotonic()
        try:
            entries = build_catalogue(
                active_sources,
                drop_reason_for=drop_reason_for,
                youtube_live=youtube_live,
                history=history,
                exclude_categories=cfg.exclude_categories,
                max_parallel_sources=cfg.max_parallel_sources,
                fetch_stats=drain_source_stats,
            )
        finally:
            # Drain whatever the aborted cycle managed to record. Without this a rebuild
            # that raises leaves its counters to merge into the next cycle's totals —
            # precisely when the numbers matter most.
            for name in source_stats:
                drain_source_stats(name)
            _report_build_diag()
            diag["last_build_seconds"] = round(time.monotonic() - t0, 1)
        store.swap(entries)
        log.info(
            "catalogue rebuilt: %d entries in %.0fs",
            len(entries),
            time.monotonic() - t0,
        )

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
    # Backstop for credentials in log output. On the HANDLER, not the logger: a logger's
    # filters only see records logged directly on it, and every module here logs through
    # a child logger (webcam-aggregator.catalogue, .fetch, .sources.youtube, …).
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())
    store, cache, rebuild_once, source_status = build_app(cfg)

    # Unpaced by design: this is the serve-time hot path (manifest refreshes every
    # few seconds per viewer, plus segment relay) — per-host pacing here would stall
    # playback, and CDN traffic is not what the pacer protects against.
    # Cookieless: it is shared by every viewer, so upstream session state must travel
    # in the (signed, per-viewer) URLs rather than in one process-wide jar.
    manifest_fetcher = Fetcher(
        delay=0.0, retries=1, byte_cap=MANIFEST_MAX_BYTES, keep_cookies=False
    )
    handler_cls = make_handler(
        store,
        cache,
        cfg.public_base_url,
        manifest_fetch=manifest_fetcher.get_with_outcome,
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
