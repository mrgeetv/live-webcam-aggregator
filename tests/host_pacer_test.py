"""HostPacer: reactive per-host pacing + the Fetcher paths that feed it.

The pacer's decisions are exercised against an injected clock whose fake sleep
ADVANCES it — no real waiting — plus two real-thread tests at the bottom (tiny
constants, hard wall-clock assertions) for the bugs only concurrency can catch."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, override
from unittest.mock import patch

import pytest
import requests

import webcam_aggregator.fetch as fetch_mod
from webcam_aggregator.fetch import (
    _PACE_BREAK_AT,  # pyright: ignore[reportPrivateUsage]
    _PACE_MAX_HOSTS,  # pyright: ignore[reportPrivateUsage]
    _retry_after_seconds,  # pyright: ignore[reportPrivateUsage]
    Fetcher,
    FetchStats,
    HostPacer,
    thread_map,
)


class _FakeClock:
    t: float

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_pacer() -> tuple[HostPacer, _FakeClock, list[float]]:
    clock = _FakeClock()
    sleeps: list[float] = []

    def sleep(d: float) -> None:
        sleeps.append(d)
        clock.t += d

    return HostPacer(clock=clock, sleep=sleep), clock, sleeps


def _escalate(p: HostPacer, host: str, times: int) -> None:
    """Drive the ladder up `times` strikes with correctly-stamped generations."""
    for gen in range(times):
        p.penalise(host, gen)


# ---------------------------------------------------------------------------
# HostPacer decisions (fake clock, no real sleeps)
# ---------------------------------------------------------------------------


def test_healthy_host_is_untouched() -> None:
    p, _clock, sleeps = _make_pacer()
    assert p.acquire("h.example") == 0
    assert sleeps == []
    assert p._hosts == {}  # pyright: ignore[reportPrivateUsage]  # no state created


def test_first_penalty_gates_then_spaces() -> None:
    p, _clock, sleeps = _make_pacer()
    strikes, window, escalated = p.penalise("h.example", 0)
    assert (strikes, window, escalated) == (1, 2.0, True)
    assert p.acquire("h.example") == 1  # waits out the 2s window
    assert sleeps == [2.0]
    # Next requests are spaced at the strike-1 interval, not free-for-all.
    assert p.acquire("h.example") == 1
    assert p.acquire("h.example") == 1
    assert sleeps[-1] == pytest.approx(0.125)


def test_one_escalation_per_burst_not_per_failure() -> None:
    p, _clock, _sleeps = _make_pacer()
    assert p.penalise("h.example", 0) == (1, 2.0, True)
    # Straggler from the SAME burst (stale gen): extends, never escalates.
    strikes, _window, escalated = p.penalise("h.example", 0)
    assert (strikes, escalated) == (1, False)
    # A failure from a request started in the NEW generation escalates.
    assert p.penalise("h.example", 1) == (2, 4.0, True)


def test_request_that_waited_through_a_window_escalates_on_failure() -> None:
    """The gen stamp must be read at the final gate pass: a request that slept
    through the window and then failed is evidence about the CURRENT window."""
    p, _clock, _sleeps = _make_pacer()
    p.penalise("h.example", 0)
    gen = p.acquire("h.example")  # sleeps out the window, stamped afterwards
    assert gen == 1
    assert p.penalise("h.example", gen) == (2, 4.0, True)


def test_stale_gen_failure_on_a_fresh_entry_still_throttles() -> None:
    """A failure landing after the entry decayed away (or was drained) proves the
    host shed recently — without a strike, acquire's fast path would ignore the
    window the penalty just set."""
    p, _clock, _sleeps = _make_pacer()
    strikes, _window, escalated = p.penalise("h.example", 7)  # stale gen, no entry
    assert (strikes, escalated) == (1, False)
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    assert s.cooldown_until > 0


def test_no_decay_while_gated() -> None:
    """A cooldown window must not shed its own strikes — with decay anchored on the
    penalty time, windows >= 32s would out-decay escalation and the breaker could
    never be reached (the unbounded-build hole)."""
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", 5)  # strikes 5, window 32s
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    assert s.strikes == 5
    clock.t += 31.0  # 31s pass INSIDE the window
    p.penalise("h.example", 0)  # stale-gen straggler runs decay first
    assert s.strikes == 5  # nothing shed while gated
    clock.t = s.quiet_since + 60.0  # 60s of genuinely quiet, ungated time
    p.acquire("h.example")
    assert s.strikes == 3  # two 30s periods shed two strikes


def test_decay_anchor_advances_by_whole_periods() -> None:
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", 3)
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s.quiet_since + 45.0  # 1.5 decay periods
    p.acquire("h.example")
    assert s.strikes == 2  # one period shed one strike…
    clock.t += 15.0  # …and the remainder still counts toward the next
    p.acquire("h.example")
    assert s.strikes == 1


def test_breaker_refuses_then_recovers_via_decay() -> None:
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", _PACE_BREAK_AT)
    assert p.acquire("h.example") is None  # breaker open: fail fast
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s.quiet_since + 30.0  # one quiet period: one strike shed
    gen = p.acquire("h.example")  # below the breaker again — requests flow
    assert gen is not None
    # If that request fails, the breaker re-trips immediately…
    p.penalise("h.example", gen)
    assert p.acquire("h.example") is None
    # …but genuine recovery decays all the way back to unthrottled.
    s2 = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s2.quiet_since + 30.0 * _PACE_BREAK_AT
    assert p.acquire("h.example") is not None
    assert s2.strikes == 0


def test_breaker_lets_one_probe_per_decay_period() -> None:
    """While the breaker is open, one request per decay period still goes out —
    without it, recovery could only be discovered by the NEXT build."""
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", _PACE_BREAK_AT)
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s.cooldown_until + 1.0  # window over; strikes not yet decayed
    gen = p.acquire("h.example")  # the probe
    assert gen is not None
    assert p.acquire("h.example") is None  # next probe only after a full period
    # A failing probe pins the breaker open, capped just past the threshold so
    # recovery time doesn't inflate with every failed probe.
    strikes, _window, escalated = p.penalise("h.example", gen)
    assert escalated and strikes == _PACE_BREAK_AT + 1
    clock.t = s.quiet_since + 1.0
    strikes2, _w2, _e2 = p.penalise("h.example", s.gen)
    assert strikes2 == _PACE_BREAK_AT + 1  # still capped


def test_long_wait_refuses_without_reserving() -> None:
    """Refusing must not push next_allowed out — a refused request that still
    reserved would let the queue run away and slow everyone behind it."""
    p, _clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", 5)  # window 32s > _PACE_MAX_WAIT
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    before = s.next_allowed
    assert p.acquire("h.example") is None
    assert s.next_allowed == before


def test_queue_deeper_than_max_wait_refuses() -> None:
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "h.example", 2)  # throttled, window 4s
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s.cooldown_until  # window over; only spacing applies
    s.next_allowed = clock.t + 40.0  # queue already 40s deep
    assert p.acquire("h.example") is None
    assert s.next_allowed == clock.t + 40.0


def test_waiters_reserve_past_the_window_end() -> None:
    """A queued waiter's slot lands AT OR AFTER the window end — otherwise every
    waiter's slot falls inside the window and they all fire the moment it
    expires, which is exactly the burst that re-trips a limiter."""
    p, _clock, sleeps = _make_pacer()
    p.penalise("h.example", 0)  # window until t+2, interval 0.125
    assert p.acquire("h.example") == 1  # slot at the window end: waits 2.0
    assert p.acquire("h.example") == 1  # NEXT slot: 0.125 after the end
    assert sleeps == [pytest.approx(2.0), pytest.approx(0.125)]


def test_new_window_mid_wait_re_reserves_and_rolls_back() -> None:
    """A window opened mid-wait invalidates our slot: the stale reservation is
    rolled back (we were last) and a new slot is taken past the new window —
    leaking it instead would build a phantom queue that refuses the next wave."""
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer_ref: list[HostPacer] = []
    injected: list[int] = []

    def sleep(d: float) -> None:
        sleeps.append(d)
        clock.t += d
        if not injected:
            injected.append(1)
            pacer_ref[0].penalise("h.example", 1)  # a NEW window opens mid-wait

    p = HostPacer(clock=clock, sleep=sleep)
    pacer_ref.append(p)
    p.penalise("h.example", 0)  # strikes 1: window until 1002
    gen = p.acquire("h.example")
    assert gen == 2  # stamped after the SECOND window
    assert sleeps == [pytest.approx(2.0), pytest.approx(4.0)]
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    # One live reservation only: the new slot's interval (strikes 2 → 0.25),
    # anchored at the second window's end — the stale slot was given back.
    assert s.next_allowed == pytest.approx(1006.25)


def test_retry_after_is_respected_but_never_trips_the_breaker_alone() -> None:
    p, _clock, _sleeps = _make_pacer()
    # Larger than the ladder window: honoured (fail-fast while it lasts), but
    # still ONE strike — a single stray edge node must not open the breaker.
    strikes, window, _ = p.penalise("h.example", 0, retry_after=120.0)
    assert (strikes, window) == (1, 120.0)
    assert p.acquire("h.example") is None  # too long to queue: fail fast
    # An absurd Retry-After is capped, not obeyed forever.
    p2, _c2, _s2 = _make_pacer()
    assert p2.penalise("h.example", 0, retry_after=86400.0)[1] == 300.0
    # Smaller than the ladder window: the ladder wins.
    p3, _c3, _s3 = _make_pacer()
    assert p3.penalise("h.example", 0, retry_after=1.0)[1] == 2.0


def test_retry_after_seconds_parses_seconds_only() -> None:
    assert _retry_after_seconds("5") == 5.0
    assert _retry_after_seconds(" 10 ") == 10.0
    assert _retry_after_seconds("0") is None
    assert _retry_after_seconds("") is None
    assert _retry_after_seconds(None) is None
    # HTTP-date form is deliberately ignored (see _retry_after_seconds).
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None


def test_drain_reports_resets_and_drops_recovered_hosts() -> None:
    p, clock, _sleeps = _make_pacer()
    p.penalise("h.example", 0)
    p.penalise("h.example", 0)  # straggler: counted as a penalty too
    p.acquire("h.example")  # accrues backoff
    s = p._hosts["h.example"]  # pyright: ignore[reportPrivateUsage]
    drained = p.drain()
    assert drained["h.example"]["penalties"] == 2
    assert drained["h.example"]["backoff_seconds"] > 0
    assert p.drain() == {}  # counters reset; still-throttled entry kept
    clock.t = s.quiet_since + 60.0  # decay to zero…
    assert p.drain() == {}
    assert p._hosts == {}  # pyright: ignore[reportPrivateUsage]  # …entry dropped


def test_host_cardinality_is_bounded() -> None:
    p, _clock, _sleeps = _make_pacer()
    for i in range(_PACE_MAX_HOSTS + 50):
        p.penalise(f"h{i}.example", 0)
    hosts = p._hosts  # pyright: ignore[reportPrivateUsage]
    assert len(hosts) == _PACE_MAX_HOSTS
    # Untracked overflow hosts still fetch — unpaced beats unbounded.
    assert p.acquire("overflow.example") == 0


def test_prune_frees_drained_and_recovered_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the cap, a host that was drained AND has decayed to zero is evicted to
    make room — a still-throttled or undrained one is not."""
    monkeypatch.setattr(fetch_mod, "_PACE_MAX_HOSTS", 2)
    p, clock, _sleeps = _make_pacer()
    p.penalise("old.example", 0)
    p.drain()  # counters cleared; entry kept (still throttled)
    clock.t += 120.0  # old.example decays to zero (lazily, at next touch)
    p.penalise("busy.example", 0)  # fits: cap not yet reached
    p.penalise("new.example", 0)  # at cap → prune frees old.example
    hosts = p._hosts  # pyright: ignore[reportPrivateUsage]
    assert "old.example" not in hosts
    assert set(hosts) == {"busy.example", "new.example"}


