from webcam_aggregator.dedup import dedupe
from webcam_aggregator.models import Candidate


def _c(
    source: str,
    key: str | None = None,
    *,
    page: str = "p",
    cat: str | None = None,
    title: str = "t",
    angle: str | None = None,
    target: str = "u",
    name_rank: int | None = None,
) -> Candidate:
    return Candidate(
        title=title,
        angle_key=angle,
        category=cat,
        source=source,
        source_page_url=page,
        target_url=target,
        predisc_key=key,
        name_rank=name_rank,
    )


def test_cross_source_yt_dup_merges_with_best_fields() -> None:
    out = dedupe(
        [
            _c("youtube-api", "yt:AAA", cat=None, title="Official Title"),
            _c("cxtvlive", "yt:AAA", page="p2", cat="Beaches", title="slug-name"),
        ]
    )
    assert len(out) == 1
    assert out[0].category == "Beaches"  # scraper category wins
    assert out[0].title == "Official Title"  # youtube title wins for a yt cam


def test_unkeyed_cams_merge_only_on_the_identical_target() -> None:
    """With no merge key, the exact target is the identity: one URL, one stream."""
    out = dedupe(
        [
            _c("airportwebcams", None, page="ajaccio", target="https://yt/@corse/live"),
            _c("airportwebcams", None, page="bastia", target="https://yt/@bastia/live"),
            _c(
                "airportwebcams",
                None,
                page="calvi",
                title="Calvi airport",
                target="https://yt/@corse/live",
            ),
        ]
    )
    assert len(out) == 2
    [corse] = [c for c in out if c.target_url == "https://yt/@corse/live"]
    assert corse.source_page_url == "ajaccio"  # tie on rank: the first copy is the base
    assert corse.title == "Calvi airport"  # the longest title, as for keyed cams


def test_a_repeat_within_one_source_keeps_its_category() -> None:
    """A source listing one stream on two pages merges like two sources would.

    The copy that has a category is the base, so its page fixes the stable id.
    """
    out = dedupe(
        [
            _c("webcamerapl", "hls:x", page="alias", cat=None),
            _c("webcamerapl", "hls:x", page="listed", cat="Beaches"),
        ]
    )
    assert len(out) == 1
    assert out[0].category == "Beaches"
    assert out[0].source_page_url == "listed"


def test_multiangle_distinct_keys_kept() -> None:
    out = dedupe(
        [
            _c("worldcams", "yt:AAA", page="p", angle="0"),
            _c("worldcams", "yt:BBB", page="p", angle="1"),
        ]
    )
    assert len(out) == 2


def test_category_precedence_cxtvlive_over_worldcams() -> None:
    out = dedupe(
        [_c("worldcams", "hls:x", cat="Cities"), _c("cxtvlive", "hls:x", cat="Beaches")]
    )
    assert len(out) == 1
    assert out[0].category == "Beaches"


def test_non_yt_title_uses_longest() -> None:
    out = dedupe(
        [
            _c("worldcams", "hls:x", title="short"),
            _c("cxtvlive", "hls:x", title="a much longer title"),
        ]
    )
    assert out[0].title == "a much longer title"


def test_a_category_we_can_map_beats_one_we_cannot() -> None:
    """An unrecognised slug only lands a cam in "Unmapped Category": take a real one."""
    within = dedupe(
        [
            _c("viewsurf", "hls:x", page="surf", cat="surf-unknown-slug"),
            _c("viewsurf", "hls:x", page="plage", cat="Beaches"),
        ]
    )
    # within one source the mapped copy is also the base, so its page fixes the id
    assert (within[0].category, within[0].source_page_url) == ("Beaches", "plage")
    across = dedupe(
        [
            _c("cxtvlive", "hls:x", cat="surf-unknown-slug"),
            _c("worldcams", "hls:x", cat="Cities"),
        ]
    )
    assert across[0].category == "Cities"  # even over a higher-ranked source


def test_name_rank_picks_the_title_and_the_base_copy() -> None:
    """A source that ranks where it found each copy names the cam from the best one."""
    out = dedupe(
        [
            _c(
                "wt", "yt:A", page="blog", title="a much longer blog title", name_rank=2
            ),
            _c("wt", "yt:A", page="cam-page", title="Barn owls", name_rank=0),
            _c("wt", "yt:A", page="index", title="Owl card", name_rank=1),
        ]
    )
    assert (out[0].title, out[0].source_page_url) == ("Barn owls", "cam-page")


def test_name_rank_counts_only_between_ranked_copies() -> None:
    """A copy with no rank has no say on it: the usual field rules decide."""
    out = dedupe(
        [
            _c("wt", "yt:A", page="cam-page", title="Barn owls", name_rank=0),
            _c("other", "yt:A", page="elsewhere", title="a much longer title"),
        ]
    )
    assert out[0].title == "a much longer title"
