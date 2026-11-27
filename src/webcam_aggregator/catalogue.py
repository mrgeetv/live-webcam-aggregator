"""Catalogue: refresh each source on its own interval, drop dead cams, merge."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from webcam_aggregator.categories import category_from_title, map_category
from webcam_aggregator.dedup import dedupe
from webcam_aggregator.fetch import OK, thread_map
from webcam_aggregator.models import (
    NO_EXTRACTOR,
    YT_OFFLINE,
    Candidate,
    CatalogueEntry,
    stable_id,
)
from webcam_aggregator.sources.base import Source, decode_entities

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from webcam_aggregator.store import Store, Tx

log = logging.getLogger("webcam-aggregator.catalogue")

DROP_THRESHOLD = 0.5
AGREE_TO_ACCEPT = 2
# A source whose last refresh finished this many intervals ago is stale: /health
# calls it overdue, and a restart drops its stored kept set rather than serve it.
STALE_INTERVALS = 2
_NO_EXCLUDE: frozenset[str] = (
    frozenset()
)  # default arg (basedpyright: no call in default)


@dataclass
class Hist:
    """Per-source state carried between refreshes.

    It holds the source's kept set, the empty-guard's state and the raw outcome of the
    last refresh: what the store saves after each refresh.
    """

    last_count: int | None = None
    shrink_streak: int = 0
    kept_set: list[Candidate] = field(default_factory=list)
    # The last refresh's RAW outcome, for /health monitoring — distinct from the
    # guard's state above. These record what actually happened on the most
    # recent refresh (crash / 0 kept) even while the empty-guard masks it by
    # reusing kept_set, so a failure surfaces to monitoring the moment it occurs.
    last_discovered: int = 0
    last_raw_kept: int = 0
    last_crashed: bool = False
    # Coarse health, for transition logging and /health: "ok" | "degraded" | "dead".
    # No per-host/per-reason detail here: that goes to the per-refresh log lines,
    # /health carries only these counts.
    status: str = "ok"
    # When the last refresh finished (unix seconds) and how long it took; None until
    # the source's first refresh.
    last_refresh_at: float | None = None
    last_refresh_seconds: float | None = None


# Titles a source passed straight through can still be HTML-escaped (Data API
# snippets, JSON feeds), and a player shows `&amp;` literally. Decoding every title
# here, as candidates leave their source, covers every source, and happens before
# dedup, whose "longest title wins" would otherwise prefer an escaped copy of a cam
# over a clean one. decode_entities is safe on text a source already decoded.
def _with_decoded_title(c: Candidate) -> Candidate:
    return replace(c, title=decode_entities(c.title))


def _to_entry(c: Candidate) -> CatalogueEntry:
    # A source that gave no category lands in "Other"; try to recover one from the title
    # before giving up. Only for "Other" — never override a real or "Unmapped" category.
    category = map_category(c.category)
    if category == "Other":
        category = category_from_title(c.title) or "Other"
    return CatalogueEntry(
        id=stable_id(c),
        title=c.title or "(untitled)",
        category=category,
        source=c.source,
        source_page_url=c.source_page_url,
        target_url=c.target_url,
    )


def _apply_yt_category(c: Candidate, live: Mapping[str, str]) -> Candidate:
    # YouTube-source cams carry no category until here; fill it from the live lookup.
    # Scoped to youtube-api so a worldcams/cxtvlive yt-embed keeps its scraped
    # category; cross-source dedup priority resolves the rest.
    if c.source == "youtube-api" and c.predisc_key:
        cat = live.get(c.predisc_key[3:])
        if cat:
            return replace(c, category=cat)
    return c


def top_counts(counts: dict[str, int], n: int) -> str:
    """Format the n biggest counts as "3x a, 2x b", largest first.

    Shared with app's cross-source liveness/pacing lines so every aggregate reads the
    same.
    """
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(f"{v}x {k}" for k, v in ranked)


def failure_counts(fetches: dict[str, dict[str, int]]) -> dict[str, int]:
    """Flatten {host: {outcome: n}} to {"<outcome> <host>": n}, OK excluded.

    This is the shape every fetch-failure log line ranks and prints.
    """
    return {
        f"{outcome} {host}": n
        for host, outcomes in fetches.items()
        for outcome, n in outcomes.items()
        if outcome != OK
    }


def _log_drop_detail(
    name: str, no_ext_hosts: dict[str, int], details: dict[str, int]
) -> None:
    """Log the two follow-up lines that turn a drop count into something actionable.

    They name which hosts have no extractor (i.e. what to write next), and what the
    extractors that DID run actually said (yt-dlp broken vs cam simply off air).
    """
    if no_ext_hosts:
        log.info(
            "%s: no extractor for %d candidate(s) — top hosts: %s",
            name,
            sum(no_ext_hosts.values()),
            top_counts(no_ext_hosts, 5),
        )
    if details:
        log.info("%s: top resolve failures — %s", name, top_counts(details, 3))


def _log_source_outcome(
    name: str,
    kept: int,
    discovered: int,
    fetches: dict[str, dict[str, int]],
    reasons: dict[str, int],
) -> bool:
    """Log one line per source, carrying the fetch outcome so an empty source says WHY.

    Returns True if it warned, so the caller can avoid stacking a second warning.

    A totally-blocked source makes very FEW requests (skyline: one category index plus
    ten category pages, then it has no cam URLs left to try), so any global "top N
    failing hosts" view ranks it below healthy sources' routine dead-cam probes — the
    worse the outage, the better it hides. Reporting against the source is what makes
    it visible.
    """
    total = sum(sum(o.values()) for o in fetches.values())
    ok_n = sum(o.get(OK, 0) for o in fetches.values())
    failures = failure_counts(fetches)
    if discovered == 0 and total:
        if ok_n == 0:
            log.warning(
                "%s: 0 kept / 0 discovered — %d fetched, 0 ok (%s)",
                name,
                total,
                top_counts(failures, 5),
            )
            return True
        # Fetches SUCCEEDED and we still extracted nothing. A failure counter cannot
        # see this: a Cloudflare/consent interstitial is an HTTP 200, so "0 failed" is
        # true and utterly misleading. It is also exactly what a site redesign looks
        # like, so this one line covers both.
        log.warning(
            "%s: 0 kept / 0 discovered — %d fetched ok but extracted 0 URLs "
            "(site layout changed, or a soft block returning 200?)",
            name,
            ok_n,
        )
        return True
    parts: list[str] = []
    if failures:
        parts.append(
            f"{total} fetched, {total - ok_n} failed: {top_counts(failures, 3)}"
        )
    dropped = sum(reasons.values())
    if dropped:
        parts.append(f"dropped {dropped} ({top_counts(reasons, 4)})")
    if parts:
        log.info(
            "%s: %d kept / %d discovered — %s",
            name,
            kept,
            discovered,
            "; ".join(parts),
        )
        return False
    log.info("%s: %d kept / %d discovered", name, kept, discovered)
    return False


def _status_for(kept: int, *, crashed: bool, h: Hist) -> str:
    """Return the coarse health of a source from its RAW result.

    The result is dead (nothing, or it blew up), degraded (a collapse the guard is
    masking), or ok. Drives the one-line transition log, so an incident reads as a
    timeline rather than the same line repeated every 6h.
    """
    if crashed or kept == 0:
        return "dead"
    if (
        h.last_count is not None
        and h.last_count > 0
        and kept < h.last_count * (1 - DROP_THRESHOLD)
    ):
        return "degraded"
    return "ok"


@dataclass
class _SourceResult:
    """One source's raw refresh outcome, before the empty-guard gets a say."""

    name: str
    kept: list[Candidate]
    discovered: int
    crashed: bool
    drop_reasons: dict[str, int] = field(default_factory=dict)
    no_extractor_hosts: dict[str, int] = field(default_factory=dict)
    resolve_details: dict[str, int] = field(default_factory=dict)


