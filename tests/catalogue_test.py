from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING

import pytest

from webcam_aggregator import store
from webcam_aggregator.app import source_status_for
from webcam_aggregator.catalogue import (
    AGREE_TO_ACCEPT,
    Hist,
    Scheduler,
)
from webcam_aggregator.models import Candidate, CatalogueEntry
from webcam_aggregator.store import open_storeless

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from webcam_aggregator.sources.base import Source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    source: str = "worldcams",
    key: str | None = "hls:cam1",
    page: str = "https://example.com/page1",
    target: str = "https://example.com/stream1.m3u8",
    title: str = "Test Cam",
    category: str | None = None,
) -> Candidate:
    return Candidate(
        title=title,
        angle_key=None,
        category=category,
        source=source,
        source_page_url=page,
        target_url=target,
        predisc_key=key,
    )


class _Src:
    name: str
    interval_hours: float = 6
    _c: list[Candidate]

    def __init__(self, name: str, cands: list[Candidate]) -> None:
        self.name = name
        self._c = cands

    def discover(self) -> list[Candidate]:
        return self._c


def _always_alive(_c: Candidate) -> str | None:
    return None


def _never_alive(_c: Candidate) -> str | None:
    return "dead-manifest"


def _no_yt_live(_ids: Iterable[str]) -> dict[str, str]:
    return {}


_NO_EXCLUDE: frozenset[str] = frozenset()


class _Clock:
    """A fake wall clock, in unix seconds, moved by hand."""

    now: float

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, hours: float) -> None:
        self.now += hours * 3600


def _scheduler(
    sources: list[Source],
    *,
    drop_reason_for: Callable[[Candidate], str | None] = _always_alive,
    youtube_live: Callable[[Iterable[str]], Mapping[str, str]] = _no_yt_live,
    intervals: Mapping[str, float] | None = None,
    clock: _Clock | None = None,
    merges: list[list[CatalogueEntry]] | None = None,
    exclude_categories: frozenset[str] = _NO_EXCLUDE,
    max_parallel_sources: int = 4,
    fetch_stats: Callable[[str], dict[str, dict[str, int]]] | None = None,
    db: store.Store | None = None,
) -> Scheduler:
    sink = merges if merges is not None else []
    return Scheduler(
        sources,
        intervals or {s.name: 6 for s in sources},
        drop_reason_for=drop_reason_for,
        youtube_live=youtube_live,
        on_merge=sink.append,
        exclude_categories=exclude_categories,
        max_parallel_sources=max_parallel_sources,
        fetch_stats=fetch_stats,
        clock=clock or _Clock(),
        store=db or open_storeless(),
    )


def _finish_all(s: Scheduler, started: list[str]) -> None:
    for _ in started:
        assert s.finish_next(timeout=5), "a dispatched refresh never finished"


def _build(
    sources: list[Source],
    *,
    drop_reason_for: Callable[[Candidate], str | None],
    youtube_live: Callable[[Iterable[str]], Mapping[str, str]],
    history: dict[str, Hist],
    exclude_categories: frozenset[str] = _NO_EXCLUDE,
    max_parallel_sources: int = 4,
    fetch_stats: Callable[[str], dict[str, dict[str, int]]] | None = None,
) -> list[CatalogueEntry]:
    """Refresh every source once on a new scheduler and return the last merge.

    `history` carries each source's state between calls, as a restart-free run of
    refreshes would, so a test can feed a source a different set each time.
    """
    merges: list[list[CatalogueEntry]] = []
    hists = [history.setdefault(src.name, Hist()) for src in sources]
    # Past every carried-over refresh's 6h interval, so each source is due again.
    last = max((h.last_refresh_at or 0.0 for h in hists), default=0.0)
    s = _scheduler(
        sources,
        drop_reason_for=drop_reason_for,
        youtube_live=youtube_live,
        clock=_Clock(last + 7 * 3600),
        merges=merges,
        exclude_categories=exclude_categories,
        max_parallel_sources=max_parallel_sources,
        fetch_stats=fetch_stats,
    )
    for src, h in zip(sources, hists, strict=True):
        s.history[src.name] = h
    refreshed: list[str] = []
    while started := s.tick():
        _finish_all(s, started)
        refreshed += started
    assert sorted(refreshed) == sorted(src.name for src in sources)
    return merges[-1]


def _all_yt_live(ids: Iterable[str]) -> dict[str, str]:
    return dict.fromkeys(ids, "")


# ---------------------------------------------------------------------------
# Test 1: Cross-source dedup (the fix)
# ---------------------------------------------------------------------------


def test_cross_source_dedup_collapses_same_predisc_key() -> None:
    """Two sources that each yield yt:AAA must collapse to ONE entry.

    The merge runs over every source's kept set, so a cam found by
    both "youtube-api" and "cxtvlive" with the same predisc_key collapses
    to a single CatalogueEntry.  Category comes from the scraper (cxtvlive
    wins per _CAT_RANK), title comes from youtube-api (canonical YT title).
    """
    yt_cam = _make_candidate(
        source="youtube-api",
        key="yt:AAA",
        page="https://www.youtube.com/watch?v=AAA",
        target="https://www.youtube.com/watch?v=AAA",
        title="Official",
        category=None,
    )
    scraper_cam = _make_candidate(
        source="cxtvlive",
        key="yt:AAA",
        page="https://cxtvlive.com/cam/AAA",
        target="https://www.youtube.com/watch?v=AAA",
        title="",
        category="Beaches",
    )

    src_yt = _Src("youtube-api", [yt_cam])
    src_scraper = _Src("cxtvlive", [scraper_cam])

    result = _build(
        [src_yt, src_scraper],
        drop_reason_for=_always_alive,
        youtube_live=lambda _ids: {"AAA": ""},
        history={},
    )

    # The fix: cross-source dedup means these collapse to ONE entry
    assert len(result) == 1, (
        f"Expected 1 entry after cross-source dedup, got {len(result)}: {result}"
    )

    entry = result[0]
    # Scraper (cxtvlive) wins on category per _CAT_RANK
    assert entry.category == "Beaches"
    # youtube-api provides the canonical title
    assert entry.title == "Official"