# ---------------------------------------------------------------------------
# Fetcher integration (fake sessions, patched module sleep)
# ---------------------------------------------------------------------------


def _addrinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webcam_aggregator.fetch.socket.getaddrinfo",
        lambda *_a, **_k: [(None, None, None, None, ("93.184.216.34", 0))],
    )


class _Resp:
    status_code: int
    headers: dict[str, str]
    is_redirect: bool = False
    is_permanent_redirect: bool = False
    body: bytes

    def __init__(self, status: int, body: bytes = b"ok") -> None:
        self.status_code = status
        self.headers = {}
        self.body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            real = requests.Response()
            real.status_code = self.status_code
            raise requests.HTTPError(response=real)

    def iter_content(self, _n: int) -> Any:
        return iter([self.body])

    def close(self) -> None: ...


def test_fetcher_503_penalises_then_recovers_on_the_gated_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the pacer: the retry waits out the window and SUCCEEDS,
    instead of firing straight back into the burst and losing the cam."""
    _addrinfo(monkeypatch)
    p, _clock, sleeps = _make_pacer()
    responses = [_Resp(503), _Resp(200, b"body")]
    monkeypatch.setattr(
        "requests.Session.get", lambda _self, _url, **_k: responses.pop(0)
    )
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=2, stats=s, pacer=p)
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.get("https://shed.example/x") == "body"
    assert s.drain() == {"shed.example": {"ok": 1}}  # one outcome per call
    assert p.drain()["shed.example"]["penalties"] == 1
    assert 2.0 in sleeps  # the retry actually waited out the window


def test_fetcher_breaker_refusal_is_fast_and_skips_politeness_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _addrinfo(monkeypatch)
    p, _clock, _sleeps = _make_pacer()
    _escalate(p, "shed.example", _PACE_BREAK_AT)

    def _no_call(_self: object, _url: str, **_k: object) -> Any:
        raise AssertionError("a refused request must never reach the network")

    monkeypatch.setattr("requests.Session.get", _no_call)
    s = FetchStats()
    f = Fetcher(delay=1.0, retries=3, stats=s, pacer=p)
    with patch("webcam_aggregator.fetch.time.sleep") as slept:
        assert f.get("https://shed.example/x") is None
    assert s.drain() == {"shed.example": {"host-backoff": 1}}
    # No request was made, so no politeness delay is owed — thousands of
    # fail-fast refusals must not burn a second each.
    assert not any(c.args and c.args[0] == 1.0 for c in slept.call_args_list)


def test_retry_only_shedding_gives_dead_cams_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _addrinfo(monkeypatch)
    calls = [0]

    def _get_404(_self: object, _url: str, **_k: object) -> _Resp:
        calls[0] += 1
        return _Resp(404)

    monkeypatch.setattr("requests.Session.get", _get_404)
    f = Fetcher(delay=0.0, retries=2, retry_only_shedding=True)
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.get("https://cdn.example/gone.m3u8") is None
    assert calls[0] == 1  # a 404 is a verdict, not a queue to wait behind

    def _get_503(_self: object, _url: str, **_k: object) -> _Resp:
        calls[0] += 1
        return _Resp(503)

    calls[0] = 0
    monkeypatch.setattr("requests.Session.get", _get_503)
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.get("https://cdn.example/busy.m3u8") is None
    assert calls[0] == 2  # shedding earns the post-cooldown retry


def test_get_segment_bypasses_the_pacer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve hot path: even a tripped breaker must not touch segment relay."""
    _addrinfo(monkeypatch)
    p, _clock, _sleeps = _make_pacer()
    _escalate(p, "cdn.example", _PACE_BREAK_AT)

    class _Seg:
        status_code: int = 200
        headers: dict[str, str] = {"Content-Type": "video/mp2t"}
        is_redirect: bool = False
        is_permanent_redirect: bool = False

        def iter_content(self, _n: int) -> Any:
            return iter([b"seg"])

        def close(self) -> None: ...

    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _Seg())
    f = Fetcher(delay=0.0, retries=1, pacer=p)
    got = f.get_segment("https://cdn.example/seg.ts")
    assert got is not None and got[3] == b"seg"


