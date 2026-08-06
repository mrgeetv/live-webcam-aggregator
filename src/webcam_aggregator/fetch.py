from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger("webcam-aggregator.fetch")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

MAX_BYTES = 8 * 1024 * 1024  # 8 MB ceiling for any fetched document
MANIFEST_MAX_BYTES = (
    32 * 1024 * 1024
)  # 32 MB — DVR HLS playlists carry a huge back-catalogue
SEGMENT_MAX_BYTES = 16 * 1024 * 1024  # 16 MB ceiling for a proxied media segment
_MAX_REDIRECTS = 5


def resolve_scrape_workers() -> int:
    """Concurrency for scraping/liveness. The work is I/O-bound (network waits), so
    the ceiling is politeness to the target host, not local cores. Override with the
    SCRAPE_WORKERS env var (e.g. raise it on a small box where the build is slow)."""
    default = min(16, (os.cpu_count() or 2) * 4)
    raw = os.environ.get("SCRAPE_WORKERS")
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("invalid SCRAPE_WORKERS=%r — using default %d", raw, default)
        return default
    if v <= 0:
        log.warning("SCRAPE_WORKERS=%d is not positive — using default %d", v, default)
        return default
    return v


SCRAPE_WORKERS = resolve_scrape_workers()

_T = TypeVar("_T")
_R = TypeVar("_R")


def thread_map(
    fn: Callable[[_T], _R], items: list[_T], *, workers: int = SCRAPE_WORKERS
) -> list[_R]:
    """Map fn over items concurrently, preserving order. Empty in → empty out."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        return list(ex.map(fn, items))


def _ip_is_unsafe(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


# Fetch outcome labels. Every failure gets one, so a source that comes back empty can
# say WHY on its own log line instead of vanishing into a silent None.
OK = "ok"
BAD_SCHEME = "bad-scheme"
DNS_ERROR = "dns-error"
BLOCKED_IP = "blocked-ip"
TIMEOUT = "timeout"
CONN_ERROR = "conn-error"
TOO_LARGE = "too-large"
TOO_MANY_REDIRECTS = "too-many-redirects"
REDIRECT_NO_LOCATION = "redirect-no-location"
UNEXPECTED_REDIRECT = "unexpected-redirect"


def _validate_ip(url: str) -> tuple[str | None, str]:
    """Resolve the URL's host ONCE, validate every returned IP, and report WHY when it
    is rejected. Returns (ip, "ok"), or (None, reason) where reason is one of
    bad-scheme / dns-error / blocked-ip.

    This is the SSRF check AND the source of truth for the connection IP: the caller
    pins the connection to the returned IP so there is no second DNS lookup between
    validation and connect (closing the DNS-rebinding TOCTOU window). Returning the
    first IP (rather than re-resolving) is what makes validate-then-pin atomic.

    The reason comes from THIS lookup, never a second one. Re-resolving to label the
    failure would not just be slow on the dead-domain long tail — on flaky or
    round-robin DNS it could report a genuine unsafe-IP block as a transient
    "dns-error", quietly downgrading the only signal that says the guard fired."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None, BAD_SCHEME
    host = parts.hostname
    if not host:
        return None, BAD_SCHEME
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None, DNS_ERROR
    chosen: str | None = None
    for info in infos:
        ip_str = str(info[4][0])  # sockaddr[0] is the IP literal
        if _ip_is_unsafe(ip_str):
            return None, BLOCKED_IP  # any unsafe IP poisons the whole host — refuse
        if chosen is None:
            chosen = ip_str
    if chosen is None:
        return None, DNS_ERROR  # resolved to nothing at all
    return chosen, OK


def _resolve_validated_ip(url: str) -> str | None:
    """Back-compat wrapper for callers that don't need the rejection reason."""
    return _validate_ip(url)[0]


# --- validate-then-pin DNS (the "curl --resolve" approach) -------------------
# We let urllib3 connect to the HOSTNAME normally, so SNI, the Host header, and
# certificate validation are all done against the hostname exactly as usual; we
# only override the *resolution* of that hostname to the IP we already validated.
# There is no second DNS lookup between the safety check and the connect, which is
# what closes the rebinding TOCTOU.
#
# (An earlier attempt pinned via a requests adapter + urllib3 pool kwargs. urllib3
# 2.x ignores `server_hostname` passed that way, so SNI fell back to the IP and
# Cloudflare 403'd it. Pinning the resolver instead keeps SNI/Host/cert correct,
# which is exactly what `curl --resolve` does.)
_pin = threading.local()
_real_getaddrinfo = socket.getaddrinfo


def _pinning_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    pinned: dict[str, str] | None = getattr(_pin, "map", None)
    if pinned and host in pinned:
        host = pinned[host]  # resolve the pre-validated IP literal, not the hostname
    return _real_getaddrinfo(host, *args, **kwargs)


# Process-wide but transparent: with no active pin it's a straight passthrough, and
# pins are thread-local + scoped to a single request (see `_PinDNS`).
socket.getaddrinfo = _pinning_getaddrinfo


