from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeIs

from .extractors.base import Resolved

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
RETRY_MIN_GAP = 3.0
# A retry doesn't re-resolve an entry refreshed more recently than this, so a
# player's retry burst costs one resolve rather than one per attempt.
RETRY_MIN_AGE = 30.0


@dataclass
class _Entry:
    resolved: Resolved | None  # None = negative (a failed resolve)
    target_hash: str
    # last time a resolve ran for this entry — including a fresh one that failed
    # and left `resolved` in place
    refreshed_at: float
    expires_at: float
    # last time this resolve was handed out as a redirect (see record_redirect)
    redirected_at: float | None = None


class ResolveCache:
    _resolve: Callable[[str, str], Resolved]
    _clock: Callable[[], float]
    _cap: int
    _data: "OrderedDict[str, _Entry]"
    _locks: dict[str, threading.Lock]
    _guard: threading.Lock

    def __init__(
        self,
        resolve: Callable[[str, str], Resolved],
        *,
        clock: Callable[[], float],
        cap: int = 500,
    ) -> None:
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
        """Record that `entry_id`'s resolve is being handed out as a redirect, and
        return whether this is a player retrying a URL that failed where the server
        can't see: back RETRY_MIN_GAP to RETRY_WINDOW after the last redirect, for
        an entry not refreshed within RETRY_MIN_AGE."""
        now = self._clock()
        with self._guard:
            e = self._data.get(entry_id)
            if e is None:
                return False
            prev = e.redirected_at
            gap = None if prev is None else now - prev
            if gap is None or gap >= RETRY_MIN_GAP:
                e.redirected_at = now
            return (
                gap is not None
                and RETRY_MIN_GAP <= gap <= RETRY_WINDOW
                and now - e.refreshed_at >= RETRY_MIN_AGE
            )

    def get(
        self, entry_id: str, target_url: str, *, fresh: bool = False
    ) -> Resolved | None:
        """The cached resolve for `entry_id`, resolving on a miss. A caller arriving
        while a resolve for the id is in flight waits for its result. `fresh` forces
        a new resolve unless one finished after this call began; if it fails, the
        cached resolve is kept rather than replaced by a negative entry."""
        start = self._clock()
        thash = str(hash(target_url))

        def usable(e: _Entry | None, now: float) -> TypeIs[_Entry]:
            return (
                e is not None
                and e.expires_at > now
                and e.target_hash == thash
                and (not fresh or e.refreshed_at >= start)
            )

        lock = self._lock_for(entry_id)
        if not lock.locked():
            with self._guard:
                e = self._data.get(entry_id)
                if usable(e, start):
                    self._data.move_to_end(entry_id)
                    return e.resolved
        with lock:  # single-flight per id
            with self._guard:
                e = self._data.get(entry_id)
                if usable(e, self._clock()):
                    return e.resolved
            try:
                resolved = self._resolve(entry_id, target_url)
                raw = resolved.ttl_seconds
                if raw is None:
                    ttl = DEFAULT_TTL * TTL_FACTOR
                elif raw > 0:
                    ttl = raw * TTL_FACTOR
                else:
                    ttl = 0.0  # already-expired token → don't serve stale, re-resolve
            except Exception:
                resolved, ttl = None, NEGATIVE_TTL
            done = self._clock()
            with self._guard:
                if (
                    resolved is None
                    and fresh
                    and e is not None
                    and e.resolved is not None
                    and e.expires_at > done
                    and e.target_hash == thash
                ):
                    e.refreshed_at = done
                    return e.resolved
                entry = _Entry(resolved, thash, done, done + ttl)
                if e is not None:
                    # retry detection spans re-resolves
                    entry.redirected_at = e.redirected_at
                self._data[entry_id] = entry
                self._data.move_to_end(entry_id)
                while len(self._data) > self._cap:
                    old_id, _ = self._data.popitem(last=False)
                    self._locks.pop(old_id, None)  # bound _locks alongside _data
            return entry.resolved