def test_shared_cam_is_probed_once_across_sources() -> None:
    """The same cam carried by two sources costs one liveness probe.

    The two sources are a first-party site and a meta-aggregator; the second gets the
    cached verdict. The verdict fans out: a cached dead cam counts as dropped in BOTH
    sources' stats.
    """
    probed: list[str] = []

    def counting(c: Candidate) -> str | None:
        probed.append(c.target_url)
        return "dead-manifest" if "dead" in c.target_url else None

    shared_live = {
        "key": "feratel:5751",
        "target": "https://webtv.feratel.com/webtv/?cam=5751",
        "title": "Ischgl",
    }
    shared_dead = {
        "key": "hls:https://cdn.example/dead.m3u8",
        "target": "https://cdn.example/dead.m3u8",
        "title": "Dead",
    }
    a = _Src(
        "feratel",
        [
            _make_candidate(source="feratel", page="https://a/1", **shared_live),
            _make_candidate(source="feratel", page="https://a/2", **shared_dead),
        ],
    )
    b = _Src(
        "windy",
        [
            _make_candidate(source="windy", page="https://b/1", **shared_live),
            _make_candidate(source="windy", page="https://b/2", **shared_dead),
        ],
    )

    history: dict[str, Hist] = {}
    result = _build(
        [a, b],
        drop_reason_for=counting,
        youtube_live=lambda _ids: {},
        history=history,
        max_parallel_sources=1,  # deterministic: no probe races in the test
    )

    # one probe per unique cam, not per source
    assert sorted(probed) == [
        "https://cdn.example/dead.m3u8",
        "https://webtv.feratel.com/webtv/?cam=5751",
    ]
    # the live cam dedups to one entry; the dead one ships nowhere
    assert len(result) == 1
    # the cached dead verdict still counts against BOTH sources' raw stats
    assert history["feratel"].last_raw_kept == 1
    assert history["windy"].last_raw_kept == 1


def test_same_key_different_target_probed_independently() -> None:
    """The verdict cache keys on target_url, not the merge key.

    Two candidates that share a predisc_key but carry DIFFERENT target_urls (a tokened
    vs bare CDN URL, or a feratel cam whose URL shape routes to a different extractor)
    must each be probed — sharing one verdict would ship a URL the probe never touched,
    or drop a live one.
    """
    probed: list[str] = []

    def counting(c: Candidate) -> str | None:
        probed.append(c.target_url)
        # only the tokened URL is live; the bare one 403s (dead)
        return None if "token=" in c.target_url else "dead-manifest"

    # same bare m3u8 -> same predisc_key, but one candidate carries an auth token
    tokened = _make_candidate(
        source="tokened-site",
        key="hls:https://cdn.x/cam.m3u8",
        page="https://tokened-site/1",
        target="https://cdn.x/cam.m3u8?token=SECRET",
        title="Live",
    )
    bare = _make_candidate(
        source="camscape",
        key="hls:https://cdn.x/cam.m3u8",
        page="https://camscape/1",
        target="https://cdn.x/cam.m3u8",
        title="Bare",
    )

    # camscape FIRST: under the old predisc_key cache this is the failing order —
    # the bare URL probes dead, caches "dead" under the shared key, and the tokened
    # live cam inherits it unprobed and is lost. Keying on target_url must prevent that.
    result = _build(
        [_Src("camscape", [bare]), _Src("tokened-site", [tokened])],
        drop_reason_for=counting,
        youtube_live=lambda _ids: {},
        history={},
        max_parallel_sources=1,
    )

    # both URLs probed (not deduped by the shared key); the bare one is dropped dead
    # BEFORE dedup, so only the live tokened URL survives and ships
    assert sorted(probed) == [
        "https://cdn.x/cam.m3u8",
        "https://cdn.x/cam.m3u8?token=SECRET",
    ]
    assert len(result) == 1
    assert result[0].target_url == "https://cdn.x/cam.m3u8?token=SECRET"


# ---------------------------------------------------------------------------
# Test 2: Multi-source build — entries deduped, category-mapped, non-empty id
# ---------------------------------------------------------------------------


def test_multisource_deduped_category_mapped_id_non_empty() -> None:
    """Two sources with distinct keys make two entries; Birds→Animals; id set."""
    c1 = _make_candidate(
        source="worldcams",
        key="hls:birds1",
        page="https://example.com/birds",
        target="https://example.com/birds.m3u8",
        category="Birds",
        title="Bird Cam",
    )
    c2 = _make_candidate(
        source="cxtvlive",
        key="hls:city1",
        page="https://example.com/city",
        target="https://example.com/city.m3u8",
        category="Cities",
        title="City Cam",
    )
    src1 = _Src("worldcams", [c1])
    src2 = _Src("cxtvlive", [c2])

    result = _build(
        [src1, src2],
        drop_reason_for=_always_alive,
        youtube_live=_all_yt_live,
        history={},
    )

    assert len(result) == 2

    birds_entry = next(e for e in result if e.title == "Bird Cam")
    city_entry = next(e for e in result if e.title == "City Cam")

    # Category mapping: Birds → Animals
    assert birds_entry.category == "Animals"
    assert city_entry.category == "Cities"

    # Non-empty id (sha256[:16] = 16 hex chars)
    assert birds_entry.id != ""
    assert len(birds_entry.id) == 16
    assert city_entry.id != ""
    assert len(city_entry.id) == 16


def test_entity_escaped_title_is_decoded() -> None:
    """Data API snippets and several scraped pages hand over titles still HTML-escaped.

    A player shows `&amp;` literally, so the build decodes them.
    """
    cam = _make_candidate(title="Ryan&#39;s &quot;Ground View&quot; Fish &amp; Chips")

    [entry] = _build(
        [_Src("ozolio", [cam])],
        drop_reason_for=_always_alive,
        youtube_live=_all_yt_live,
        history={},
    )

    assert entry.title == 'Ryan\'s "Ground View" Fish & Chips'


def test_escaped_copy_of_a_cam_does_not_win_the_merge() -> None:
    """Titles are decoded before the merge, not after it.

    Dedup keeps the longest title, and escaping makes a title longer.
    """
    escaped = _make_candidate(source="ozolio", title="Harry&#039;s Bar")
    # Longer than the escaped title once decoded (11), shorter than it raw (16).
    clean = _make_candidate(source="camscape", title="Harry's Bar, HI")

    [entry] = _build(
        [_Src("ozolio", [escaped]), _Src("camscape", [clean])],
        drop_reason_for=_always_alive,
        youtube_live=_all_yt_live,
        history={},
    )

    assert entry.title == "Harry's Bar, HI"


