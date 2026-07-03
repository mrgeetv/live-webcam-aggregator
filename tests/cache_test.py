from __future__ import annotations

import time
import threading

from webcam_aggregator.cache import ResolveCache, DEFAULT_TTL, TTL_FACTOR, NEGATIVE_TTL
from webcam_aggregator.extractors.base import Resolved


class _Clock:
    t: float

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _resolved(
    url: str = "http://example.com/stream", ttl: int | None = 100
) -> Resolved:
    return Resolved(url=url, stream_type="hls", ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# 1. Single-flight: 5 concurrent threads; resolver runs exactly once
# ---------------------------------------------------------------------------


def test_single_flight_concurrent() -> None:
    clock = _Clock()
    call_count = 0
    call_count_lock = threading.Lock()

    def slow_resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        time.sleep(0.05)
        with call_count_lock:
            call_count += 1
        return _resolved()

    cache = ResolveCache(slow_resolve, clock=clock)
    results: list[Resolved | None] = [None] * 5
    barrier = threading.Barrier(5)

    def worker(idx: int) -> None:
        barrier.wait()  # all threads start at the same instant
        results[idx] = cache.get("cam1", "http://t")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1, f"resolver called {call_count} times, expected 1"
    assert all(r is not None for r in results)
    assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# 2. Cache hit: second get within TTL does NOT call resolver again
# ---------------------------------------------------------------------------


def test_cache_hit_no_second_resolve() -> None:
    clock = _Clock()
    call_count = 0

    def counting_resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        return _resolved(ttl=100)

    cache = ResolveCache(counting_resolve, clock=clock)
    r1 = cache.get("cam1", "http://t")
    r2 = cache.get("cam1", "http://t")

    assert call_count == 1
    assert r1 == r2


# ---------------------------------------------------------------------------
# 3. TTL expiry: advance clock past ttl*0.8; next get re-resolves
# ---------------------------------------------------------------------------


def test_ttl_expiry_triggers_re_resolve() -> None:
    clock = _Clock()
    call_count = 0
    ttl = 100

    def counting_resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        return _resolved(ttl=ttl)

    cache = ResolveCache(counting_resolve, clock=clock)
    cache.get("cam1", "http://t")
    assert call_count == 1

    # Advance past the cached TTL window (ttl * TTL_FACTOR = 80s)
    clock.t = ttl * TTL_FACTOR + 1.0

    cache.get("cam1", "http://t")
    assert call_count == 2


# ---------------------------------------------------------------------------
# 4. Negative cache: failed resolve → None; second call within 60s doesn't re-resolve
# ---------------------------------------------------------------------------


def test_negative_cache() -> None:
    clock = _Clock()
    call_count = 0

    def failing_resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("stream unavailable")

    cache = ResolveCache(failing_resolve, clock=clock)
    r1 = cache.get("cam1", "http://t")
    assert r1 is None
    assert call_count == 1

    # Immediate second call — still within negative TTL
    r2 = cache.get("cam1", "http://t")
    assert r2 is None
    assert call_count == 1, "resolver should NOT be called again within negative TTL"


def test_negative_cache_expires() -> None:
    clock = _Clock()
    call_count = 0

    def failing_resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("stream unavailable")

    cache = ResolveCache(failing_resolve, clock=clock)
    cache.get("cam1", "http://t")
    assert call_count == 1

    # Advance past the negative TTL
    clock.t = NEGATIVE_TTL + 1.0
    cache.get("cam1", "http://t")
    assert call_count == 2


# ---------------------------------------------------------------------------
# 5. LRU eviction: cap=2; insert 3 ids; first is evicted; its get re-resolves
# ---------------------------------------------------------------------------


def test_lru_eviction() -> None:
    clock = _Clock()
    resolve_counts: dict[str, int] = {}

    def counting_resolve(entry_id: str, _target_url: str) -> Resolved:
        resolve_counts[entry_id] = resolve_counts.get(entry_id, 0) + 1
        return _resolved(url=f"http://example.com/{entry_id}")

    cache = ResolveCache(counting_resolve, clock=clock, cap=2)

    cache.get("cam1", "http://t")
    cache.get("cam2", "http://t")
    cache.get("cam3", "http://t")  # cam1 should be evicted (LRU)

    # cam1 was evicted — its get should trigger a fresh resolve
    cache.get("cam1", "http://t")
    assert (
        resolve_counts["cam1"] == 2
    ), "cam1 should have been re-resolved after eviction"

    # cam2 and cam3 should still be cached
    assert resolve_counts.get("cam2", 0) == 1
    assert resolve_counts.get("cam3", 0) == 1


# ---------------------------------------------------------------------------
# 6. target_url_hash invalidation: different target_url on same id → re-resolve
# ---------------------------------------------------------------------------


def test_target_url_hash_invalidation() -> None:
    clock = _Clock()
    call_count = 0

    def counting_resolve(_entry_id: str, target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        return _resolved(url=target_url)

    cache = ResolveCache(counting_resolve, clock=clock)

    r1 = cache.get("cam1", "http://t1")
    assert call_count == 1
    assert r1 is not None and r1.url == "http://t1"

    # Different target_url → stale entry, must re-resolve
    r2 = cache.get("cam1", "http://t2")
    assert call_count == 2
    assert r2 is not None and r2.url == "http://t2"


# ---------------------------------------------------------------------------
# Extra: DEFAULT_TTL used when ttl_seconds is None
# ---------------------------------------------------------------------------


def test_default_ttl_when_none() -> None:
    clock = _Clock()
    call_count = 0

    def resolve_no_ttl(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal call_count
        call_count += 1
        return Resolved(url="http://example.com", stream_type="mp4", ttl_seconds=None)

    cache = ResolveCache(resolve_no_ttl, clock=clock)
    cache.get("cam1", "http://t")
    assert call_count == 1

    # Just before default TTL expires — should still be cached
    clock.t = DEFAULT_TTL * TTL_FACTOR - 1.0
    cache.get("cam1", "http://t")
    assert call_count == 1

    # Past default TTL — should re-resolve
    clock.t = DEFAULT_TTL * TTL_FACTOR + 1.0
    cache.get("cam1", "http://t")
    assert call_count == 2


def test_non_positive_ttl_not_cached() -> None:
    """An already-expired token (ttl_seconds <= 0) must NOT be cached for DEFAULT_TTL."""
    clock = _Clock()
    calls = 0

    def resolve(_entry_id: str, _target_url: str) -> Resolved:
        nonlocal calls
        calls += 1
        return _resolved(ttl=0)  # token already expired

    cache = ResolveCache(resolve, clock=clock)
    cache.get("cam", "http://t")
    cache.get("cam", "http://t")  # ttl<=0 → not cached → resolves again
    assert calls == 2
