from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
    # Blank means unset (docker-compose forwards `${SCRAPE_WORKERS:-}` as ""), not bad.
    if raw is None or not raw.strip():
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
HOST_BACKOFF = "host-backoff"  # refused by the HostPacer: host is shedding load


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


# --- reactive per-host pacing -------------------------------------------------
# A catalogue build aims a whole thread pool at one host (a scraper's pages, and the
# liveness resolver's second pass, all live on that source's domain), and a host that
# starts shedding load (429/503) turns that into a burst of self-inflicted failures —
# each a cam silently missing from the playlist. The pacer is the feedback loop:
# invisible until a host actually sheds, then it spaces requests and gates retries
# past the shedding window so they succeed instead of re-entering the burst.

_PACE_INTERVAL = 0.125  # spacing after one strike (8 req/s), doubling per strike
_PACE_INTERVAL_CAP = 4.0
_PACE_COOLDOWN = 2.0  # gate after a penalty, doubling per strike
_PACE_COOLDOWN_CAP = 60.0
_PACE_RETRY_AFTER_CAP = 300.0  # honour an explicit Retry-After up to this long
_PACE_DECAY = 30.0  # ungated quiet seconds that shed one strike
_PACE_BREAK_AT = 8  # strikes at which acquire() fails fast (one probe per period)
_PACE_MAX_WAIT = 30.0  # never queue a request longer than this — refuse instead
_PACE_MAX_HOSTS = 1024  # host names come from scraped HTML — bound them
_SHEDDING_STATUSES = (429, 503)  # the responses that mean "you, specifically: slower"
_SHEDDING_OUTCOMES = tuple(f"http-{c}" for c in _SHEDDING_STATUSES)


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a seconds-form Retry-After header. The HTTP-date form is deliberately
    ignored: it is rare on the hosts we meet, and a misparsed date is worse than
    letting the adaptive ladder find the window itself."""
    if not value:
        return None
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    return secs if secs > 0 else None


@dataclass
class _HostPace:
    strikes: int = 0
    gen: int = 0  # window generation: one escalation per burst, not per failure
    cooldown_until: float = 0.0
    next_allowed: float = 0.0  # the next reserved send slot while throttled
    quiet_since: float = 0.0  # decay anchor: quiet time accrues from here
    penalties: int = 0
    backoff_seconds: float = 0.0  # thread-seconds slept behind this host's gate


class HostPacer:
    """Reactive per-host politeness, shared by every build-side Fetcher.

    Purely sleep-based: a waiting thread holds nothing, so an exception path cannot
    leak capacity and no deadlock is possible even though the pacer spans nested
    thread pools. A host that has never been penalised is a dict miss — zero
    overhead and zero state until it answers 429/503.

    On a penalty the host gets a cooldown window (doubling per strike, capped) and
    subsequent requests are spaced (interval doubling per strike). A send slot is
    reserved AT OR AFTER the window end, so queued waiters release one interval
    apart instead of all firing the moment the window expires — the release burst
    is what re-trips a limiter, and slot spacing removes it without jitter. Strikes
    shed one per _PACE_DECAY of UNGATED quiet — never during a window, or a long
    window would decay its own strikes and the ladder could never reach the
    breaker. At _PACE_BREAK_AT strikes, or when a request would queue longer than
    _PACE_MAX_WAIT, acquire() refuses instead of waiting: the bulk of a
    hard-blocked source fails fast (as it did before pacing existed) while one
    probe per decay period keeps testing for recovery."""

    _clock: Callable[[], float]
    _sleep: Callable[[float], None]
    _lock: threading.Lock
    _hosts: dict[str, _HostPace]

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._hosts = {}

    @staticmethod
    def _interval(strikes: int) -> float:
        return min(_PACE_INTERVAL * 2 ** (strikes - 1), _PACE_INTERVAL_CAP)

    @staticmethod
    def _window(strikes: int) -> float:
        return min(_PACE_COOLDOWN * 2 ** (strikes - 1), _PACE_COOLDOWN_CAP)

    @staticmethod
    def _decay(s: _HostPace, now: float) -> None:
        # The anchor advances by whole periods (not to `now`): jumping it to `now`
        # would discard the remainder and silently slow every subsequent decay.
        if s.strikes and now > s.quiet_since:
            shed = int((now - s.quiet_since) / _PACE_DECAY)
            if shed:
                s.strikes = max(0, s.strikes - shed)
                s.quiet_since += shed * _PACE_DECAY
                if s.strikes == 0:
                    # A stale reservation must not poison the next episode.
                    s.next_allowed = 0.0

    @staticmethod
    def _rollback(s: _HostPace, my_slot: float | None, my_end: float) -> None:
        # Give back a reservation that will never be used — but only when we are
        # still the LAST reservation (nobody queued behind us). A refused request
        # that left its slot behind would build a phantom queue: one long window
        # can strand a whole pool's reservations, refuse the wave behind them off
        # the phantom depth, and fail-fast the source with no breaker and no
        # warning. Rolling back only-if-last keeps the common case exact without
        # tracking a queue.
        if my_slot is not None and s.next_allowed == my_end:
            s.next_allowed = my_slot

    def acquire(self, host: str) -> int | None:
        """Block until a request to `host` may start. Returns the generation stamp
        to hand back to penalise(), or None to refuse (fail fast): the breaker is
        open, or the wait would exceed _PACE_MAX_WAIT. That bound is per WAIT, not
        per call — successive new windows can extend a caller — but sustained
        shedding escalates to the breaker, which is what bounds the total."""
        my_slot: float | None = None
        my_end = 0.0
        while True:
            with self._lock:
                s = self._hosts.get(host)
                if s is None:
                    return 0  # never penalised: gen 0 matches a fresh entry's gen
                now = self._clock()
                self._decay(s, now)
                if s.strikes == 0:
                    return s.gen
                if s.strikes >= _PACE_BREAK_AT:
                    self._rollback(s, my_slot, my_end)
                    # Breaker open: fail fast, except one probe per decay period —
                    # without the probe, recovery could only be discovered by the
                    # NEXT build. next_allowed doubles as the probe spacer here
                    # (the normal queue is empty while everything is refused; a
                    # still-live pre-breaker reservation just delays the first
                    # probe by one interval, which is harmless).
                    if now >= s.cooldown_until and now >= s.next_allowed:
                        s.next_allowed = now + _PACE_DECAY
                        return s.gen
                    return None
                if my_slot is None or s.cooldown_until > my_slot:
                    # Reserve a send slot AT OR AFTER the window end, so waiters
                    # release one interval apart instead of bursting at expiry.
                    # Re-reserve only when a NEW window has invalidated our slot
                    # (rolling the stale one back if we were last); refusing
                    # happens WITHOUT reserving, which is what bounds the queue —
                    # a refused request must not push next_allowed further out.
                    self._rollback(s, my_slot, my_end)
                    slot = max(now, s.next_allowed, s.cooldown_until)
                    if slot - now > _PACE_MAX_WAIT:
                        return None
                    my_slot = slot
                    my_end = slot + self._interval(s.strikes)
                    s.next_allowed = my_end
                if now >= my_slot:
                    # The gen stamp is read HERE, at the final gate pass — stamped
                    # any earlier, a request that waited through a window would
                    # carry a stale gen and its failure could never escalate.
                    return s.gen
                wait = my_slot - now
                # Charged at decision time; a wait later invalidated by a new
                # window slightly overstates. Diagnostics, not billing.
                s.backoff_seconds += wait
            self._sleep(wait)

    def penalise(
        self, host: str, gen: int, retry_after: float | None = None
    ) -> tuple[int, float, bool]:
        """Record a 429/503 from `host`. Returns (strikes, window_seconds,
        escalated) for the caller to log OUTSIDE the pacer's lock."""
        with self._lock:
            now = self._clock()
            s = self._hosts.get(host)
            if s is None:
                if len(self._hosts) >= _PACE_MAX_HOSTS:
                    self._prune(now)
                if len(self._hosts) >= _PACE_MAX_HOSTS:
                    return (0, 0.0, False)  # absurd cardinality: stop tracking
                s = self._hosts[host] = _HostPace()
            self._decay(s, now)
            s.penalties += 1
            escalated = gen == s.gen
            if escalated:
                # First failure of a new burst. A straggler from an earlier burst
                # (stale gen) refreshes the window below but never escalates: its
                # failure is evidence about conditions at its START, and that
                # burst has already been counted. Capped just past the breaker so
                # failing probes pin the breaker open without inflating the
                # recovery time (decay is linear in strikes).
                s.strikes = min(s.strikes + 1, _PACE_BREAK_AT + 1)
                s.gen += 1
            elif s.strikes == 0:
                # A stale-gen failure landing on a fresh (or fully decayed) entry
                # still proves the host shed recently — without a strike, the
                # window set below would be ignored by acquire's fast path.
                s.strikes = 1
            window = self._window(max(s.strikes, 1))
            if retry_after is not None and retry_after > window:
                # An explicit "come back later" is respected up to the cap; it
                # never trips the breaker by itself (one stray edge node must not
                # kill the host for the whole build) — repeats climb the ladder.
                window = min(retry_after, _PACE_RETRY_AFTER_CAP)
            s.cooldown_until = max(s.cooldown_until, now + window)
            # Quiet time starts when the window ENDS — never let a window decay
            # its own strikes, or the breaker becomes unreachable.
            s.quiet_since = s.cooldown_until
            return (s.strikes, window, escalated)

    def _prune(self, now: float) -> None:
        # Called with the lock held, only when a new host would exceed the cap.
        # Frees hosts that recovered AND had their counters drained — so between
        # drains it may free nothing; the cap (stop tracking new hosts) is the
        # real bound, and drain() is what reclaims each cycle.
        for host, s in list(self._hosts.items()):
            self._decay(s, now)
            if s.strikes == 0 and not s.penalties and not s.backoff_seconds:
                del self._hosts[host]

    def drain(self) -> dict[str, dict[str, float]]:
        """Snapshot and reset the per-host counters, as
        {host: {"penalties": n, "backoff_seconds": s}} (backoff is thread-seconds
        slept behind the gate). Fully-recovered entries are dropped, which is what
        bounds the map across cycles."""
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            now = self._clock()
            for host, s in list(self._hosts.items()):
                self._decay(s, now)
                if s.penalties or s.backoff_seconds:
                    out[host] = {
                        "penalties": s.penalties,
                        "backoff_seconds": round(s.backoff_seconds, 1),
                    }
                    s.penalties = 0
                    s.backoff_seconds = 0.0
                if s.strikes == 0:
                    del self._hosts[host]
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
    _pacer: HostPacer | None
    _retry_only_shedding: bool

    def __init__(
        self,
        delay: float = 1.0,
        retries: int = 3,
        byte_cap: int = MAX_BYTES,
        stats: FetchStats | None = None,
        pacer: HostPacer | None = None,
        retry_only_shedding: bool = False,
    ) -> None:
        self._delay = delay
        self._retries = retries
        self._byte_cap = byte_cap
        self._stats = stats if stats is not None else FetchStats()
        # None = unpaced (the serve-time fetchers: a build-inflicted cooldown must
        # never stall a player request). retry_only_shedding restricts retries to
        # 429/503 — the probe fetcher's dead-cam long tail (404s, timeouts) must
        # cost one attempt, while a shedding host earns the post-cooldown retry.
        self._pacer = pacer
        self._retry_only_shedding = retry_only_shedding
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
                # EXCEPT a pacer refusal: no request was made, and a source failing
                # fast makes thousands of them — delaying each would burn minutes of
                # pool time being polite about requests that never happened.
                if exc.reason != HOST_BACKOFF:
                    time.sleep(self._delay)
                break  # retrying cannot change this verdict
            except requests.RequestException as exc:
                outcome = _classify(exc)
                # Attribute a mid-redirect-chain failure to the hop that failed, not
                # the URL the caller asked for. _FetchFailure carries its hop above;
                # requests' exceptions carry the failing response/request instead.
                hop = getattr(exc.response, "url", None) or getattr(
                    exc.request, "url", None
                )
                if hop:
                    host = urlsplit(hop).hostname or host
                time.sleep(self._delay)
                if attempt == self._retries - 1:
                    break
                if self._retry_only_shedding and outcome not in _SHEDDING_OUTCOMES:
                    break  # a dead cam costs one attempt; only shedding earns more
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
            # Pace BEFORE the DNS check: a request may queue behind a cooldown for
            # seconds, and a resolution done first would be stale by the connect.
            gen = 0
            if self._pacer is not None:
                acquired = self._pacer.acquire(host)
                if acquired is None:
                    raise _FetchFailure(HOST_BACKOFF, host=host)
                gen = acquired
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
                if resp.status_code >= 400:
                    # Streamed response: close before raising, or the pooled
                    # connection is only returned by GC — a shedding episode would
                    # leak one per failure against a pool of SCRAPE_WORKERS.
                    resp.close()
                    self._penalise_if_shedding(resp, host, gen)
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

    def _penalise_if_shedding(
        self, resp: requests.Response, host: str, gen: int
    ) -> None:
        """Feed a 429/503 back to the pacer; log outside its lock. The WARNINGs fire
        once per breaker trip / oversized Retry-After, not per refused request —
        refusals themselves are counted as host-backoff outcomes in the stats."""
        if self._pacer is None or resp.status_code not in _SHEDDING_STATUSES:
            return
        strikes, window, escalated = self._pacer.penalise(
            host, gen, _retry_after_seconds(resp.headers.get("Retry-After"))
        )
        if escalated and strikes == _PACE_BREAK_AT:
            # == not >=: failing breaker probes sit at the cap (BREAK_AT + 1) and
            # would otherwise re-log this for the whole outage. Only the 7 -> 8
            # transition — the trip itself — warns; a re-trip after decay warns
            # again, so a long outage reads as a timeline, not a firehose.
            log.warning(
                "%s: %d consecutive shedding windows — failing fast (host-backoff)",
                host,
                strikes,
            )
        elif escalated and window > _PACE_COOLDOWN_CAP:
            # Only an explicit Retry-After can exceed the ladder's cap; honouring
            # it means refusing this host for minutes, which deserves a WARNING —
            # behaviourally a breaker trip, just an obedient one. `escalated`
            # keeps the burst's stragglers from each re-warning.
            log.warning(
                "%s: Retry-After %.0fs — refusing the host until it passes",
                host,
                window,
            )
        else:
            log.debug("%s: http-%d — backing off %.1fs", host, resp.status_code, window)

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
        body = urlencode(data).encode()
        # Sending pre-encoded bytes means requests won't auto-set the form Content-Type,
        # and servers (e.g. WordPress admin-ajax) then 400 — can't parse $_POST. Set it
        # ourselves; a caller can still override via `headers`.
        post_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        post_headers.update(headers or {})
        outcome = CONN_ERROR
        for attempt in range(self._retries):
            gen = 0
            if self._pacer is not None:
                acquired = self._pacer.acquire(host)
                if acquired is None:
                    outcome = HOST_BACKOFF
                    break
                gen = acquired
            # Validate AFTER the acquire, like get(): a request can queue behind a
            # cooldown for seconds, and a resolution done first would be stale.
            ip, reason = _validate_ip(url)
            if ip is None:
                outcome = reason
                break
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
                    if resp.is_redirect or resp.is_permanent_redirect:
                        resp.close()
                        # admin-ajax POSTs shouldn't redirect; refuse
                        raise _FetchFailure(UNEXPECTED_REDIRECT, host=host)
                    if resp.status_code >= 400:
                        resp.close()  # streamed: release the pooled connection
                        self._penalise_if_shedding(resp, host, gen)
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
                    # Politeness delay AFTER the body, like get() — it used to run
                    # mid-response, which held the connection through the sleep.
                    time.sleep(self._delay)
                    return b"".join(chunks).decode("utf-8", "replace")
            except _FetchFailure as exc:
                outcome = exc.reason
                time.sleep(self._delay)  # failures pay the politeness delay too
                break  # retrying cannot change this verdict
            except requests.RequestException as exc:
                outcome = _classify(exc)
                time.sleep(self._delay)
                if attempt == self._retries - 1:
                    break
                if self._retry_only_shedding and outcome not in _SHEDDING_OUTCOMES:
                    break  # same contract as get(): the flag is Fetcher-wide
                time.sleep(2**attempt)
        self._stats.record(host, outcome)
        return None