# ---------------------------------------------------------------------------
# Test 3: Dead dropped
# ---------------------------------------------------------------------------


def test_non_yt_dead_candidate_excluded() -> None:
    """A non-yt candidate whose probe returns a drop reason is excluded."""
    alive_cand = _make_candidate(
        key="hls:alive",
        page="https://example.com/alive",
        target="https://example.com/alive.m3u8",
    )
    dead_cand = _make_candidate(
        key="hls:dead",
        page="https://example.com/dead",
        target="https://example.com/dead.m3u8",
    )

    def drop_reason_for(c: Candidate) -> str | None:
        return None if c.predisc_key == "hls:alive" else "dead-manifest"

    src = _Src("worldcams", [alive_cand, dead_cand])
    result = _build(
        [src], drop_reason_for=drop_reason_for, youtube_live=_no_yt_live, history={}
    )

    assert len(result) == 1
    assert result[0].source_page_url == "https://example.com/alive"


def test_yt_candidate_excluded_when_not_in_live_set() -> None:
    """A yt candidate whose id is NOT in youtube_live is excluded."""
    live_cand = _make_candidate(
        source="youtube-api",
        key="yt:LIVE001",
        page="https://www.youtube.com/watch?v=LIVE001",
        target="https://www.youtube.com/watch?v=LIVE001",
    )
    dead_cand = _make_candidate(
        source="youtube-api",
        key="yt:DEAD001",
        page="https://www.youtube.com/watch?v=DEAD001",
        target="https://www.youtube.com/watch?v=DEAD001",
    )
    src = _Src("youtube-api", [live_cand, dead_cand])

    def youtube_live(_ids: Iterable[str]) -> dict[str, str]:
        return {"LIVE001": ""}

    result = _build(
        [src], drop_reason_for=_never_alive, youtube_live=youtube_live, history={}
    )

    assert len(result) == 1
    assert result[0].source_page_url == live_cand.source_page_url


def test_youtube_category_applied_from_live() -> None:
    """A youtube-source cam gets its category from the live lookup."""
    cam = _make_candidate(
        source="youtube-api",
        key="yt:LIVE001",
        page="https://www.youtube.com/watch?v=LIVE001",
        target="https://www.youtube.com/watch?v=LIVE001",
    )
    result = _build(
        [_Src("youtube-api", [cam])],
        drop_reason_for=_always_alive,
        youtube_live=lambda _ids: {"LIVE001": "Travel & Events"},
        history={},
    )
    assert len(result) == 1
    assert result[0].category == "Travel & Events"


# ---------------------------------------------------------------------------
# Test 4: Empty guard
# ---------------------------------------------------------------------------


def _make_old_candidates(n: int) -> list[Candidate]:
    return [
        _make_candidate(
            source="worldcams",
            key=f"hls:old{i}",
            page=f"https://example.com/old{i}",
            target=f"https://example.com/old{i}.m3u8",
            title=f"Old Cam {i}",
            category="Cities",
        )
        for i in range(n)
    ]


def test_empty_guard_reuses_previous_on_first_shrink() -> None:
    """Prior kept_set of 10; build returning 2 kept (>50% drop) reuses old 10."""
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, kept_set=old_kept)
    }

    # Only 2 candidates survive — 80% drop, triggers guard
    new_cands = [
        _make_candidate(
            key=f"hls:new{i}",
            page=f"https://example.com/new{i}",
            target=f"https://example.com/new{i}.m3u8",
            title=f"New Cam {i}",
        )
        for i in range(2)
    ]
    src = _Src("worldcams", new_cands)

    result = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )

    # Should get the 10 old entries (converted from old_kept), not the 2 new ones
    assert len(result) == 10
    old_titles = {f"Old Cam {i}" for i in range(10)}
    assert {e.title for e in result} == old_titles


def test_empty_guard_accepts_after_agree_to_accept_consecutive_shrinks() -> None:
    """After 2 consecutive shrinks the new small baseline is accepted."""
    old_kept = _make_old_candidates(10)
    # Streak already at AGREE_TO_ACCEPT - 1 (one more shrink tips it over)
    history: dict[str, Hist] = {
        "worldcams": Hist(
            last_count=10,
            shrink_streak=AGREE_TO_ACCEPT - 1,
            kept_set=old_kept,
        )
    }

    new_cands = [
        _make_candidate(
            key=f"hls:new{i}",
            page=f"https://example.com/new{i}",
            target=f"https://example.com/new{i}.m3u8",
            title=f"New Cam {i}",
        )
        for i in range(2)
    ]
    src = _Src("worldcams", new_cands)

    result = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )

    # New small set accepted — 2 entries
    assert len(result) == 2
    assert all(e.title.startswith("New Cam") for e in result)


def test_empty_guard_no_history_promotes_unconditionally() -> None:
    """First build (no history) always promotes, regardless of count."""
    cand = _make_candidate(key="hls:only1", title="Only Cam")
    src = _Src("worldcams", [cand])

    result = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history={}
    )

    assert len(result) == 1
    assert result[0].title == "Only Cam"


def test_empty_guard_streak_increments_then_resets() -> None:
    """Two consecutive shrinks: the first reuses the old set; the second accepts."""
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, kept_set=old_kept)
    }

    small_cands = [
        _make_candidate(
            key=f"hls:s{i}",
            page=f"https://example.com/s{i}",
            target=f"https://example.com/s{i}.m3u8",
            title=f"S {i}",
        )
        for i in range(2)
    ]
    src = _Src("worldcams", small_cands)

    # First guarded build → streak becomes 1, old 10 returned
    first = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(first) == 10
    assert history["worldcams"].shrink_streak == 1
    assert history["worldcams"].last_count == 10  # not updated yet

    # Second guarded build → streak reaches AGREE_TO_ACCEPT (2): new baseline accepted
    second = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(second) == 2
    assert history["worldcams"].shrink_streak == 0  # reset after acceptance
    assert history["worldcams"].last_count == 2  # updated to new small count


