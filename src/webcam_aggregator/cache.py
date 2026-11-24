"""Resolve cache: per-entry locking, TTL, LRU eviction and negative caching."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeIs

from webcam_aggregator.logging_redaction import describe_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from webcam_aggregator.extractors.base import Resolved

TTL_FACTOR = 0.8
NEGATIVE_TTL = 60.0
# Fallback when an extractor can't report an expiry (DirectHls/MetaTag/ipcamlive).
# Kept short: these are often session/token streams whose URLs go stale in minutes,
# so a stale cached resolve would break playback until it expired.
DEFAULT_TTL = 600.0
# A player handed a redirect fetches the upstream itself, so when that URL fails the
# server never sees it: the only sign is the player coming back for the same stream.
# One back within this window of the last redirect is treated as retrying a failure.
RETRY_WINDOW = 120.0
# ...but not one back sooner than this: players open each stream twice on start-up,
# which is not a retry. Requests inside the gap don't restart it, so a player
# retrying faster than this still counts once the gap has passed.
RETRY_MIN_GAP = 5.0
# A retry doesn't re-resolve an entry refreshed more recently than this, so a
# player's retry burst costs one resolve rather than one per attempt. Each further
# retry-triggered resolve in one run of retries doubles it, up to RETRY_MAX_AGE:
# retries that keep coming mean fresh URLs fail too (a leashed client, yt-dlp
# blocked), and re-resolving at the first rate would hammer YouTube for nothing.
RETRY_MIN_AGE = 30.0
RETRY_MAX_AGE = 600.0


@dataclass(frozen=True)
class Lookup:
    """The answer `ResolveCache.get` gives.

    Holds the resolve to serve (None when there is none) and why the resolve attempt
    behind this answer failed (None when it didn't). Both are set when a fresh resolve
    failed and the cached one stands.
    """

    resolved: Resolved | None
    error: str | None = None


@dataclass
class _Entry:
    resolved: Resolved | None  # None = negative (a failed resolve)
    target_hash: str
    # when the last resolve for this entry began — including a fresh one that
    # failed and left `resolved` in place
    refreshed_at: float
    expires_at: float
    # last time this resolve was handed out as a redirect (see record_redirect)
    redirected_at: float | None = None
    # retry-triggered resolves in the current run of retries (see RETRY_MIN_AGE)
    retry_streak: int = 0
    # a negative entry's failure, safe to log at INFO (describe_error)
    error: str | None = None


class ResolveCache:
    """Cache of resolved stream URLs by catalogue entry id."""

    _resolve: Callable[[str, str], Resolved]
    _clock: Callable[[], float]
    _cap: int
    _data: OrderedDict[str, _Entry]
    _locks: dict[str, threading.Lock]
    _guard: threading.Lock

    def __init__(
        self,
        resolve: Callable[[str, str], Resolved],
        *,
        clock: Callable[[], float],
        cap: int = 500,
    ) -> None:
        """Set the `resolve` callback, the `clock` and the `cap` on entries kept."""
        self._resolve = resolve
        self._clock = clock
        self._cap = cap
        self._data = OrderedDict()
        self._locks = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def record_redirect(self, entry_id: str) -> bool:
        """Record a redirect for `entry_id`; return whether it is a player retrying.

        Return whether this is a player retrying a URL that failed where the server
        can't see: back RETRY_MIN_GAP to RETRY_WINDOW after the last redirect, for an
        entry not refreshed within the run's backed-off RETRY_MIN_AGE.
        """
        now = self._clock()
        with self._guard:
            e = self._data.get(entry_id)
            if e is None:
                return False
            prev = e.redirected_at
            gap = None if prev is None else now - prev
            if gap is None or gap >= RETRY_MIN_GAP:
                e.redirected_at = now
            if gap is None or gap > RETRY_WINDOW:
                e.retry_streak = 0  # a new play, not a retry: the run is over
                return False
            min_age = min(RETRY_MIN_AGE * 2**e.retry_streak, RETRY_MAX_AGE)
            if gap < RETRY_MIN_GAP or now - e.refreshed_at < min_age:
                return False
            e.retry_streak += 1
            return True

    def _resolve_with_ttl(
        self, entry_id: str, target_url: str
    ) -> tuple[Resolved | None, float, str | None]:
        """Resolve once: the result, how long to cache it, and why it failed if so."""
        try:
            resolved = self._resolve(entry_id, target_url)
        # Whatever the extractor raised becomes a negative cache entry and the
        # player's error, never an exception out of a /stream request.
        except Exception as exc:  # noqa: BLE001
            return None, NEGATIVE_TTL, describe_error(exc)
        raw = resolved.ttl_seconds
        if raw is None:
            return resolved, DEFAULT_TTL * TTL_FACTOR, None
        # An already-expired token (ttl <= 0) is not served stale: re-resolve.
        return resolved, max(raw, 0) * TTL_FACTOR, None

    def get(self, entry_id: str, target_url: str, *, fresh: bool = False) -> Lookup:
        """Return the cached resolve for `entry_id`, resolving on a miss.

        A caller arriving while a resolve for the id is in flight waits for its result.
        `fresh` forces a new resolve; if it fails, the cached resolve is kept rather
        than replaced by a negative entry. A failure's reason is kept with its negative
        entry, so every request it answers can say why, not just the one that resolved.
        """
        start = self._clock()
        thash = str(hash(target_url))

        def usable(e: _Entry | None, now: float) -> TypeIs[_Entry]:
            return (
                e is not None
                and e.expires_at > now
                and e.target_hash == thash
                and not fresh
            )

        lock = self._lock_for(entry_id)
        if not lock.locked():
            with self._guard:
                e = self._data.get(entry_id)
                if usable(e, start):
                    self._data.move_to_end(entry_id)
                    return Lookup(e.resolved, e.error)
        with lock:  # single-flight per id
            now = self._clock()
            with self._guard:
                e = self._data.get(entry_id)
                if usable(e, now):
                    return Lookup(e.resolved, e.error)
            resolved, ttl, error = self._resolve_with_ttl(entry_id, target_url)
            with self._guard:
                if (
                    resolved is None
                    and fresh
                    and e is not None
                    and e.resolved is not None
                    and e.expires_at > now
                    and e.target_hash == thash
                ):
                    e.refreshed_at = now
                    return Lookup(e.resolved, error)
                entry = _Entry(resolved, thash, now, now + ttl, error=error)
                if e is not None:
                    # retry detection spans re-resolves
                    entry.redirected_at = e.redirected_at
                    entry.retry_streak = e.retry_streak
                self._data[entry_id] = entry
                self._data.move_to_end(entry_id)
                while len(self._data) > self._cap:
                    old_id, _ = self._data.popitem(last=False)
                    self._locks.pop(old_id, None)  # bound _locks alongside _data
            return Lookup(entry.resolved, error)
