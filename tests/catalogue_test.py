from __future__ import annotations

from collections.abc import Iterable

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


def _always_alive(_c: Candidate) -> bool:
    return True


def _never_alive(_c: Candidate) -> bool:
    return False


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

    def is_alive(c: Candidate) -> bool:
        return c.predisc_key == "hls:alive"

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


def test_source_liveness_failure_is_isolated_and_reuses_history() -> None:
    """Sources run concurrently: one whose liveness probe raises is treated like a crash
    (reuse its last good set) and must NOT sink the other sources' results."""
    boom = _make_candidate(
        source="worldcams", key="hls:boom", target="https://example.com/boom.m3u8"
    )
    good = _make_candidate(
        source="cxtvlive", key="hls:ok", target="https://example.com/ok.m3u8"
    )

    def _selective_alive(c: Candidate) -> bool:
        if "boom" in c.target_url:
            raise RuntimeError("liveness boom")
        return True

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