def test_crash_reuses_the_kept_set_and_never_wipes() -> None:
    """A source whose discover() keeps raising keeps reusing its kept set.

    Two consecutive crashes must NOT be accepted as an empty set.
    """
    cam = _make_candidate(
        source="worldcams",
        key="hls:a",
        page="https://example.com/a",
        target="https://example.com/a.m3u8",
    )

    class _CrashSrc:
        name: str = "worldcams"
        interval_hours: float = 6
        calls: int

        def __init__(self) -> None:
            self.calls = 0

        def discover(self) -> list[Candidate]:
            self.calls += 1
            if self.calls == 1:
                return [cam]
            msg = "boom"
            raise RuntimeError(msg)

    src = _CrashSrc()
    history: dict[str, Hist] = {}
    first = _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(first) == 1
    for _ in range(2):  # the second crash in a row is the one that could wipe it
        result = _build(
            [src],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
        )
        assert len(result) == 1


def test_health_fields_record_raw_outcome_under_guard() -> None:
    """The per-cycle raw fields record the actual failure.

    They feed /health monitoring and show the failure even while the empty-guard masks
    it: on a collapse, last_raw_kept is the shrunk count (the raw result), not the
    larger count the guard keeps serving.
    """
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, kept_set=old_kept)
    }
    small = [
        _make_candidate(
            key=f"hls:s{i}",
            page=f"https://example.com/s{i}",
            target=f"https://example.com/s{i}.m3u8",
            title=f"S {i}",
        )
        for i in range(2)
    ]

    # Collapse cycle: guard reuses the old 10, but the raw field shows the true 2.
    _build(
        [_Src("worldcams", small)],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    h = history["worldcams"]
    assert h.last_raw_kept == 2  # RAW: what this cycle actually produced
    assert h.last_crashed is False
    assert h.last_discovered == 2

    # Healthy cycle (10 candidates, no collapse): raw reflects the full set.
    ten = _make_old_candidates(10)
    _build(
        [_Src("worldcams", ten)],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    h = history["worldcams"]
    assert h.last_raw_kept == 10
    assert h.last_crashed is False


def test_health_fields_flag_crash() -> None:
    """A crashed discover records last_crashed=True and last_raw_kept=0.

    That holds even though the empty-guard keeps serving the kept set.
    """
    cam = _make_candidate(
        source="worldcams", key="hls:a", target="https://example.com/a.m3u8"
    )

    class _CrashSrc:
        name: str = "worldcams"
        interval_hours: float = 6
        calls: int = 0

        def discover(self) -> list[Candidate]:
            self.calls += 1
            if self.calls == 1:
                return [cam]
            msg = "boom"
            raise RuntimeError(msg)

    src = _CrashSrc()
    history: dict[str, Hist] = {}
    _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert history["worldcams"].last_crashed is False  # first cycle succeeded

    _build(
        [src], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    h = history["worldcams"]
    assert h.last_crashed is True
    assert h.last_raw_kept == 0


def test_source_liveness_failure_is_isolated_and_reuses_history() -> None:
    """A source whose liveness probe raises is treated like a crash.

    Sources run concurrently; it reuses its kept set and must NOT sink the other
    sources' results.
    """
    boom = _make_candidate(
        source="worldcams", key="hls:boom", target="https://example.com/boom.m3u8"
    )
    good = _make_candidate(
        source="cxtvlive", key="hls:ok", target="https://example.com/ok.m3u8"
    )

    def _selective_alive(c: Candidate) -> str | None:
        if "boom" in c.target_url:
            msg = "liveness boom"
            raise RuntimeError(msg)
        return None

    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=1, shrink_streak=0, kept_set=[boom])
    }
    entries = _build(
        [_Src("worldcams", [boom]), _Src("cxtvlive", [good])],
        drop_reason_for=_selective_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    srcs = {e.source for e in entries}
    assert "cxtvlive" in srcs  # healthy source unaffected by the other's liveness crash
    assert "worldcams" in srcs  # failed source reused its kept set
    assert history["worldcams"].kept_set == [boom]  # history not wiped


def test_exclude_categories_drops_matching_entries() -> None:
    """Entries whose mapped category is excluded are dropped.

    Matching is case-insensitive and on the unified category: Birds maps to Animals, so
    excluding 'animals' drops it.
    """
    beach = _make_candidate(
        source="worldcams",
        key="hls:b",
        page="https://example.com/b",
        target="https://example.com/b.m3u8",
        category="Beaches",
        title="Beach Cam",
    )
    bird = _make_candidate(
        source="worldcams",
        key="hls:r",
        page="https://example.com/r",
        target="https://example.com/r.m3u8",
        category="Birds",
        title="Bird Cam",
    )
    result = _build(
        [_Src("worldcams", [beach, bird])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history={},
        exclude_categories=frozenset({"animals"}),
    )
    assert {e.title for e in result} == {"Beach Cam"}


def test_title_category_recovered_for_uncategorised() -> None:
    # a source that gave no category -> "Other" -> recovered from the title keyword;
    # a title with no signal stays "Other"
    bear = _make_candidate(
        key="hls:bear",
        page="https://e.com/bear",
        target="https://e.com/bear.m3u8",
        title="Brown Bear Cam — Alaska, USA",
    )
    odd = _make_candidate(
        key="hls:odd",
        page="https://e.com/odd",
        target="https://e.com/odd.m3u8",
        title="Channel Cam",
    )
    result = _build(
        [_Src("worldcams", [bear, odd])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history={},
    )
    cats = {e.title: e.category for e in result}
    assert cats["Brown Bear Cam — Alaska, USA"] == "Animals"
    assert cats["Channel Cam"] == "Other"


def test_title_fallback_never_overrides_source_category() -> None:
    # a real mapped category wins over a title keyword; an unrecognised source category
    # stays "Unmapped Category" (only "Other" is title-recovered)
    real = _make_candidate(
        key="hls:r",
        page="https://e.com/r",
        target="https://e.com/r.m3u8",
        title="Times Square",
        category="Birds",  # -> Animals, NOT "Cities" from title
    )
    unmapped = _make_candidate(
        key="hls:u",
        page="https://e.com/u",
        target="https://e.com/u.m3u8",
        title="Bear Beach",
        category="Zzz Unknown Probe Cat",
    )
    result = _build(
        [_Src("worldcams", [real, unmapped])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history={},
    )
    cats = {e.title: e.category for e in result}
    assert cats["Times Square"] == "Animals"
    assert cats["Bear Beach"] == "Unmapped Category"


# ---------------------------------------------------------------------------
# Per-source fetch outcome — an empty source must say WHY
# ---------------------------------------------------------------------------


def _stats(
    canned: dict[str, dict[str, dict[str, int]]],
) -> Callable[[str], dict[str, dict[str, int]]]:
    """Build a fetch_stats accessor over canned {source: {host: {outcome: n}}}."""

    def accessor(name: str) -> dict[str, dict[str, int]]:
        return canned.get(name, {})

    return accessor


def test_blocked_source_names_the_host_and_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blocked scraper's real shape: a handful of fetches, all 403, nothing found."""
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", [])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats(
                {"skyline": {"www.skylinewebcams.com": {"http-403": 11}}}
            ),
        )
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "skyline: 0 kept / 0 discovered" in caplog.text
    assert "11 fetched, 0 ok" in caplog.text
    assert "www.skylinewebcams.com" in caplog.text
    assert "http-403" in caplog.text


def test_soft_block_returning_200_is_still_flagged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Cloudflare challenge page is an HTTP 200.

    A failure counter sees nothing wrong. This is the case the whole per-host-failure
    approach could not detect.
    """
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", [])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats({"skyline": {"www.skylinewebcams.com": {"ok": 11}}}),
        )
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "11 fetched ok but extracted 0 URLs" in caplog.text


def test_healthy_source_logs_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("explore", [_make_candidate(key="hls:x")])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats({"explore": {"explore.org": {"ok": 1}}}),
        )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert "explore: 1 kept / 1 discovered" in caplog.text


def test_partial_failures_are_reported_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """~35% dead cams is skyline's NORMAL state — informative, not a warning."""
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", [_make_candidate(key="hls:x")])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats(
                {"skyline": {"www.skylinewebcams.com": {"ok": 65, "http-404": 35}}}
            ),
        )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert "100 fetched, 35 failed" in caplog.text


def test_source_with_no_fetcher_is_not_misreported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """youtube-api uses googleapiclient, not Fetcher.

    No stats, and it must not be described as "0 fetched" as though the network were the
    problem.
    """
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("youtube-api", [])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats({}),
        )
    assert "youtube-api: 0 kept / 0 discovered" in caplog.text
    assert "fetched" not in caplog.text


