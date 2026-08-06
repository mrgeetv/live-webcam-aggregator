from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

import pytest

from webcam_aggregator.catalogue import (
    AGREE_TO_ACCEPT,
    Hist,
    build_catalogue,
)
from webcam_aggregator.models import Candidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
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


def _all_yt_live(ids: Iterable[str]) -> dict[str, str]:
    return {i: "" for i in ids}


# ---------------------------------------------------------------------------
# Test 1: Cross-source dedup (the fix)
# ---------------------------------------------------------------------------


def test_cross_source_dedup_collapses_same_predisc_key() -> None:
    """Two sources that each yield yt:AAA must collapse to ONE entry.

    dedup() runs ONCE after all sources are processed, so a cam found by
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

    result = build_catalogue(
        [src_yt, src_scraper],
        is_alive=_always_alive,
        youtube_live=lambda _ids: {"AAA": ""},
        history={},
    )

    # The fix: cross-source dedup means these collapse to ONE entry
    assert (
        len(result) == 1
    ), f"Expected 1 entry after cross-source dedup, got {len(result)}: {result}"

    entry = result[0]
    # Scraper (cxtvlive) wins on category per _CAT_RANK
    assert entry.category == "Beaches"
    # youtube-api provides the canonical title
    assert entry.title == "Official"


# ---------------------------------------------------------------------------
# Test 2: Multi-source build — entries deduped, category-mapped, non-empty id
# ---------------------------------------------------------------------------


def test_multisource_deduped_category_mapped_id_non_empty() -> None:
    """Two sources with distinct keys produce two entries; Birds→Animals; id non-empty."""
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

    result = build_catalogue(
        [src1, src2],
        is_alive=_always_alive,
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
    assert birds_entry.id != "" and len(birds_entry.id) == 16
    assert city_entry.id != "" and len(city_entry.id) == 16


# ---------------------------------------------------------------------------
# Test 3: Dead dropped
# ---------------------------------------------------------------------------


def test_non_yt_dead_candidate_excluded() -> None:
    """A non-yt candidate with is_alive=False is excluded."""
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

    def is_alive(c: Candidate) -> str | None:
        return None if c.predisc_key == "hls:alive" else "dead-manifest"

    src = _Src("worldcams", [alive_cand, dead_cand])
    result = build_catalogue(
        [src], is_alive=is_alive, youtube_live=_no_yt_live, history={}
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

    result = build_catalogue(
        [src], is_alive=_never_alive, youtube_live=youtube_live, history={}
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
    result = build_catalogue(
        [_Src("youtube-api", [cam])],
        is_alive=_always_alive,
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
    """Prior last_kept of 10; build returning 2 kept (>50% drop) reuses old 10."""
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, last_kept=old_kept)
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

    result = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
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
            last_kept=old_kept,
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

    result = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )

    # New small set accepted — 2 entries
    assert len(result) == 2
    assert all(e.title.startswith("New Cam") for e in result)


def test_empty_guard_no_history_promotes_unconditionally() -> None:
    """First build (no history) always promotes, regardless of count."""
    cand = _make_candidate(key="hls:only1", title="Only Cam")
    src = _Src("worldcams", [cand])

    result = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history={}
    )

    assert len(result) == 1
    assert result[0].title == "Only Cam"


def test_empty_guard_streak_increments_then_resets() -> None:
    """Two consecutive shrinks: first reuses old set; second (AGREE_TO_ACCEPT=2) accepts."""
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, last_kept=old_kept)
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
    first = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(first) == 10
    assert history["worldcams"].shrink_streak == 1
    assert history["worldcams"].last_count == 10  # not updated yet

    # Second guarded build → streak reaches AGREE_TO_ACCEPT (2), so new baseline accepted
    second = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(second) == 2
    assert history["worldcams"].shrink_streak == 0  # reset after acceptance
    assert history["worldcams"].last_count == 2  # updated to new small count


def test_crash_reuses_last_good_set_and_never_wipes() -> None:
    """A source whose discover() raises reuses its last good set across repeated
    crashes — two consecutive crashes must NOT be accepted as an empty set."""
    cam = _make_candidate(
        source="worldcams",
        key="hls:a",
        page="https://example.com/a",
        target="https://example.com/a.m3u8",
    )

    class _CrashSrc:
        name: str = "worldcams"
        calls: int

        def __init__(self) -> None:
            self.calls = 0

        def discover(self) -> list[Candidate]:
            self.calls += 1
            if self.calls == 1:
                return [cam]
            raise RuntimeError("boom")

    src = _CrashSrc()
    history: dict[str, Hist] = {}
    first = build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert len(first) == 1
    for _ in range(2):  # the old bug wiped last_kept on the 2nd consecutive crash
        result = build_catalogue(
            [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
        )
        assert len(result) == 1


def test_health_fields_record_raw_outcome_under_guard() -> None:
    """The per-cycle raw fields (/health monitoring) record the actual failure even
    while the empty-guard masks it: on a collapse, last_raw_kept is the shrunk count
    (the raw result), not the larger count the guard keeps serving."""
    old_kept = _make_old_candidates(10)
    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=10, shrink_streak=0, last_kept=old_kept)
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
    build_catalogue(
        [_Src("worldcams", small)],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    h = history["worldcams"]
    assert h.last_raw_kept == 2  # RAW: what this cycle actually produced
    assert h.last_crashed is False
    assert h.last_discovered == 2

    # Healthy cycle (10 candidates, no collapse): raw reflects the full set.
    ten = _make_old_candidates(10)
    build_catalogue(
        [_Src("worldcams", ten)],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    h = history["worldcams"]
    assert h.last_raw_kept == 10
    assert h.last_crashed is False


def test_health_fields_flag_crash() -> None:
    """A crashed discover records last_crashed=True and last_raw_kept=0, even though
    the empty-guard keeps serving the last-good set."""
    cam = _make_candidate(
        source="worldcams", key="hls:a", target="https://example.com/a.m3u8"
    )

    class _CrashSrc:
        name: str = "worldcams"
        calls: int = 0

        def discover(self) -> list[Candidate]:
            self.calls += 1
            if self.calls == 1:
                return [cam]
            raise RuntimeError("boom")

    src = _CrashSrc()
    history: dict[str, Hist] = {}
    build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    assert history["worldcams"].last_crashed is False  # first cycle succeeded

    build_catalogue(
        [src], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    h = history["worldcams"]
    assert h.last_crashed is True
    assert h.last_raw_kept == 0


def test_source_liveness_failure_is_isolated_and_reuses_history() -> None:
    """Sources run concurrently: one whose liveness probe raises is treated like a crash
    (reuse its last good set) and must NOT sink the other sources' results."""
    boom = _make_candidate(
        source="worldcams", key="hls:boom", target="https://example.com/boom.m3u8"
    )
    good = _make_candidate(
        source="cxtvlive", key="hls:ok", target="https://example.com/ok.m3u8"
    )

    def _selective_alive(c: Candidate) -> str | None:
        if "boom" in c.target_url:
            raise RuntimeError("liveness boom")
        return None

    history: dict[str, Hist] = {
        "worldcams": Hist(last_count=1, shrink_streak=0, last_kept=[boom])
    }
    entries = build_catalogue(
        [_Src("worldcams", [boom]), _Src("cxtvlive", [good])],
        is_alive=_selective_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    srcs = {e.source for e in entries}
    assert "cxtvlive" in srcs  # healthy source unaffected by the other's liveness crash
    assert "worldcams" in srcs  # failed source reused its last good set
    assert history["worldcams"].last_kept == [boom]  # history not wiped


def test_exclude_categories_drops_matching_entries() -> None:
    """Entries whose mapped category is excluded are dropped (case-insensitive, matched
    on the unified category — Birds maps to Animals, so excluding 'animals' drops it).
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
    result = build_catalogue(
        [_Src("worldcams", [beach, bird])],
        is_alive=_always_alive,
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
    result = build_catalogue(
        [_Src("worldcams", [bear, odd])],
        is_alive=_always_alive,
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
    result = build_catalogue(
        [_Src("worldcams", [real, unmapped])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history={},
    )
    cats = {e.title: e.category for e in result}
    assert cats["Times Square"] == "Animals"
    assert cats["Bear Beach"] == "Unmapped Category"


# ---------------------------------------------------------------------------
# Per-source fetch outcome — an empty source must say WHY (2026-08 incident)
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
    """Skyline's real shape on 2026-08-04: 11 fetches, all 403, nothing discovered."""
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("skyline", [])],
            is_alive=_always_alive,
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
    """A Cloudflare challenge page is an HTTP 200 — a failure counter sees nothing
    wrong. This is the case the whole per-host-failure approach could not detect."""
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("skyline", [])],
            is_alive=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats({"skyline": {"www.skylinewebcams.com": {"ok": 11}}}),
        )
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "11 fetched ok but extracted 0 URLs" in caplog.text


