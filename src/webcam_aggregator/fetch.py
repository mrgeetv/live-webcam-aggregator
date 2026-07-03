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


def _resolve_validated_ip(url: str) -> str | None:
    """Resolve the URL's host ONCE and validate every returned IP. Returns a single
    safe IP to connect to, or None if the scheme is unsupported, the host won't
    resolve, or ANY resolved IP is private/loopback/link-local/reserved/multicast.

    This is the SSRF check AND the source of truth for the connection IP: the caller
    pins the connection to the returned IP so there is no second DNS lookup between
    validation and connect (closing the DNS-rebinding TOCTOU window). Returning the
    first IP (rather than re-resolving) is what makes validate-then-pin atomic."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    chosen: str | None = None
    for info in infos:
        ip_str = str(info[4][0])  # sockaddr[0] is the IP literal
        if _ip_is_unsafe(ip_str):
            return None  # any unsafe IP poisons the whole host — refuse
        if chosen is None:
            chosen = ip_str
    return chosen


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


class Fetcher:
    _session: requests.Session
    _delay: float
    _retries: int
    _byte_cap: int

    def __init__(
        self, delay: float = 1.0, retries: int = 3, byte_cap: int = MAX_BYTES
    ) -> None:
        self._delay = delay
        self._retries = retries
        self._byte_cap = byte_cap
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
        for attempt in range(self._retries):
            try:
                body = self._fetch_following(url, timeout)
                time.sleep(self._delay)
                return body
            except requests.RequestException:
                if attempt == self._retries - 1:
                    return None
                time.sleep(2**attempt)
        return None

    def _fetch_following(self, url: str, timeout: float) -> str | None:
        # Follow redirects manually so EVERY hop is re-resolved, re-validated, and
        # re-pinned by _resolve_validated_ip. requests' own redirect following would
        # skip the guard and let an upstream 302 us at an internal host (SSRF).
        current = url
        for _hop in range(_MAX_REDIRECTS):
            ip = _resolve_validated_ip(current)
            if ip is None:
                return None
            host = urlsplit(current).hostname or ""
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
                        return None
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(8192):
                    total += len(chunk)
                    if total > self._byte_cap:
                        resp.close()
                        return None  # oversized → refuse
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", "replace")
        return None  # too many redirects

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
        ip = _resolve_validated_ip(url)
        if ip is None:
            return None
        host = urlsplit(url).hostname or ""
        body = urlencode(data).encode()
        # Sending pre-encoded bytes means requests won't auto-set the form Content-Type,
        # and servers (e.g. WordPress admin-ajax) then 400 — can't parse $_POST. Set it
        # ourselves; a caller can still override via `headers`.
        post_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        post_headers.update(headers or {})
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
                        return None  # admin-ajax POSTs shouldn't redirect; refuse
                    resp.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_content(8192):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            resp.close()
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks).decode("utf-8", "replace")
            except requests.RequestException:
                if attempt == self._retries - 1:
                    return None
                time.sleep(2**attempt)
        return None