# A verdict outlives the refresh that made it so that another source carrying the same
# URL can reuse it, but not by much: tokens expire and cams go off air.
VERDICT_TTL_SECONDS = 30 * 60


class VerdictTable:
    """One liveness verdict per target URL, shared by every source's refreshes.

    Meta-aggregators (windy, camscape) carry the same cams as the first-party sources,
    so without this the same feratel page gets probed by each source that lists it:
    extra load aimed at exactly the hosts that already rate-limit us. A verdict
    answers for `VERDICT_TTL_SECONDS`, then the next source to ask probes again. The
    check-then-probe window means two sources racing on the same URL can still probe
    twice; that rare duplicate is cheaper than holding a lock across a network fetch.
    """

    _probe: Callable[[Candidate], str | None]
    _clock: Callable[[], float]
    _verdicts: dict[str, tuple[str | None, float]]
    _lock: threading.Lock

    def __init__(
        self, probe: Callable[[Candidate], str | None], clock: Callable[[], float]
    ) -> None:
        """Wrap `probe`, the liveness check that returns a cam's drop reason."""
        self._probe = probe
        self._clock = clock
        self._verdicts = {}
        self._lock = threading.Lock()

    def drop_reason(self, c: Candidate, live: Mapping[str, str]) -> str | None:
        """Return why `c` is dropped, or None to keep it (`live`: the live yt ids)."""
        if c.predisc_key and c.predisc_key.startswith("yt:"):
            return None if c.predisc_key[3:] in live else YT_OFFLINE
        # Key on target_url, NOT predisc_key: the probe fetches target_url and its
        # verdict is a property of that exact URL. predisc_key is a lossy MERGE
        # identity (it strips auth tokens, and feratel:<id> collapses every URL shape
        # for a cam), so sharing a verdict across it would apply one URL's result to a
        # materially different one (a tokened vs bare copy of one m3u8, or a feratel
        # page that routes to a different extractor). Identical target_urls across
        # sources are the same fetch, so this still collapses the genuinely duplicated
        # probes.
        with self._lock:
            hit = self._verdicts.get(c.target_url)
            if hit is not None and hit[1] > self._clock():
                return hit[0]
        reason = self._probe(c)
        with self._lock:
            self._verdicts[c.target_url] = (reason, self._clock() + VERDICT_TTL_SECONDS)
        return reason

    def prune(self) -> None:
        """Forget expired verdicts, so the table holds ~one TTL's worth of URLs."""
        now = self._clock()
        with self._lock:
            self._verdicts = {u: v for u, v in self._verdicts.items() if v[1] > now}