def test_healthy_source_logs_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("explore", [_make_candidate(key="hls:x")])],
            is_alive=_always_alive,
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
        build_catalogue(
            [_Src("skyline", [_make_candidate(key="hls:x")])],
            is_alive=_always_alive,
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
    """youtube-api uses googleapiclient, not Fetcher — no stats, and it must not be
    described as "0 fetched" as though the network were the problem."""
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("youtube-api", [])],
            is_alive=_always_alive,
            youtube_live=_no_yt_live,
            history={},
            fetch_stats=_stats({}),
        )
    assert "youtube-api: 0 kept / 0 discovered" in caplog.text
    assert "fetched" not in caplog.text


def test_hist_records_fetches() -> None:
    history: dict[str, Hist] = {}
    build_catalogue(
        [_Src("skyline", [])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
        fetch_stats=_stats({"skyline": {"www.skylinewebcams.com": {"http-403": 11}}}),
    )
    assert history["skyline"].last_fetches == {
        "www.skylinewebcams.com": {"http-403": 11}
    }


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
        build_catalogue(
            [_Src("skyline", cands)],
            is_alive=_drops("dead-manifest"),
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
        build_catalogue(
            [_Src("camscape", cands)],
            is_alive=_drops("no-extractor"),
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
        build_catalogue(
            [_Src("worldcams", cands)],
            is_alive=_drops("resolve-failed:yt-dlp failed: Sign in to confirm"),
            youtube_live=_no_yt_live,
            history={},
        )
    assert "dropped 2 (2x resolve-failed)" in caplog.text
    assert "top resolve failures" in caplog.text
    assert "Sign in to confirm" in caplog.text


def test_clean_source_logs_no_drop_suffix(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("explore", [_make_candidate(key="hls:x")])],
            is_alive=_always_alive,
            youtube_live=_no_yt_live,
            history={},
        )
    assert "explore: 1 kept / 1 discovered" in caplog.text
    assert "dropped" not in caplog.text


def test_yt_offline_is_counted_as_a_reason() -> None:
    """A YouTube API outage spikes this across every source at once."""
    history: dict[str, Hist] = {}
    cam = _make_candidate(
        source="youtube-api",
        key="yt:DEAD001",
        target="https://www.youtube.com/watch?v=DEAD001",
    )
    build_catalogue(
        [_Src("youtube-api", [cam])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    assert history["youtube-api"].drop_reasons == {"yt-offline": 1}


def test_hist_records_drop_reasons_and_hosts() -> None:
    history: dict[str, Hist] = {}
    build_catalogue(
        [_Src("camscape", [_make_candidate(key="hls:a", target="https://rtsp.me/a")])],
        is_alive=_drops("no-extractor"),
        youtube_live=_no_yt_live,
        history=history,
    )
    assert history["camscape"].drop_reasons == {"no-extractor": 1}
    assert history["camscape"].no_extractor_hosts == {"rtsp.me": 1}


# ---------------------------------------------------------------------------
# Source status — the empty-guard used to fall silent (2026-08-05: a full day
# of zero-cam rebuilds logged nothing at all)
# ---------------------------------------------------------------------------


def test_zero_source_warns_every_single_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    history: dict[str, Hist] = {}
    good = _Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(10)])
    build_catalogue(
        [good], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
    )
    dead = _Src("skyline", [])
    for cycle in range(AGREE_TO_ACCEPT + 3):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
            build_catalogue(
                [dead],
                is_alive=_always_alive,
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
        build_catalogue(
            [dead], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
        )
    assert "-> dead" in caplog.text
    assert history["skyline"].status == "dead"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [dead], is_alive=_always_alive, youtube_live=_no_yt_live, history=history
        )
    assert "-> dead" not in caplog.text  # unchanged status: no repeat transition
    assert "still dead" in caplog.text  # but it still says something


def test_recovery_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    history: dict[str, Hist] = {}
    build_catalogue(
        [_Src("skyline", [])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [_Src("skyline", [_make_candidate(key="hls:back")])],
            is_alive=_always_alive,
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
    build_catalogue(
        [dead],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
        fetch_stats=stats,
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.catalogue"):
        build_catalogue(
            [dead],
            is_alive=_always_alive,
            youtube_live=_no_yt_live,
            history=history,
            fetch_stats=stats,
        )
    assert "11 fetched, 0 ok" in caplog.text
    assert "still dead" not in caplog.text


def test_degraded_status_is_tracked() -> None:
    history: dict[str, Hist] = {}
    build_catalogue(
        [_Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(10)])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    build_catalogue(
        [_Src("skyline", [_make_candidate(key=f"hls:{i}") for i in range(2)])],
        is_alive=_always_alive,
        youtube_live=_no_yt_live,
        history=history,
    )
    assert history["skyline"].status == "degraded"
