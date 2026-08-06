from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from .categories import category_from_title, map_category
from .dedup import dedupe
from .fetch import OK, thread_map
from .models import Candidate, CatalogueEntry, stable_id
from .sources.base import Source

log = logging.getLogger("webcam-aggregator.catalogue")

DROP_THRESHOLD = 0.5
AGREE_TO_ACCEPT = 2
_NO_EXCLUDE: frozenset[str] = (
    frozenset()
)  # default arg (basedpyright: no call in default)


@dataclass
class Hist:
    last_count: int | None = None
    shrink_streak: int = 0
    last_kept: list[Candidate] = field(default_factory=list)
    # This-cycle RAW outcome, for /health monitoring — distinct from the guard's
    # last-*good* state above. These record what actually happened on the most
    # recent rebuild (crash / 0 kept) even while the empty-guard masks it by
    # reusing last_kept, so a failure surfaces to monitoring the moment it occurs.
    last_discovered: int = 0
    last_raw_kept: int = 0
    last_crashed: bool = False
    # This source's fetch outcomes for the cycle, {host: {outcome: count}} — what makes
    # "0 discovered" self-explaining instead of silent. Exposed on /health.
    last_fetches: dict[str, dict[str, int]] = field(default_factory=dict)


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


def _log_source_outcome(
    name: str, kept: int, discovered: int, fetches: dict[str, dict[str, int]]
) -> None:
    """One line per source, carrying the fetch outcome so an empty source says WHY.

    A totally-blocked source makes very FEW requests (skyline: one category index plus
    ten category pages, then it has no cam URLs left to try), so any global "top N
    failing hosts" view ranks it below healthy sources' routine dead-cam probes — the
    worse the outage, the better it hides. Reporting against the source is what makes
    it visible."""
    total = sum(sum(o.values()) for o in fetches.values())
    ok_n = sum(o.get(OK, 0) for o in fetches.values())
    failures = {
        f"{outcome} {host}": n
        for host, outcomes in fetches.items()
        for outcome, n in outcomes.items()
        if outcome != OK
    }
    if discovered == 0 and total:
        if ok_n == 0:
            detail = ", ".join(
                f"{n}x {k}" for k, n in sorted(failures.items(), key=lambda kv: -kv[1])
            )
            log.warning(
                "%s: 0 kept / 0 discovered — %d fetched, 0 ok (%s)", name, total, detail
            )
            return
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
        return
    if failures:
        detail = ", ".join(
            f"{n}x {k}" for k, n in sorted(failures.items(), key=lambda kv: -kv[1])[:3]
        )
        log.info(
            "%s: %d kept / %d discovered (%d fetched, %d failed: %s)",
            name,
            kept,
            discovered,
            total,
            total - ok_n,
            detail,
        )
        return
    log.info("%s: %d kept / %d discovered", name, kept, discovered)


def build_catalogue(
    sources: list[Source],
    *,
    is_alive: Callable[[Candidate], bool],
    youtube_live: Callable[[Iterable[str]], Mapping[str, str]],
    history: dict[str, Hist],
    exclude_categories: frozenset[str] = _NO_EXCLUDE,
    max_parallel_sources: int = 4,
    fetch_stats: Callable[[str], dict[str, dict[str, int]]] | None = None,
) -> list[CatalogueEntry]:
    # Sources discover + liveness-check CONCURRENTLY (each hits a different site), so the
    # build's wall-clock is the slowest source, not the sum. Each source's work is
    # self-contained and returns its kept candidates; the per-source empty guard and the
    # cross-source dedup run serially in the main thread afterwards, so there are no
    # shared-state races. The nested thread_map (inner liveness pool) is safe: the pools
    # are separate objects and no shared semaphore spans the nesting.
    yt_lock = threading.Lock()

    def filter_source(src: Source) -> tuple[str, list[Candidate], int, bool]:
        # (name, kept, discovered, crashed). Never raises — a source that blows up reports
        # crashed=True instead of sinking the whole build (and every other source with it).
        try:
            cands = list(src.discover())
        except Exception:
            log.exception("source %s discover() failed", src.name)
            return src.name, [], 0, True
        try:
            yt_ids = [
                c.predisc_key[3:]
                for c in cands
                if c.predisc_key and c.predisc_key.startswith("yt:")
            ]
            # youtube_live hits the Data API through a shared client — serialise it.
            with yt_lock:
                live: Mapping[str, str] = youtube_live(yt_ids) if yt_ids else {}

            def alive(c: Candidate, _live: Mapping[str, str] = live) -> bool:
                if c.predisc_key and c.predisc_key.startswith("yt:"):
                    return c.predisc_key[3:] in _live
                return is_alive(c)

            kept = [
                _apply_yt_category(c, live)
                for c, ok in zip(cands, thread_map(alive, cands))
                if ok
            ]
            return src.name, kept, len(cands), False
        except Exception:
            log.exception("source %s liveness filter failed", src.name)
            return src.name, [], len(cands), True

    # Cap concurrent sources so total build concurrency stays ~max_parallel_sources ×
    # SCRAPE_WORKERS regardless of source count (extra sources batch through the pool).
    results = thread_map(filter_source, list(sources), workers=max_parallel_sources)

    # Per-source empty guard + cross-source dedup, serial (results keep source order, so
    # dedup priority is unchanged from the old sequential build).
    kept_all: list[Candidate] = []
    for name, kept, discovered, crashed in results:
        h = history.setdefault(name, Hist())
        h.last_fetches = fetch_stats(name) if fetch_stats else {}
        _log_source_outcome(name, len(kept), discovered, h.last_fetches)
        # Record the RAW result before the guard can mask it — /health alerts on this
        # (crashed, or 0 kept) even when the guard keeps serving the last good set.
        h.last_discovered = discovered
        h.last_raw_kept = len(kept)
        h.last_crashed = crashed
        if crashed and h.last_kept:
            # A crash is not a genuine "0 cams" result — reuse the last good set and leave
            # history untouched, so two consecutive crashes can't get accepted as an empty
            # set (which would wipe last_kept and disable the guard).
            log.warning(
                "%s discover crashed; reusing previous %d", name, len(h.last_kept)
            )
            kept_all.extend(h.last_kept)
            continue
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
                    len(h.last_kept),
                )
                kept_all.extend(h.last_kept)  # guard: reuse this source's last good set
                continue
        h.shrink_streak = 0
        h.last_count = len(kept)
        h.last_kept = kept
        kept_all.extend(kept)

    entries = [_to_entry(c) for c in dedupe(kept_all)]
    if exclude_categories:
        # exclude_categories is casefolded (config._csv_set) for case-insensitive match
        entries = [
            e for e in entries if e.category.casefold() not in exclude_categories
        ]
    return entries