def test_fetch_stats_reach_the_source_line(caplog: pytest.LogCaptureFixture) -> None:
    """Per-source fetch outcomes surface on the log line.

    /health does not carry them, so the line is the only place a blocked source explains
    itself.
    """
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", [])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats(
                {"skyline": {"www.skylinewebcams.com": {"http-403": 11}}}
            ),
        )
    assert "11 fetched, 0 ok (11x http-403 www.skylinewebcams.com)" in caplog.text


# ---------------------------------------------------------------------------
# Liveness drop reasons — diagnosing the partial-degradation case (1700 -> 520)
# ---------------------------------------------------------------------------


def _drops(reason: str) -> Callable[[Candidate], str | None]:
    def check(_c: Candidate) -> str | None:
        return reason

    return check


def test_drop_breakdown_on_the_source_line(caplog: pytest.LogCaptureFixture) -> None:
    cands = [_make_candidate(key=f"hls:{i}") for i in range(3)]
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", cands)],
            drop_reason_for=_drops("dead-manifest"),
            youtube_live=_no_yt_live,
            history={},
        )
    assert "skyline: 0 kept / 3 discovered" in caplog.text
    assert "dropped 3 (3x dead-manifest)" in caplog.text


def test_no_extractor_hosts_are_named(caplog: pytest.LogCaptureFixture) -> None:
    """The top-hosts line tells you which extractor is worth writing next."""
    cands = [
        _make_candidate(key="hls:a", target="https://rtsp.me/a"),
        _make_candidate(key="hls:b", target="https://rtsp.me/b"),
        _make_candidate(key="hls:c", target="https://ivideon.com/c"),
    ]
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("camscape", cands)],
            drop_reason_for=_drops("no-extractor"),
            youtube_live=_no_yt_live,
            history={},
        )
    assert "no extractor for 3 candidate(s)" in caplog.text
    assert "2x rtsp.me" in caplog.text
    assert "1x ivideon.com" in caplog.text


