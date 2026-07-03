import pytest

from webcam_aggregator.categories import (
    category_from_title,
    map_category,
    unknown_categories,
)


def test_known_mappings():
    assert map_category("Birds") == "Animals"
    assert map_category("Pets & Animals") == "Animals"
    assert map_category("Ski Resorts") == "Mountains"
    assert map_category("Railway Stations") == "Trains & Railways"
    assert map_category("Vatican") == "Religion"
    assert map_category("Weather") == "Weather"
    assert map_category("Sports Live") == "Sports"
    assert map_category("Watch Soccer Live") == "Sports"


def test_source_other_and_quality_tags_map_to_other_not_unmapped():
    # a source's literal "Other" (and non-content tags) -> our "Other", NOT "Unmapped"
    assert map_category("Other") == "Other"
    assert map_category("High Definition Hd") == "Other"


def test_native_youtube_kept():
    assert map_category("Entertainment") == "Entertainment"
    assert map_category("Travel & Events") == "Travel & Events"


def test_empty_falls_back_to_other():
    assert map_category(None) == "Other"
    assert map_category("") == "Other"


def test_unknown_category_is_flagged_not_buried_in_other():
    # a source that DID give a category we don't recognise -> distinct "Unmapped
    # Category" (visible + logged), NOT silently "Other"
    assert map_category("Something Random") == "Unmapped Category"


def test_unmapped_category_logged_once(caplog: pytest.LogCaptureFixture):
    import logging

    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.categories"):
        assert map_category("Zqx Unmapped Probe") == "Unmapped Category"
        assert map_category("Zqx Unmapped Probe") == "Unmapped Category"  # 2nd: no log
    hits = [r for r in caplog.records if "Zqx Unmapped Probe" in r.getMessage()]
    assert len(hits) == 1


def test_all_categories_set():
    from webcam_aggregator.categories import ALL_CATEGORIES

    assert "Animals" in ALL_CATEGORIES  # unified
    assert "Trains & Railways" in ALL_CATEGORIES
    assert "Travel & Events" in ALL_CATEGORIES  # native YouTube, passes through
    assert "Other" in ALL_CATEGORIES  # fallback (source gave no category)
    assert "Unmapped Category" in ALL_CATEGORIES  # source gave one we don't map
    assert list(ALL_CATEGORIES) == sorted(ALL_CATEGORIES)  # stable, sorted order


def test_unknown_categories_returns_empty_for_valid():
    assert unknown_categories(frozenset({"animals", "religion"})) == frozenset()


def test_unknown_categories_returns_typos():
    assert unknown_categories(frozenset({"relgion", "animals"})) == frozenset(
        {"relgion"}
    )


def test_category_from_title_keywords():
    assert category_from_title("Brown Bear Cam - Brooks Falls") == "Animals"
    assert category_from_title("Brixham Harbour") == "Ports & Ships"
    assert category_from_title("Mount Buller Ski Area") == "Mountains"
    assert category_from_title("Pinamar Beach") == "Beaches"
    assert category_from_title("Niagara Falls") == "Water & Waterways"
    assert category_from_title("St. Paul's Cathedral") == "Religion"
    assert category_from_title("Times Square Skyline") == "Cities"
    assert category_from_title("Aurora Borealis Live") == "Weather"


def test_category_from_title_first_match_wins():
    # specific beats generic: "harbour" (Ports, earlier rule) over "beach" (later)
    assert category_from_title("Harbour Beach") == "Ports & Ships"
    # a species beats a generic "street"
    assert category_from_title("Eagle Street") == "Animals"


def test_category_from_title_geo_default_to_travel():
    # a named place + geo but no content word -> place view -> Travel & Events
    assert category_from_title("Suzu — Ishikawa, Japan") == "Travel & Events"
    assert (
        category_from_title("Kensington Cam 6 Philadelphia, PA.") == "Travel & Events"
    )
    assert category_from_title("Vlora - Albania") == "Travel & Events"


def test_category_from_title_keyword_uses_name_not_geo():
    # a category word in the " — geo" suffix must NOT win; only the name counts
    assert category_from_title("Old Mill — Lake District, England") == "Travel & Events"


def test_category_from_title_no_signal_is_none():
    # a bare word with no keyword and no geo stays Other (None)
    assert category_from_title("Bude") is None
    assert category_from_title("Channel Cam") is None


def test_title_rule_categories_are_all_valid():
    # every category the title fallback can emit must be a real, excludable category
    from webcam_aggregator.categories import (
        ALL_CATEGORIES,
        TITLE_FALLBACK_CATEGORIES,
    )

    invalid = TITLE_FALLBACK_CATEGORIES - set(ALL_CATEGORIES)
    assert not invalid, f"title rules emit non-categories: {invalid}"


def test_readme_documents_every_category():
    """Drift guard: every excludable category must be listed in the README, so users
    always know what they can pass to EXCLUDE_CATEGORIES."""
    from pathlib import Path

    from webcam_aggregator.categories import ALL_CATEGORIES

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text("utf-8")
    missing = [c for c in ALL_CATEGORIES if c not in readme]
    assert not missing, f"categories missing from README: {missing}"