def _tally(
    name: str,
    cands: list[Candidate],
    reasons: list[str | None],
    live: Mapping[str, str],
) -> _SourceResult:
    """Sort a source's candidates into kept ones and counted drops."""
    kept: list[Candidate] = []
    drops: dict[str, int] = {}
    no_ext_hosts: dict[str, int] = {}
    details: dict[str, int] = {}
    for c, reason in zip(cands, reasons, strict=True):
        if reason is None:
            kept.append(_apply_yt_category(c, live))
            continue
        # A reason may carry ":<detail>": bucket on the label, keep the detail for
        # the top-N line (that is what separates "yt-dlp is broken" from "this cam
        # is off air").
        label, _, detail = reason.partition(":")
        drops[label] = drops.get(label, 0) + 1
        if label == NO_EXTRACTOR:
            host = urlsplit(c.target_url).hostname or "?"
            no_ext_hosts[host] = no_ext_hosts.get(host, 0) + 1
        elif detail:
            details[detail] = details.get(detail, 0) + 1
    return _SourceResult(
        name,
        kept,
        len(cands),
        crashed=False,
        drop_reasons=drops,
        no_extractor_hosts=no_ext_hosts,
        resolve_details=details,
    )


def _record_refresh(
    r: _SourceResult, h: Hist, fetches: dict[str, dict[str, int]]
) -> list[Candidate]:
    """Log a source's refresh, update its history, and return the candidates to serve.

    That is the new set, or the kept set while the empty-guard holds.
    """
    name, kept, discovered, crashed = r.name, r.kept, r.discovered, r.crashed
    warned = _log_source_outcome(name, len(kept), discovered, fetches, r.drop_reasons)
    _log_drop_detail(name, r.no_extractor_hosts, r.resolve_details)
    # Record the RAW result before the guard can mask it — /health alerts on this
    # (crashed, or 0 kept) even when the guard keeps serving the kept set.
    h.last_discovered = discovered
    h.last_raw_kept = len(kept)
    h.last_crashed = crashed
    # Status is computed from the RAW result, before the guard branches below can
    # return early, so a masked failure still registers.
    status = _status_for(len(kept), crashed=crashed, h=h)
    if status != h.status:
        log.warning(
            "%s: %s -> %s (%d kept, was %s)",
            name,
            h.status,
            status,
            len(kept),
            h.last_count if h.last_count is not None else "n/a",
        )
    elif status == "dead" and not warned:
        # Still down, and nothing above said so. The empty-guard stops warning once
        # it accepts the zero (after AGREE_TO_ACCEPT refreshes), which would make a
        # long outage go quiet exactly when it matters. A source at zero warns every
        # single refresh it stays there.
        log.warning("%s: still dead (%d kept / %d discovered)", name, 0, discovered)
    h.status = status
    if crashed and h.kept_set:
        # A crash is not a genuine "0 cams" result — reuse the kept set and leave
        # history untouched, so two consecutive crashes can't get accepted as an empty
        # set (which would wipe kept_set and disable the guard).
        log.warning("%s discover crashed; reusing previous %d", name, len(h.kept_set))
        return h.kept_set
    collapsed = (
        h.last_count is not None
        and h.last_count > 0
        and len(kept) < h.last_count * (1 - DROP_THRESHOLD)
    )
    if collapsed:
        h.shrink_streak += 1
        if h.shrink_streak < AGREE_TO_ACCEPT:
            log.warning(
                "%s collapsed to %d (< %.0f%% of last %d); keeping previous %d",
                name,
                len(kept),
                DROP_THRESHOLD * 100,
                h.last_count,
                len(h.kept_set),
            )
            return h.kept_set  # guard: reuse this source's kept set
    h.shrink_streak = 0
    h.last_count = len(kept)
    h.kept_set = kept
    return kept