def test_resolve_failure_detail_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Separates "yt-dlp is broken" from "the cam is off air"."""
    cands = [_make_candidate(key=f"hls:{i}") for i in range(2)]
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("worldcams", cands)],
            drop_reason_for=_drops("resolve-failed:yt-dlp failed: Sign in to confirm"),
            youtube_live=_no_yt_live,
            history={},
        )
    assert "dropped 2 (2x resolve-failed)" in caplog.text
    assert "top resolve failures" in caplog.text
    assert "Sign in to confirm" in caplog.text


def test_clean_source_logs_no_drop_suffix(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("explore", [_make_candidate(key="hls:x")])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
        )
    assert "explore: 1 kept / 1 discovered" in caplog.text
    assert "dropped" not in caplog.text


def test_yt_offline_is_counted_as_a_reason(caplog: pytest.LogCaptureFixture) -> None:
    """A YouTube API outage spikes this across every source at once."""
    cam = _make_candidate(
        source="youtube-api",
        key="yt:DEAD001",
        target="https://www.youtube.com/watch?v=DEAD001",
    )
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("youtube-api", [cam])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history={},
        )
    assert "dropped 1 (1x yt-offline)" in caplog.text


# ---------------------------------------------------------------------------
# Source status — the empty-guard falls silent once it accepts a zero, which would
# leave a long outage logging nothing at all
# ---------------------------------------------------------------------------


def test_zero_source_warns_every_single_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    history: dict[str, Hist] = {}
    good = _Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(10)])
    _build(
        [good], drop_reason_for=_always_alive, youtube_live=_no_yt_live, history=history
    )
    dead = _Src("skyline", [])
    for cycle in range(AGREE_TO_ACCEPT + 3):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
            _build(
                [dead],
                drop_reason_for=_always_alive,
                youtube_live=_no_yt_live,
                history=history,
            )
        assert any(
            r.levelno == logging.WARNING and "skyline" in r.getMessage()
            for r in caplog.records
        ), f"cycle {cycle} logged nothing"


def test_status_transition_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    history: dict[str, Hist] = {}
    dead = _Src("skyline", [])
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        _build(
            [dead],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
        )
    assert "-> dead" in caplog.text
    assert history["skyline"].status == "dead"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        _build(
            [dead],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
        )
    assert "-> dead" not in caplog.text  # unchanged status: no repeat transition
    assert "still dead" in caplog.text  # but it still says something


def test_recovery_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    history: dict[str, Hist] = {}
    _build(
        [_Src("skyline", [])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        _build(
            [_Src("skyline", [_make_candidate(key="hls:back")])],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
        )
    assert "dead -> ok" in caplog.text
    assert history["skyline"].status == "ok"


def test_still_dead_does_not_stack_on_the_fetch_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the fetch line already explains the outage, don't repeat a vaguer one."""
    history: dict[str, Hist] = {}
    dead = _Src("skyline", [])
    stats = _stats({"skyline": {"www.skylinewebcams.com": {"http-403": 11}}})
    _build(
        [dead],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
        fetch_stats=stats,
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        _build(
            [dead],
            drop_reason_for=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
            fetch_stats=stats,
        )
    assert "11 fetched, 0 ok" in caplog.text
    assert "still dead" not in caplog.text


def test_degraded_status_is_tracked() -> None:
    history: dict[str, Hist] = {}
    _build(
        [_Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(10)])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    _build(
        [_Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(2)])],
        drop_reason_for=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    assert history["skyline"].status == "degraded"


# ---------------------------------------------------------------------------
# The scheduler: intervals, slots, order, merges, shared verdicts
# ---------------------------------------------------------------------------


class _SlowSrc:
    """A source whose refresh moves the fake clock on, as a long scrape would."""

    name: str
    interval_hours: float = 6
    _clock: _Clock
    _hours: float

    def __init__(self, name: str, clock: _Clock, hours: float) -> None:
        self.name = name
        self._clock = clock
        self._hours = hours

    def discover(self) -> list[Candidate]:
        self._clock.advance(self._hours)
        return [_make_candidate(source=self.name, key=f"hls:{self.name}")]


class _GatedSrc:
    """A source whose discover() blocks until the test opens its gate."""

    name: str
    interval_hours: float = 6
    gate: threading.Event
    calls: int

    def __init__(self, name: str, gate: threading.Event | None = None) -> None:
        self.name = name
        self.gate = gate or threading.Event()
        self.calls = 0

    def discover(self) -> list[Candidate]:
        self.calls += 1
        assert self.gate.wait(timeout=5)
        return [
            _make_candidate(
                source=self.name,
                key=f"hls:{self.name}",
                target=f"https://example.com/{self.name}.m3u8",
            )
        ]


def test_interval_runs_from_when_the_refresh_finished() -> None:
    clock = _Clock()
    s = _scheduler(
        [_SlowSrc("explore", clock, 3)], intervals={"explore": 1}, clock=clock
    )
    _finish_all(s, s.tick())
    h = s.history["explore"]
    assert h.last_refresh_at == clock.now  # stamped at the end, not the start
    assert h.last_refresh_seconds == 3 * 3600
    clock.advance(0.9)
    assert s.tick() == []  # 3.9h after it started, but only 0.9h after it ended
    clock.advance(0.1)
    assert s.tick() == ["explore"]


def test_a_source_never_runs_two_refreshes_at_once() -> None:
    clock = _Clock()
    src = _GatedSrc("explore")
    s = _scheduler([src], intervals={"explore": 1}, clock=clock)
    assert s.tick() == ["explore"]
    clock.advance(5)  # long overdue while its refresh is still running
    assert s.tick() == []
    src.gate.set()
    _finish_all(s, ["explore"])
    assert src.calls == 1
    assert s.tick() == []  # due again an interval after it finished, not at once


def test_max_parallel_sources_caps_running_refreshes() -> None:
    gate = threading.Event()
    srcs = [_GatedSrc(n, gate) for n in ("a", "b", "c")]
    s = _scheduler(list(srcs), max_parallel_sources=2)
    assert s.tick() == ["a", "b"]  # equally overdue: listed order
    assert s.tick() == []  # both slots busy, c waits
    gate.set()
    _finish_all(s, ["a", "b"])
    assert s.tick() == ["c"]
    _finish_all(s, ["c"])


def test_most_overdue_source_goes_first() -> None:
    clock = _Clock()
    srcs = [_Src(n, [_make_candidate(source=n, key=f"hls:{n}")]) for n in "abc"]
    s = _scheduler(
        list(srcs),
        intervals={"a": 6, "b": 2, "c": 4},
        clock=clock,
        max_parallel_sources=1,
    )
    for _ in srcs:  # first refreshes, all at the same moment
        _finish_all(s, s.tick())
    clock.advance(7)  # now a is 1h overdue, b 5h, c 3h
    order: list[str] = []
    for _ in srcs:
        started = s.tick()
        _finish_all(s, started)
        order += started
    assert order == ["b", "c", "a"]


def test_catalogue_merges_after_every_refresh(caplog: pytest.LogCaptureFixture) -> None:
    """The playlist serves from the first refresh and grows as others land."""
    merges: list[list[CatalogueEntry]] = []
    srcs = [
        _Src(n, [_make_candidate(source=n, key=f"hls:{n}", target=f"https://{n}/x")])
        for n in ("worldcams", "skyline")
    ]
    s = _scheduler(list(srcs), merges=merges, max_parallel_sources=1)
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _finish_all(s, s.tick())
        assert [e.source for e in merges[-1]] == ["worldcams"]
        _finish_all(s, s.tick())
    assert sorted(e.source for e in merges[-1]) == ["skyline", "worldcams"]
    assert len(merges) == 2
    assert "catalogue merged: 1 entries" in caplog.text
    assert "catalogue merged: 2 entries" in caplog.text