def test_post_is_paced_and_penalised(monkeypatch: pytest.MonkeyPatch) -> None:
    _addrinfo(monkeypatch)
    p, _clock, _sleeps = _make_pacer()
    monkeypatch.setattr("requests.Session.post", lambda _self, _url, **_k: _Resp(503))
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=1, stats=s, pacer=p)
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.post("https://shed.example/ajax", {"a": "b"}) is None
    assert s.drain() == {"shed.example": {"http-503": 1}}
    assert p.drain()["shed.example"]["penalties"] == 1
    # Breaker open → refused before the request is built.
    _escalate(p, "shed.example", _PACE_BREAK_AT)
    monkeypatch.setattr(
        "requests.Session.post",
        lambda _self, _url, **_k: (_ for _ in ()).throw(AssertionError("no call")),
    )
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.post("https://shed.example/ajax", {"a": "b"}) is None
    assert s.drain() == {"shed.example": {"host-backoff": 1}}


def test_breaker_warning_fires_once_per_trip(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The trip (7 -> 8) warns; the failing probes pinned at the cap must not —
    a host blocked for a whole build would otherwise log one WARNING per probe."""
    _addrinfo(monkeypatch)
    p, clock, _sleeps = _make_pacer()
    _escalate(p, "shed.example", _PACE_BREAK_AT - 1)
    s = p._hosts["shed.example"]  # pyright: ignore[reportPrivateUsage]
    clock.t = s.cooldown_until + 1.0  # window over; strikes not yet decayed
    monkeypatch.setattr("requests.Session.get", lambda _self, _url, **_k: _Resp(503))
    f = Fetcher(delay=0.0, retries=1, pacer=p)
    with (
        caplog.at_level(logging.WARNING, logger="webcam-aggregator.fetch"),
        patch("webcam_aggregator.fetch.time.sleep"),
    ):
        assert f.get("https://shed.example/x") is None  # 8th strike: the trip
        clock.t = s.cooldown_until + 1.0  # into the probe window
        assert f.get("https://shed.example/y") is None  # failing probe (pinned)
    trips = [r for r in caplog.records if "failing fast" in r.getMessage()]
    assert len(trips) == 1


def test_retry_after_warning_not_repeated_by_stragglers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p, _clock, _sleeps = _make_pacer()
    f = Fetcher(delay=0.0, retries=1, pacer=p)
    resp = requests.Response()
    resp.status_code = 503
    resp.headers["Retry-After"] = "120"
    shed = f._penalise_if_shedding  # pyright: ignore[reportPrivateUsage]
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.fetch"):
        shed(resp, "shed.example", 0)
        # In-flight straggler from the same burst (stale gen): counted, silent.
        shed(resp, "shed.example", 0)
    warns = [r for r in caplog.records if "Retry-After" in r.getMessage()]
    assert len(warns) == 1


def test_post_honours_retry_only_shedding(monkeypatch: pytest.MonkeyPatch) -> None:
    _addrinfo(monkeypatch)
    calls = [0]

    def _post_500(_self: object, _url: str, **_k: object) -> _Resp:
        calls[0] += 1
        return _Resp(500)

    monkeypatch.setattr("requests.Session.post", _post_500)
    f = Fetcher(delay=0.0, retries=2, retry_only_shedding=True)
    with patch("webcam_aggregator.fetch.time.sleep"):
        assert f.post("https://x.example/ajax", {"a": "b"}) is None
    assert calls[0] == 1  # a 500 is not shedding: no second attempt


# ---------------------------------------------------------------------------
# Real threads: the bugs only concurrency can catch (wedged waits, lost wakes,
# the release burst)
# ---------------------------------------------------------------------------


def test_queued_waiters_release_spaced_not_in_a_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three real threads queue behind one window; their acquires must return one
    interval apart, not together at expiry — the release burst is what re-trips
    a limiter. Generous margins: slots are 0.2s apart, we assert >= 0.1s."""
    monkeypatch.setattr(fetch_mod, "_PACE_COOLDOWN", 0.3)
    monkeypatch.setattr(fetch_mod, "_PACE_INTERVAL", 0.2)
    p = HostPacer()
    releases: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        assert p.acquire("h.example") is not None
        with lock:
            releases.append(time.monotonic())

    t0 = time.monotonic()
    p.penalise("h.example", 0)
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(releases) == 3
    releases.sort()
    # Assert each release against its ABSOLUTE slot (window end + i intervals):
    # scheduling delays can only push a release later, so this can't flake the
    # way a pairwise-gap assertion can (an early release delayed into the next
    # one's slot shrinks the observed gap but never violates the slots).
    for i, released in enumerate(releases):
        assert released >= t0 + 0.3 + 0.2 * i


def test_sixteen_workers_against_a_shedding_host_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """50 URLs, 16 real threads, a host that sheds intermittently plus timeouts and
    a mid-body error — the map must complete promptly with consistent counters.
    Catches: a wait that never wakes, a penalty that never fires, an exception
    path that wedges the pacer."""
    _addrinfo(monkeypatch)
    monkeypatch.setattr(fetch_mod, "_PACE_COOLDOWN", 0.02)
    monkeypatch.setattr(fetch_mod, "_PACE_INTERVAL", 0.005)
    monkeypatch.setattr(fetch_mod, "_PACE_DECAY", 0.5)
    monkeypatch.setattr(fetch_mod, "_PACE_MAX_WAIT", 1.0)
    p = HostPacer()  # real clock, real sleeps — constants above keep them tiny

    lock = threading.Lock()
    calls = [0]

    class _MidBodyBoom(_Resp):
        @override
        def iter_content(self, _n: int) -> Any:
            def _gen() -> Any:
                yield b"partial"
                raise requests.ConnectionError("mid-body")

            return _gen()

    def scripted(_self: object, url: str, **_k: object) -> Any:
        with lock:
            calls[0] += 1
            n = calls[0]
        if url.endswith("/t"):
            raise requests.Timeout("slow")
        if url.endswith("/b"):
            return _MidBodyBoom(200)
        return _Resp(503) if n % 4 == 0 else _Resp(200, b"ok")

    monkeypatch.setattr("requests.Session.get", scripted)
    s = FetchStats()
    f = Fetcher(delay=0.0, retries=2, stats=s, pacer=p)
    urls = [f"https://shed.example/{i}" for i in range(46)]
    urls += ["https://shed.example/t", "https://shed.example/t"]
    urls += ["https://shed.example/b", "https://shed.example/b"]

    t0 = time.monotonic()
    results = thread_map(f.get, urls, workers=16)
    elapsed = time.monotonic() - t0

    assert elapsed < 15.0  # hard deadline: nothing wedged
    assert len(results) == 50  # every URL produced a verdict
    drained = s.drain()["shed.example"]
    assert sum(drained.values()) == 50  # one outcome per call, none lost
    assert p.drain()["shed.example"]["penalties"] >= 1  # 503s actually fed back