def _refresh(
    src: Source,
    verdicts: VerdictTable,
    youtube_live: Callable[[Iterable[str]], Mapping[str, str]],
    yt_lock: threading.Lock,
) -> _SourceResult:
    """Discover one source's cams and liveness-check them.

    Never raises: a source that blows up reports crashed=True instead, so it can't
    take the scheduler, or any other source, down with it.
    """
    try:
        cands = [_with_decoded_title(c) for c in src.discover()]
    except Exception:
        log.exception("source %s discover() failed", src.name)
        return _SourceResult(src.name, [], 0, crashed=True)
    try:
        yt_ids = [
            c.predisc_key[3:]
            for c in cands
            if c.predisc_key and c.predisc_key.startswith("yt:")
        ]
        # youtube_live hits the Data API through a shared client — serialise it.
        with yt_lock:
            live: Mapping[str, str] = youtube_live(yt_ids) if yt_ids else {}
        reasons = thread_map(lambda c: verdicts.drop_reason(c, live), cands)
        return _tally(src.name, cands, reasons, live)
    except Exception:
        log.exception("source %s liveness filter failed", src.name)
        return _SourceResult(src.name, [], len(cands), crashed=True)


@dataclass
class _Finished:
    result: _SourceResult
    started: float
    finished: float


# The scheduler re-checks what is due at least this often, even with nothing due
# sooner, so a wall-clock step (NTP, a suspended host) can't strand a due source.
_MAX_WAIT_SECONDS = 60.0


