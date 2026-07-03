from webcam_aggregator.dedup import dedupe
from webcam_aggregator.models import Candidate


def _c(
    source: str,
    key: str | None = None,
    page: str = "p",
    cat: str | None = None,
    title: str = "t",
    angle: str | None = None,
) -> Candidate:
    return Candidate(
        title=title,
        angle_key=angle,
        category=cat,
        source=source,
        source_page_url=page,
        target_url="u",
        predisc_key=key,
    )


def test_cross_source_yt_dup_merges_with_best_fields():
    out = dedupe(
        [
            _c("youtube-api", "yt:AAA", cat=None, title="Official Title"),
            _c("cxtvlive", "yt:AAA", page="p2", cat="Beaches", title="slug-name"),
        ]
    )
    assert len(out) == 1
    assert out[0].category == "Beaches"  # scraper category wins
    assert out[0].title == "Official Title"  # youtube title wins for a yt cam


def test_channel_cams_never_merged():
    out = dedupe(
        [
            _c("worldcams", None, page="czech/usti"),
            _c("worldcams", None, page="czech/decin"),
        ]
    )
    assert len(out) == 2


def test_multiangle_distinct_keys_kept():
    out = dedupe(
        [
            _c("worldcams", "yt:AAA", page="p", angle="0"),
            _c("worldcams", "yt:BBB", page="p", angle="1"),
        ]
    )
    assert len(out) == 2


def test_category_precedence_cxtvlive_over_worldcams():
    out = dedupe(
        [_c("worldcams", "hls:x", cat="Cities"), _c("cxtvlive", "hls:x", cat="Beaches")]
    )
    assert len(out) == 1
    assert out[0].category == "Beaches"


def test_non_yt_title_uses_longest():
    out = dedupe(
        [
            _c("worldcams", "hls:x", title="short"),
            _c("cxtvlive", "hls:x", title="a much longer title"),
        ]
    )
    assert out[0].title == "a much longer title"
