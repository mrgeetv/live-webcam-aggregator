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


def test_category_from_title_non_english_keywords():
    # our European sources title their cams in the local language, so the keyword
    # rules carry the common content words alongside the English ones
    assert category_from_title("Hafen Greetsiel") == "Ports & Ships"
    assert category_from_title("Live vom Strand von Borkum") == "Beaches"
    assert category_from_title("Göstling - Hochkarbahn Bergstation") == "Mountains"
    assert category_from_title("Wyciąg narciarski w Bytomiu") == "Mountains"
    assert category_from_title("Mielno - Jezioro Jamno") == "Water & Waterways"
    assert category_from_title("Worms - Innenstadt") == "Cities"
    assert category_from_title("ŻYWIEC - widok na Rynek") == "Cities"
    # plaża/plaža is a beach, but a bare "plaza" is a town square -> Cities
    assert category_from_title("Dźwirzyno - plaża wschodnia") == "Beaches"
    assert category_from_title("Dongdaemun Design Plaza") == "Cities"


def test_category_from_title_japanese_keywords():
    # wholly-Japanese sources (tomarigi, shareju) leave many cams uncategorised, and a
    # \b-anchored English rule can never fire on unspaced Japanese
    assert category_from_title("【神田川】柏橋映像監視局［新宿区北新宿3-36］") == (
        "Water & Waterways"
    )
    assert category_from_title("大分市水害監視カメラNo.31") == "Water & Waterways"
    assert category_from_title("ユメックス 国道17号バイパス代交差点付近") == "Traffic"
    assert category_from_title("道路カメラ_春日北交差点") == "Traffic"
    assert category_from_title("ＪＲ博多駅前　Hakata Station") == "Trains & Railways"
    assert category_from_title("京都 伏見稲荷大社付近 裏参道") == "Religion"
    assert category_from_title("お天気・ライブ情報カメラ 北海道") == "Weather"
    assert category_from_title("横須賀市災害監視カメラ　野比海岸") == "Beaches"


def test_japanese_keywords_avoid_place_name_false_positives():
    # 川 is the trap: these are PLACES whose name merely contains it, and each is
    # really something else (a road cam, a station, a plain city view)
    assert category_from_title("旭川市国道12号線 忠和ライブカメラ") == "Traffic"
    assert category_from_title("神奈川県横浜市の眺め") != "Water & Waterways"
    assert category_from_title("川崎の街並み") != "Water & Waterways"
    # 道の駅 is a roadside rest stop, not a railway station
    assert category_from_title("道の駅うつのみや ライブカメラ") == "Traffic"


def test_compound_and_non_english_words_the_english_rules_missed():
    # \bgolf\b cannot match the compound spelling these languages use
    assert category_from_title("Varbergs Golfklubb - Västra Banan") == "Sports"
    assert category_from_title("Golfclub Donau-Riss e.V.") == "Sports"
    # but a bare "golf" prefix must not swallow the Italian/Spanish word for gulf
    assert category_from_title("Rapallo, Golfo di Tigullio") != "Sports"
    # bridge, in the languages our sources use
    assert category_from_title("Puente Internacional Córdova") == "Landmarks"
    assert category_from_title("Süd-Dakota-Brücke") == "Landmarks"


def test_education_and_hotels_titles():
    # both categories exist in the taxonomy but had no title rule, so these cams
    # could never leave "Other"
    assert category_from_title("University of Nevada, Reno - Quadrangle") == "Education"
    assert category_from_title("Lourdes Hill College") == "Education"
    assert category_from_title("Jostedal hotell, Norway") == "Hotels"
    # a ski "resort" is a mountain cam, not a hotel one (Mountains rule comes first)
    assert category_from_title("Base Cam at Stratton Mountain Resort") == "Mountains"


def test_non_english_keywords_avoid_english_false_positives():
    # "the strand" is a London street, not a beach; the German beach word takes no article
    assert category_from_title("The Strand — London, England") != "Beaches"
    # "montagn" must be a whole word (montagna/montagne), not a place-name fragment
    assert category_from_title("Montagnana Town Square") == "Cities"
    assert category_from_title("Montagnac Village") != "Mountains"
    assert category_from_title("Nice Montagne — France") == "Mountains"


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


def test_every_youtube_category_maps_to_a_real_group() -> None:
    """youtube_api._YT_CATEGORIES and categories._MAP/_NATIVE_YT are two tables that
    have to agree. When they drifted, Gaming / Film & Animation / Howto & Style /
    Comedy all fell into the single "Unmapped Category" bucket, so a user could only
    exclude all four together (or none). Every YouTube category must land on a real,
    individually-excludable group."""
    from webcam_aggregator.categories import ALL_CATEGORIES, UNMAPPED, map_category
    from webcam_aggregator.sources.youtube_api import (
        _YT_CATEGORIES,  # pyright: ignore[reportPrivateUsage]
    )

    for yt_name in _YT_CATEGORIES.values():
        mapped = map_category(yt_name)
        assert mapped != UNMAPPED, f"YouTube category {yt_name!r} is unmapped"
        assert (
            mapped in ALL_CATEGORIES
        ), f"{yt_name!r} -> {mapped!r} not a real category"


def test_gaming_is_individually_excludable() -> None:
    """The specific thing that prompted this: live gaming streams sneak in via the
    YouTube search and must be droppable without taking cartoons and comedy with them.
    """
    from webcam_aggregator.categories import map_category, unknown_categories

    assert map_category("Gaming") == "Gaming"
    assert unknown_categories(frozenset({"gaming"})) == frozenset()