class Scheduler:
    """Refresh each source on its own interval and re-merge the catalogue after each.

    A refresh is one source's discover + liveness, run on a pool of
    `max_parallel_sources` workers, so total concurrency stays ~that × SCRAPE_WORKERS
    however many sources exist. A source is due once its interval has passed since its
    last refresh FINISHED, and never runs two refreshes at once, so one that overruns
    its interval doesn't start again the moment it ends. Due sources go most overdue
    first; one that has never refreshed is the most overdue of all.

    Everything after a refresh (the empty-guard, the source's log line, the merge)
    runs on the thread that calls `finish_next`, one refresh at a time, so `history`
    and the merge need no lock. The liveness pool nested inside each refresh can't
    deadlock the refresh pool: the one thing spanning both, the shared HostPacer,
    holds nothing while a thread waits (pacing is sleep-only).
    Keep it that way: nothing a fetch path acquires may be held while waiting on
    another thread.
    """

    history: dict[str, Hist]
    intervals: dict[str, float]
    _sources: list[Source]
    _youtube_live: Callable[[Iterable[str]], Mapping[str, str]]
    _on_merge: Callable[[list[CatalogueEntry]], None]
    _exclude: frozenset[str]
    _max: int
    _fetch_stats: Callable[[str], dict[str, dict[str, int]]] | None
    _clock: Callable[[], float]
    _store: Store
    _verdicts: VerdictTable
    _yt_lock: threading.Lock
    _running: set[str]
    _done: queue.Queue[_Finished]
    _pool: ThreadPoolExecutor

    def __init__(
        self,
        sources: list[Source],
        intervals: Mapping[str, float],
        *,
        drop_reason_for: Callable[[Candidate], str | None],
        youtube_live: Callable[[Iterable[str]], Mapping[str, str]],
        on_merge: Callable[[list[CatalogueEntry]], None],
        store: Store,
        exclude_categories: frozenset[str] = _NO_EXCLUDE,
        max_parallel_sources: int = 4,
        fetch_stats: Callable[[str], dict[str, dict[str, int]]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Schedule `sources`, each every `intervals[name]` hours.

        `on_merge` receives the catalogue after every refresh, and at once if `store`
        holds any source's kept set: what a restart serves before any refresh. `clock`
        is wall-clock unix seconds: it stamps `Hist.last_refresh_at`, which /health
        reports as UTC.
        """
        self._sources = list(sources)
        self.intervals = {s.name: float(intervals[s.name]) for s in self._sources}
        self.history = {s.name: Hist() for s in self._sources}
        self._youtube_live = youtube_live
        self._on_merge = on_merge
        self._exclude = exclude_categories
        self._max = max_parallel_sources
        self._fetch_stats = fetch_stats
        self._clock = clock
        self._store = store
        self._verdicts = VerdictTable(drop_reason_for, clock)
        self._yt_lock = threading.Lock()
        self._running = set()
        self._done = queue.Queue()
        self._pool = ThreadPoolExecutor(
            max_workers=max_parallel_sources, thread_name_prefix="refresh"
        )
        if self._load():
            self._merge()

    def _load(self) -> bool:
        """Restore each source's last finished refresh from the store.

        A restored source serves its kept set with no re-check and is due an interval
        after that refresh, as if there had been no restart. One stale enough for
        /health to call overdue is dropped whole, guard state included, and starts as
        new. Rows for a source the code doesn't know are left alone. Return whether
        anything was restored.
        """
        # One query, so a source's row and its cams come from one snapshot: a read
        # that fails returns nothing at all, never a source without its kept set.
        rows = self._store.read(
            "SELECT s.name, s.last_refresh_at, s.last_refresh_seconds, s.discovered, "
            "s.raw_kept, s.crashed, s.status, s.guard_last_count, "
            "s.guard_shrink_streak, k.title, k.angle_key, k.category, "
            "k.source_page_url, k.target_url, k.predisc_key, k.name_rank "
            "FROM source s LEFT JOIN kept_cam k ON k.source = s.name "
            "ORDER BY s.name, k.pos"
        )
        if not rows:
            return False
        now = self._clock()
        restored: dict[str, Hist] = {}
        stale: set[str] = set()
        for name, at, secs, disc, raw, crashed, status, count, streak, *cam in rows:
            interval = self.intervals.get(name)
            if interval is None or name in stale:
                continue
            if now - at > STALE_INTERVALS * interval * 3600:
                stale.add(name)
                continue
            h = restored.get(name)
            if h is None:
                h = restored[name] = Hist(
                    last_count=count,
                    shrink_streak=streak,
                    last_discovered=disc,
                    last_raw_kept=raw,
                    last_crashed=bool(crashed),
                    status=status,
                    last_refresh_at=float(at),
                    last_refresh_seconds=secs,
                )
            title, angle, category, page, target, key, rank = cam
            if target is not None:  # None: a source whose kept set is empty
                h.kept_set.append(
                    Candidate(title, angle, category, name, page, target, key, rank)
                )
        self.history.update(restored)
        log.info(
            "store: loaded %s; over age, so starting as new: %s",
            ", ".join(s.name for s in self._sources if s.name in restored) or "none",
            ", ".join(s.name for s in self._sources if s.name in stale) or "none",
        )
        return bool(restored)

    def _save(self, name: str, h: Hist, f: _Finished) -> None:
        """Commit a finished refresh: the outcome, guard state and the kept set it left.

        One transaction, so a restart finds either this refresh or the one before it,
        never part of each.
        """

        def write(tx: Tx) -> None:
            tx.execute(
                "INSERT OR REPLACE INTO source (name, last_refresh_at, "
                "last_refresh_seconds, discovered, raw_kept, crashed, status, "
                "guard_last_count, guard_shrink_streak) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    int(f.finished),
                    round(f.finished - f.started, 1),
                    h.last_discovered,
                    h.last_raw_kept,
                    int(h.last_crashed),
                    h.status,
                    h.last_count,
                    h.shrink_streak,
                ),
            )
            tx.execute("DELETE FROM kept_cam WHERE source = ?", (name,))
            tx.executemany(
                "INSERT INTO kept_cam (source, pos, title, angle_key, category, "
                "source_page_url, target_url, predisc_key, name_rank) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        name,
                        pos,
                        c.title,
                        c.angle_key,
                        c.category,
                        c.source_page_url,
                        c.target_url,
                        c.predisc_key,
                        c.name_rank,
                    )
                    for pos, c in enumerate(h.kept_set)
                ),
            )

        self._store.write(f"{name}'s refresh", write)

    def _due_at(self, name: str) -> float:
        last = self.history[name].last_refresh_at
        return -math.inf if last is None else last + self.intervals[name] * 3600

    def tick(self) -> list[str]:
        """Start the due sources, most overdue first, in free slots; return them."""
        now = self._clock()
        due = sorted(  # stable: equally overdue sources start in their listed order
            (
                s
                for s in self._sources
                if s.name not in self._running and self._due_at(s.name) <= now
            ),
            key=lambda s: self._due_at(s.name),
        )
        started = due[: self._max - len(self._running)]
        for src in started:
            self._running.add(src.name)
            self._pool.submit(self._run, src)
        return [s.name for s in started]

    def _run(self, src: Source) -> None:
        t0 = self._clock()
        result = _refresh(src, self._verdicts, self._youtube_live, self._yt_lock)
        self._done.put(_Finished(result, started=t0, finished=self._clock()))

    def finish_next(self, timeout: float | None = None) -> bool:
        """Wait up to `timeout` for a refresh to finish, then guard it and re-merge.

        Return False if none finished in time.
        """
        try:
            f = self._done.get(timeout=timeout)
        except queue.Empty:
            return False
        r = f.result
        h = self.history[r.name]
        try:
            # A source's fetch stats are its own Fetcher's, and only its refresh
            # fetches through that, so draining here covers exactly this refresh.
            fetches = self._fetch_stats(r.name) if self._fetch_stats else {}
            _record_refresh(r, h, fetches)
        finally:
            # Whatever went wrong above, the refresh ran: the source must be due again
            # an interval from now, never left marked running (it would never refresh
            # again, and /health would show it "unknown" for good).
            h.last_refresh_at = f.finished
            h.last_refresh_seconds = round(f.finished - f.started, 1)
            self._running.discard(r.name)
        self._save(r.name, h, f)
        self._verdicts.prune()
        self._merge()
        return True

    def _merge(self) -> None:
        # The guard leaves a source's previous kept set in place while it holds a
        # collapse back. Sources merge in their listed order, so dedup's ties break
        # the same way on every merge.
        kept_all = [c for s in self._sources for c in self.history[s.name].kept_set]
        entries = [_to_entry(c) for c in dedupe(kept_all)]
        if self._exclude:
            # exclude_categories is casefolded (config._csv_set) for case-insensitive
            # match
            entries = [e for e in entries if e.category.casefold() not in self._exclude]
        self._on_merge(entries)
        log.info("catalogue merged: %d entries", len(entries))

    def run_once(self) -> None:
        """Start whatever is due, then handle one finished refresh or wait for one."""
        self.tick()
        wait = _MAX_WAIT_SECONDS
        if len(self._running) < self._max:
            # A slot is free, so tick() started everything due: sleep until the next
            # idle source falls due, unless a refresh finishes first.
            idle = [
                self._due_at(s.name)
                for s in self._sources
                if s.name not in self._running
            ]
            if idle:
                wait = min(wait, max(0.0, min(idle) - self._clock()))
        self.finish_next(timeout=wait)

    def run_forever(self) -> None:
        """Run the schedule until the process exits."""
        while True:
            try:
                self.run_once()
            # The loop is the only thing that refreshes anything: a bug in one step
            # must be logged, never allowed to end the schedule.
            except Exception:
                log.exception("scheduler step failed; carrying on")