class _PinDNS:
    """Pin `host` -> `ip` for getaddrinfo on THIS thread for the duration of the
    with-block, so the connection dials the validated IP while urllib3 still does
    SNI/Host/cert against the hostname. Thread-local, so concurrent fetches from
    thread_map workers and the HTTP server never see each other's pins."""

    _host: str
    _ip: str

    def __init__(self, host: str, ip: str) -> None:
        self._host = host
        self._ip = ip

    def __enter__(self) -> None:
        m: dict[str, str] | None = getattr(_pin, "map", None)
        if m is None:
            m = {}
            _pin.map = m
        m[self._host] = self._ip

    def __exit__(self, *_exc: object) -> None:
        m: dict[str, str] | None = getattr(_pin, "map", None)
        if m is not None:
            m.pop(self._host, None)


class FetcherProtocol(Protocol):
    def get(self, url: str, timeout: float = ..., /) -> str | None: ...


class FetcherPostProtocol(Protocol):
    def post(
        self,
        url: str,
        data: dict[str, str],
        /,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = ...,
    ) -> str | None: ...


# Hosts that gate on a Referer: EarthCam's CDN 403s the manifest/segments without it;
# CamSecure's player pages (camsecure.co/.uk) serve a decoy page without it, hiding the
# HLS <source>.
_REFERER_HOSTS: dict[str, str] = {
    "earthcam.com": "https://www.earthcam.com/",
    "camsecure.co": "https://www.camsecure.co.uk/",
    "camsecure.uk": "https://www.camsecure.co.uk/",
}


def _referer_for(url: str) -> dict[str, str]:
    host = urlsplit(url).hostname or ""
    for h, ref in _REFERER_HOSTS.items():
        if host == h or host.endswith("." + h):
            return {"Referer": ref}
    return {}