def test_a_merge_keeps_the_sources_that_did_not_refresh() -> None:
    clock = _Clock()
    merges: list[list[CatalogueEntry]] = []
    srcs = [
        _Src(n, [_make_candidate(source=n, key=f"hls:{n}", target=f"https://{n}/x")])
        for n in ("explore", "skyline")
    ]
    s = _scheduler(
        list(srcs),
        intervals={"explore": 1, "skyline": 24},
        clock=clock,
        merges=merges,
    )
    _finish_all(s, s.tick())
    clock.advance(1)
    assert s.tick() == ["explore"]
    _finish_all(s, ["explore"])
    assert sorted(e.source for e in merges[-1]) == ["explore", "skyline"]


def test_verdicts_are_shared_across_refreshes_until_they_expire() -> None:
    """Two sources carrying one URL cost one probe while the verdict is fresh."""
    clock = _Clock()
    probed: list[str] = []

    def counting(c: Candidate) -> str | None:
        probed.append(c.target_url)
        return None

    shared = {"key": "feratel:5751", "target": "https://webtv.feratel.com/?cam=5751"}
    srcs = [
        _Src(n, [_make_candidate(source=n, page=f"https://{n}/1", **shared)])
        for n in ("feratel", "windy")
    ]
    s = _scheduler(
        list(srcs),
        drop_reason_for=counting,
        intervals={"feratel": 1, "windy": 1.25},
        clock=clock,
        max_parallel_sources=1,
    )
    _finish_all(s, s.tick())  # feratel probes
    clock.advance(0.25)
    _finish_all(s, s.tick())  # windy, 15 min later, reuses the verdict
    assert len(probed) == 1
    clock.advance(1.5)  # both due again, the verdict 105 min old
    _finish_all(s, s.tick())
    assert len(probed) == 2  # expired: probed afresh
    _finish_all(s, s.tick())
    assert len(probed) == 2  # ...and that fresh verdict is shared again


def test_run_once_starts_and_finishes_a_due_refresh() -> None:
    merges: list[list[CatalogueEntry]] = []
    s = _scheduler([_Src("worldcams", [_make_candidate()])], merges=merges)
    s.run_once()
    assert len(merges) == 1
    assert s.history["worldcams"].last_refresh_at is not None


def test_a_failure_after_the_refresh_does_not_strand_the_source() -> None:
    """A source whose finish step raises is still due again an interval later."""
    clock = _Clock()

    def broken_stats(_name: str) -> dict[str, dict[str, int]]:
        msg = "stats boom"
        raise RuntimeError(msg)

    s = _scheduler(
        [_Src("explore", [_make_candidate()])],
        intervals={"explore": 1},
        clock=clock,
        fetch_stats=broken_stats,
    )
    assert s.tick() == ["explore"]
    with contextlib.suppress(RuntimeError):
        s.finish_next(timeout=5)
    assert s.history["explore"].last_refresh_at == clock.now  # it did run
    clock.advance(1)
    assert s.tick() == ["explore"]  # not stuck as "running" forever


# ---------------------------------------------------------------------------
# Restart: a second scheduler opened on the same store
# ---------------------------------------------------------------------------


def _cams(name: str, n: int) -> list[Candidate]:
    return [
        _make_candidate(
            source=name,
            key=f"hls:{name}{i}",
            page=f"https://{name}/{i}",
            target=f"https://{name}/{i}.m3u8",
            title=f"{name} cam {i}",
        )
        for i in range(n)
    ]


def _restart(
    path: Path,
    sources: list[Source],
    clock: _Clock,
    *,
    merges: list[list[CatalogueEntry]] | None = None,
    intervals: Mapping[str, float] | None = None,
) -> Scheduler:
    return _scheduler(
        sources,
        db=store.Store(path),
        clock=clock,
        merges=merges,
        intervals=intervals,
    )


