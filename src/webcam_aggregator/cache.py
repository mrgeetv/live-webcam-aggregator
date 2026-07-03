from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .extractors.base import Resolved

TTL_FACTOR = 0.8
NEGATIVE_TTL = 60.0
# Fallback when an extractor can't report an expiry (DirectHls/MetaTag/ipcamlive).
# Kept short: these are often session/token streams whose URLs go stale in minutes,
# so a stale cached resolve would break playback until it expired.
DEFAULT_TTL = 600.0


@dataclass
class _Entry:
    resolved: Resolved | None  # None = negative (a failed resolve)
    target_hash: str
    expires_at: float


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

    def get(self, entry_id: str, target_url: str) -> Resolved | None:
        now = self._clock()
        thash = str(hash(target_url))
        with self._guard:
            e = self._data.get(entry_id)
            if e and e.expires_at > now and e.target_hash == thash:
                self._data.move_to_end(entry_id)
                return e.resolved
        with self._lock_for(entry_id):  # single-flight per id
            now = self._clock()
            with self._guard:
                e = self._data.get(entry_id)
                if e and e.expires_at > now and e.target_hash == thash:
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
                entry = _Entry(resolved, thash, now + ttl)
            except Exception:
                entry = _Entry(None, thash, now + NEGATIVE_TTL)
            with self._guard:
                self._data[entry_id] = entry
                self._data.move_to_end(entry_id)
                while len(self._data) > self._cap:
                    old_id, _ = self._data.popitem(last=False)
                    self._locks.pop(old_id, None)  # bound _locks alongside _data
            return entry.resolved
