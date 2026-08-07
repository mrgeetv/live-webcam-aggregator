from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from .categories import category_from_title, map_category
from .dedup import dedupe
from .fetch import OK, thread_map
from .models import (
    NO_EXTRACTOR,
    YT_OFFLINE,
    Candidate,
    CatalogueEntry,
    stable_id,
)
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
    # Coarse health, for transition logging and /health: "ok" | "degraded" | "dead".
    # No per-host/per-reason detail here: that goes to the per-cycle log lines,
    # /health carries only these counts.
    status: str = "ok"


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
    """ "3x a, 2x b" for the n biggest counts, largest first. Shared with app's
    cross-source liveness/pacing lines so every aggregate reads the same."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(f"{v}x {k}" for k, v in ranked)


def failure_counts(fetches: dict[str, dict[str, int]]) -> dict[str, int]:
    """Flatten {host: {outcome: n}} to {"<outcome> <host>": n}, OK excluded —
    the shape every fetch-failure log line ranks and prints."""
    return {
        f"{outcome} {host}": n
        for host, outcomes in fetches.items()
        for outcome, n in outcomes.items()
        if outcome != OK
    }


def _log_drop_detail(
    name: str, no_ext_hosts: dict[str, int], details: dict[str, int]
) -> None:
    """The two follow-up lines that turn a drop count into something actionable: which
    hosts have no extractor (i.e. what to write next), and what the extractors that DID
    run actually said (yt-dlp broken vs cam simply off air)."""
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
    """One line per source, carrying the fetch outcome so an empty source says WHY.
    Returns True if it warned, so the caller can avoid stacking a second warning.

    A totally-blocked source makes very FEW requests (skyline: one category index plus
    ten category pages, then it has no cam URLs left to try), so any global "top N
    failing hosts" view ranks it below healthy sources' routine dead-cam probes — the
    worse the outage, the better it hides. Reporting against the source is what makes
    it visible."""
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


def _status_for(kept: int, crashed: bool, h: Hist) -> str:
    """Coarse health from the RAW result: dead (nothing, or it blew up), degraded (a
    collapse the guard is masking), or ok. Drives the one-line transition log, so an
    incident reads as a timeline rather than the same line repeated every 6h."""
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
    """One source's raw cycle outcome, before the empty-guard gets a say."""

    name: str
    kept: list[Candidate]
    discovered: int
    crashed: bool
    drop_reasons: dict[str, int] = field(default_factory=dict)
    no_extractor_hosts: dict[str, int] = field(default_factory=dict)
    resolve_details: dict[str, int] = field(default_factory=dict)


def build_catalogue(
    sources: list[Source],
    *,
    drop_reason_for: Callable[[Candidate], str | None],
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
    # are separate objects, and the one thing that DOES span the nesting — the shared
    # HostPacer — holds nothing while a thread waits (pacing is sleep-only), so it can
    # slow a pool down but can never deadlock it. Keep it that way: nothing a fetch
    # path acquires may be held while waiting on another thread.
    yt_lock = threading.Lock()

    def filter_source(src: Source) -> _SourceResult:
        # Never raises — a source that blows up reports crashed=True instead of
        # sinking the whole build (and every other source with it).
        try:
            cands = list(src.discover())
        except Exception:
            log.exception("source %s discover() failed", src.name)
            return _SourceResult(src.name, [], 0, True)
        try:
            yt_ids = [
                c.predisc_key[3:]
                for c in cands
                if c.predisc_key and c.predisc_key.startswith("yt:")
            ]
            # youtube_live hits the Data API through a shared client — serialise it.
            with yt_lock:
                live: Mapping[str, str] = youtube_live(yt_ids) if yt_ids else {}

            def drop_reason(
                c: Candidate, _live: Mapping[str, str] = live
            ) -> str | None:
                if c.predisc_key and c.predisc_key.startswith("yt:"):
                    return None if c.predisc_key[3:] in _live else YT_OFFLINE
                return drop_reason_for(c)

            kept: list[Candidate] = []
            reasons: dict[str, int] = {}
            no_ext_hosts: dict[str, int] = {}
            details: dict[str, int] = {}
            for c, reason in zip(cands, thread_map(drop_reason, cands)):
                if reason is None:
                    kept.append(_apply_yt_category(c, live))
                    continue
                # A reason may carry ":<detail>" — bucket on the label, keep the detail
                # for the top-N line (that is what separates "yt-dlp is broken" from
                # "this cam is off air").
                label, _, detail = reason.partition(":")
                reasons[label] = reasons.get(label, 0) + 1
                if label == NO_EXTRACTOR:
                    host = urlsplit(c.target_url).hostname or "?"
                    no_ext_hosts[host] = no_ext_hosts.get(host, 0) + 1
                elif detail:
                    details[detail] = details.get(detail, 0) + 1
            return _SourceResult(
                src.name, kept, len(cands), False, reasons, no_ext_hosts, details
            )
        except Exception:
            log.exception("source %s liveness filter failed", src.name)
            return _SourceResult(src.name, [], len(cands), True)

    # Cap concurrent sources so total build concurrency stays ~max_parallel_sources ×
    # SCRAPE_WORKERS regardless of source count (extra sources batch through the pool).
    results = thread_map(filter_source, list(sources), workers=max_parallel_sources)

    # Per-source empty guard + cross-source dedup, serial (results keep source order, so
    # dedup priority is unchanged from the old sequential build).
    kept_all: list[Candidate] = []
    for r in results:
        name, kept, discovered, crashed = r.name, r.kept, r.discovered, r.crashed
        h = history.setdefault(name, Hist())
        # Drained per cycle for the log line only — the WHY lives in the logs, not
        # on Hist (see the field comment there).
        fetches = fetch_stats(name) if fetch_stats else {}
        warned = _log_source_outcome(
            name, len(kept), discovered, fetches, r.drop_reasons
        )
        _log_drop_detail(name, r.no_extractor_hosts, r.resolve_details)
        # Record the RAW result before the guard can mask it — /health alerts on this
        # (crashed, or 0 kept) even when the guard keeps serving the last good set.
        h.last_discovered = discovered
        h.last_raw_kept = len(kept)
        h.last_crashed = crashed
        # Status is computed from the RAW result, before the guard branches below can
        # `continue` past it, so a masked failure still registers.
        status = _status_for(len(kept), crashed, h)
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
            # it accepts the zero (after AGREE_TO_ACCEPT cycles), which would make a
            # long outage go quiet exactly when it matters. A source at zero warns every
            # single cycle it stays there.
            log.warning("%s: still dead (%d kept / %d discovered)", name, 0, discovered)
        h.status = status
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