def test_a_restart_serves_the_stored_kept_sets_at_once(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    clock = _Clock()
    srcs: list[Source] = [_Src("explore", _cams("explore", 3)), _Src("feratel", [])]
    first: list[list[CatalogueEntry]] = []
    s = _restart(db, srcs, clock, merges=first)
    _finish_all(s, s.tick())

    clock.advance(0.5)
    after: list[list[CatalogueEntry]] = []
    s2 = _restart(db, srcs, clock, merges=after)
    assert after == [first[-1]]  # merged at startup, before any refresh
    assert s2.tick() == []  # neither is due: each waits out its interval


def test_only_due_sources_refresh_after_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    clock = _Clock()
    srcs: list[Source] = [
        _Src("explore", _cams("explore", 1)),
        _Src("skyline", _cams("skyline", 1)),
    ]
    intervals = {"explore": 1, "skyline": 24}
    s = _restart(db, srcs, clock, intervals=intervals)
    _finish_all(s, s.tick())

    clock.advance(1.5)
    s2 = _restart(db, srcs, clock, intervals=intervals)
    assert s2.tick() == ["explore"]
    _finish_all(s2, ["explore"])


def test_a_set_older_than_twice_its_interval_is_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "s.db"
    clock = _Clock()
    srcs: list[Source] = [
        _Src("explore", _cams("explore", 2)),
        _Src("skyline", _cams("skyline", 2)),
    ]
    intervals = {"explore": 1, "skyline": 24}
    s = _restart(db, srcs, clock, intervals=intervals)
    _finish_all(s, s.tick())

    clock.advance(3)  # explore 3x its interval old, skyline well inside its own
    merges: list[list[CatalogueEntry]] = []
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        s2 = _restart(db, srcs, clock, merges=merges, intervals=intervals)
    assert {e.source for e in merges[-1]} == {"skyline"}
    assert s2.history["explore"].last_refresh_at is None  # starts as new
    assert "loaded skyline; over age, so starting as new: explore" in caplog.text
    assert s2.tick() == ["explore"]
    _finish_all(s2, ["explore"])


def test_a_restart_mid_outage_still_needs_two_bad_refreshes(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    clock = _Clock()
    src = _Src("skyline", _cams("skyline", 10))
    s = _restart(db, [src], clock)
    _finish_all(s, s.tick())

    src._c = _cams("skyline", 2)  # pyright: ignore[reportPrivateUsage]
    for bad in range(AGREE_TO_ACCEPT):
        clock.advance(6)
        merges: list[list[CatalogueEntry]] = []
        s = _restart(db, [src], clock, merges=merges)
        assert len(merges[-1]) == 10  # the guard's kept set, served at startup
        _finish_all(s, s.tick())
        held = bad + 1 < AGREE_TO_ACCEPT
        assert len(merges[-1]) == (10 if held else 2)


def test_rows_for_a_source_the_code_no_longer_has_are_ignored(
    tmp_path: Path,
) -> None:
    db = tmp_path / "s.db"
    clock = _Clock()
    s = _restart(db, [_Src("gone", _cams("gone", 2))], clock)
    _finish_all(s, s.tick())

    merges: list[list[CatalogueEntry]] = []
    s2 = _restart(db, [_Src("explore", _cams("explore", 1))], clock, merges=merges)
    assert "gone" not in s2.history
    assert merges == []  # nothing of explore's stored, so nothing to serve yet
    _finish_all(s2, s2.tick())
    assert {e.source for e in merges[-1]} == {"explore"}


def test_health_reads_the_same_either_side_of_a_restart(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "s.db"
    clock = _Clock(1_000_000.7)
    srcs: list[Source] = [_Src("explore", _cams("explore", 2)), _Src("feratel", [])]
    s = _restart(db, srcs, clock)
    _finish_all(s, s.tick())
    before = source_status_for(s, clock())

    clock.advance(1)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        s2 = _restart(db, srcs, clock)
        assert source_status_for(s2, clock()) == source_status_for(s, clock())
        assert source_status_for(s2, clock() - 3600) == before
        assert s2.history["feratel"].status == "dead"
        clock.advance(6)
        _finish_all(s2, s2.tick())
    # The outage reads on as one: no fresh "-> dead" transition, only "still dead".
    assert "-> dead" not in caplog.text
    assert "feratel: still dead" in caplog.text


class _CrashesAfterFirst:
    name: str = "feratel"
    interval_hours: float = 6
    _calls: int = 0

    def discover(self) -> list[Candidate]:
        self._calls += 1
        if self._calls > 1:
            msg = "site down"
            raise RuntimeError(msg)
        return _cams("feratel", 1)


def test_a_restart_restores_every_field_it_saved(tmp_path: Path) -> None:
    """Each column round-trips: angle_key feeds the stable id players hold."""
    db = tmp_path / "s.db"
    clock = _Clock()
    page = "https://camscape/harbour"
    cams = [
        Candidate(
            "North", "north", "Beaches", "camscape", page, "https://c/n", "k:n", 1
        ),
        Candidate(
            "South", "south", "Animals", "camscape", page, "https://c/s", "k:s", 2
        ),
        Candidate("Gone", None, None, "camscape", page, "https://c/dead", None),
    ]
    srcs: list[Source] = [_Src("camscape", cams), _CrashesAfterFirst()]

    def drop_dead(c: Candidate) -> str | None:
        return "dead-manifest" if c.target_url.endswith("dead") else None

    first: list[list[CatalogueEntry]] = []
    s = _scheduler(
        srcs, drop_reason_for=drop_dead, clock=clock, merges=first, db=store.Store(db)
    )
    _finish_all(s, s.tick())
    clock.advance(6)
    _finish_all(s, s.tick())  # feratel crashes, keeping its kept set
    assert s.history["feratel"].last_crashed

    after: list[list[CatalogueEntry]] = []
    s2 = _scheduler(srcs, clock=clock, merges=after, db=store.Store(db))
    assert s2.history == s.history
    assert after == [first[-1]]


def test_a_set_stamped_in_the_future_is_dropped(tmp_path: Path) -> None:
    """A refresh saved while the clock ran ahead would otherwise not be due for days."""
    db = tmp_path / "s.db"
    clock = _Clock()
    srcs: list[Source] = [_Src("explore", _cams("explore", 1))]
    s = _restart(db, srcs, clock)
    _finish_all(s, s.tick())

    clock.advance(-48)  # the clock was corrected back
    s2 = _restart(db, srcs, clock)
    assert s2.history["explore"].last_refresh_at is None
    assert s2.tick() == ["explore"]
    _finish_all(s2, ["explore"])


def test_a_save_that_raises_still_leaves_the_refresh_merged(tmp_path: Path) -> None:
    # A lone surrogate (a JSON feed's "\ud800") can't be encoded for SQLite.
    cam = _make_candidate(source="explore", title="cam \ud800")
    merges: list[list[CatalogueEntry]] = []
    s = _scheduler(
        [_Src("explore", [cam])], merges=merges, db=store.Store(tmp_path / "s.db")
    )
    started = s.tick()
    with pytest.raises(UnicodeEncodeError):
        _finish_all(s, started)
    assert len(merges[-1]) == 1


def test_a_first_start_says_the_store_held_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        _scheduler([_Src("explore", [])])
    assert "store: loaded none; over age, so starting as new: none" in caplog.text


def test_a_store_that_cannot_save_neither_stops_the_merge_nor_the_retries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Playback reads only the merged catalogue, so a failing save can't touch it."""
    db = store.Store(tmp_path / "s.db")
    # A full disk: the file can't grow.
    assert db.write("cap", lambda tx: tx.execute("PRAGMA max_page_count = 1"))
    cams = [
        _make_candidate(
            source="explore",
            key="hls:big",
            target="https://explore/big.m3u8",
            title="cam " + "x" * 100_000,
        )
    ]
    clock = _Clock()
    merges: list[list[CatalogueEntry]] = []
    s = _scheduler([_Src("explore", cams)], db=db, clock=clock, merges=merges)
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        _finish_all(s, s.tick())
    assert len(merges[-1]) == 1
    assert db.status == "failing"
    assert "explore's refresh not saved" in caplog.text

    uncap = "PRAGMA max_page_count = 1073741823"
    assert db.write("uncap", lambda tx: tx.execute(uncap))
    # Failing again, so the refresh's own save is what heals it.
    db.write("fail", lambda tx: tx.execute("INSERT INTO no_such_table VALUES (1)"))
    clock.advance(6)
    _finish_all(s, s.tick())
    assert db.status == "ok"
    assert db.read("SELECT count(*) FROM kept_cam") == [(1,)]
    db.close()