class _FetchFailure(Exception):
    """A classified, non-retryable failure — a blocked IP, an oversized body or a
    broken redirect chain. Distinct from requests' exceptions because retrying cannot
    change the verdict. Carries the failing HOP's host, which on a redirect is not the
    host the caller asked for."""

    reason: str
    host: str

    def __init__(self, reason: str, host: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.host = host


class FetchStats:
    """Thread-safe (host, outcome) -> count.

    One instance PER SOURCE (see app.build_app). A single shared counter could not say
    WHICH source's fetches failed, and a process-global one would also be polluted by
    serve-time player traffic between rebuilds — so the one line that is supposed to
    explain an empty source would be reporting hosts the build never touched."""

    _counts: dict[tuple[str, str], int]
    _lock: threading.Lock
    _max_hosts: int

    def __init__(self, max_hosts: int = 500) -> None:
        self._counts = {}
        self._lock = threading.Lock()
        # Host keys come from scraped third-party HTML, so their cardinality is not
        # ours to trust. Past the cap, fold into one "other" bucket rather than growing
        # without bound between drains.
        self._max_hosts = max_hosts

    def record(self, host: str, outcome: str) -> None:
        with self._lock:
            key = (host or "?", outcome)
            if key not in self._counts and len(self._counts) >= self._max_hosts:
                key = ("other", outcome)
            self._counts[key] = self._counts.get(key, 0) + 1

    def drain(self) -> dict[str, dict[str, int]]:
        """Snapshot and clear, as {host: {outcome: count}}."""
        with self._lock:
            counts = self._counts
            self._counts = {}
        out: dict[str, dict[str, int]] = {}
        for (host, outcome), n in counts.items():
            out.setdefault(host, {})[outcome] = n
        return out


def _classify(exc: BaseException) -> str:
    """Map a requests exception to a stable outcome label.

    Order matters: ConnectTimeout subclasses BOTH Timeout and ConnectionError, and
    "timeout" is the more useful diagnosis, so Timeout is checked first."""
    if isinstance(exc, requests.Timeout):
        return TIMEOUT
    if isinstance(exc, requests.TooManyRedirects):
        return TOO_MANY_REDIRECTS
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        if resp is not None:
            return f"http-{resp.status_code}"
    return CONN_ERROR


class Fetcher:
    _session: requests.Session
    _delay: float
    _retries: int
    _byte_cap: int
    _stats: FetchStats

    def __init__(
        self,
        delay: float = 1.0,
        retries: int = 3,
        byte_cap: int = MAX_BYTES,
        stats: FetchStats | None = None,
    ) -> None:
        self._delay = delay
        self._retries = retries
        self._byte_cap = byte_cap
        self._stats = stats if stats is not None else FetchStats()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = UA
        # Size the connection pool to the worker count so concurrent fetches to one host
        # reuse connections instead of churning (urllib3 "Connection pool is full").
        adapter = HTTPAdapter(
            pool_connections=SCRAPE_WORKERS, pool_maxsize=SCRAPE_WORKERS
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def get(self, url: str, timeout: float = 20.0) -> str | None:
        host = urlsplit(url).hostname or "?"
        outcome = CONN_ERROR
        for attempt in range(self._retries):
            try:
                body = self._fetch_following(url, timeout)
                time.sleep(self._delay)
                self._stats.record(host, OK)
                return body
            except _FetchFailure as exc:
                outcome = exc.reason
                host = exc.host or host  # the failing HOP, not the original URL
                # The politeness delay applies to failures too. It used to, by accident
                # (these cases returned None down the success path); dropping it would
                # let a bot-wall serving redirect loops turn into unthrottled hammering
                # across every worker — the fastest way to earn a hard block.
                time.sleep(self._delay)
                break  # retrying cannot change this verdict
            except requests.RequestException as exc:
                outcome = _classify(exc)
                time.sleep(self._delay)
                if attempt == self._retries - 1:
                    break
                time.sleep(2**attempt)
        self._stats.record(host, outcome)
        return None

    def _fetch_following(self, url: str, timeout: float) -> str:
        # Follow redirects manually so EVERY hop is re-resolved, re-validated, and
        # re-pinned by _resolve_validated_ip. requests' own redirect following would
        # skip the guard and let an upstream 302 us at an internal host (SSRF).
        current = url
        for _hop in range(_MAX_REDIRECTS):
            host = urlsplit(current).hostname or ""
            ip, reason = _validate_ip(current)
            if ip is None:
                raise _FetchFailure(reason, host=host)
            # Fresh per-call session pinned to the validated IP (thread-safe).
            with _PinDNS(host, ip):
                resp = self._session.get(
                    current,
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                    headers=_referer_for(current),
                )
                if resp.is_redirect or resp.is_permanent_redirect:
                    location = resp.headers.get("Location")
                    resp.close()
                    if not location:
                        raise _FetchFailure(REDIRECT_NO_LOCATION, host=host)
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(8192):
                    total += len(chunk)
                    if total > self._byte_cap:
                        resp.close()
                        raise _FetchFailure(TOO_LARGE, host=host)  # oversized → refuse
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", "replace")
        raise _FetchFailure(TOO_MANY_REDIRECTS, host=urlsplit(current).hostname or "")

    def get_segment(
        self, url: str, range_header: str | None = None
    ) -> tuple[int, str, str | None, bytes] | None:
        """Fetch a media segment as bytes, relaying status + Range. None on failure."""
        ip = _resolve_validated_ip(url)
        if ip is None:
            return None
        host = urlsplit(url).hostname or ""
        headers = {"Range": range_header} if range_header else {}
        headers.update(_referer_for(url))
        try:
            with _PinDNS(host, ip):
                resp = self._session.get(
                    url,
                    headers=headers,
                    timeout=20,
                    stream=True,
                    allow_redirects=False,
                )
                if resp.is_redirect or resp.is_permanent_redirect:
                    resp.close()
                    return None  # signed segment URLs shouldn't redirect; refuse
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(65536):
                    total += len(chunk)
                    if total > SEGMENT_MAX_BYTES:
                        resp.close()
                        return None
                    chunks.append(chunk)
                return (
                    resp.status_code,
                    resp.headers.get("Content-Type", "video/mp2t"),
                    resp.headers.get("Content-Range"),
                    b"".join(chunks),
                )
        except requests.RequestException:
            return None

    def post(
        self,
        url: str,
        data: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> str | None:
        host = urlsplit(url).hostname or ""
        ip, reason = _validate_ip(url)
        if ip is None:
            self._stats.record(host, reason)
            return None
        body = urlencode(data).encode()
        # Sending pre-encoded bytes means requests won't auto-set the form Content-Type,
        # and servers (e.g. WordPress admin-ajax) then 400 — can't parse $_POST. Set it
        # ourselves; a caller can still override via `headers`.
        post_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        post_headers.update(headers or {})
        outcome = CONN_ERROR
        for attempt in range(self._retries):
            try:
                with _PinDNS(host, ip):
                    resp = self._session.post(
                        url,
                        data=body,
                        headers=post_headers,
                        timeout=timeout,
                        stream=True,
                        allow_redirects=False,
                    )
                    time.sleep(self._delay)
                    if resp.is_redirect or resp.is_permanent_redirect:
                        resp.close()
                        # admin-ajax POSTs shouldn't redirect; refuse
                        raise _FetchFailure(UNEXPECTED_REDIRECT, host=host)
                    resp.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_content(8192):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            resp.close()
                            raise _FetchFailure(TOO_LARGE, host=host)
                        chunks.append(chunk)
                    self._stats.record(host, OK)
                    return b"".join(chunks).decode("utf-8", "replace")
            except _FetchFailure as exc:
                outcome = exc.reason
                break  # retrying cannot change this verdict
            except requests.RequestException as exc:
                outcome = _classify(exc)
                if attempt == self._retries - 1:
                    break
                time.sleep(2**attempt)
        self._stats.record(host, outcome)
        return None
