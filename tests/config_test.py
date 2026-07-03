import logging

import pytest

from webcam_aggregator import config


def test_requires_api_key():
    with pytest.raises(ValueError):
        config.load({})


def test_defaults_and_strip_trailing_slash():
    c = config.load(
        {"YOUTUBE_API_KEY": "k", "PUBLIC_BASE_URL": "https://cams.example/"}
    )
    assert c.public_base_url == "https://cams.example"
    assert c.catalogue_interval_hours == 6


def test_interval_floor_and_bad_value():
    floored = config.load({"YOUTUBE_API_KEY": "k", "CATALOGUE_INTERVAL_HOURS": "0"})
    assert floored.catalogue_interval_hours == 1
    bad = config.load({"YOUTUBE_API_KEY": "k", "CATALOGUE_INTERVAL_HOURS": "x"})
    assert bad.catalogue_interval_hours == 6


def test_new_field_defaults():
    c = config.load({"YOUTUBE_API_KEY": "k"})
    assert c.search_query  # non-empty built-in default
    assert c.log_level == "INFO"
    assert c.proxy_youtube is False
    assert c.max_parallel_sources == 4


def test_new_fields_from_env():
    c = config.load(
        {
            "YOUTUBE_API_KEY": "k",
            "SEARCH_QUERY": "trains|railway",
            "LOG_LEVEL": "debug",
            "PROXY_YOUTUBE": "true",
            "MAX_PARALLEL_SOURCES": "8",
        }
    )
    assert c.search_query == "trains|railway"
    assert c.log_level == "DEBUG"  # normalised to upper-case
    assert c.proxy_youtube is True
    assert c.max_parallel_sources == 8


def test_exclude_categories_parsed_casefolded():
    c = config.load(
        {"YOUTUBE_API_KEY": "k", "EXCLUDE_CATEGORIES": "Religion, Sports ,music"}
    )
    assert c.exclude_categories == frozenset({"religion", "sports", "music"})


def test_exclude_categories_default_empty():
    assert config.load({"YOUTUBE_API_KEY": "k"}).exclude_categories == frozenset()


def test_bad_log_level_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load({"YOUTUBE_API_KEY": "k", "LOG_LEVEL": "VERBOSE"})
    assert "LOG_LEVEL" in caplog.text


def test_bad_proxy_youtube_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        c = config.load({"YOUTUBE_API_KEY": "k", "PROXY_YOUTUBE": "maybe"})
    assert "PROXY_YOUTUBE" in caplog.text
    assert c.proxy_youtube is False  # bad value falls back to the default


def test_bad_max_parallel_sources_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        c = config.load({"YOUTUBE_API_KEY": "k", "MAX_PARALLEL_SOURCES": "lots"})
    assert "MAX_PARALLEL_SOURCES" in caplog.text
    assert c.max_parallel_sources == 4  # bad value falls back to the default


def test_max_parallel_sources_clamped_to_minimum() -> None:
    c = config.load({"YOUTUBE_API_KEY": "k", "MAX_PARALLEL_SOURCES": "0"})
    assert c.max_parallel_sources == 1  # _int_env clamps to the minimum


def test_localhost_public_base_url_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load({"YOUTUBE_API_KEY": "k"})
    assert "PUBLIC_BASE_URL" in caplog.text


def test_non_localhost_public_base_url_no_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load(
            {"YOUTUBE_API_KEY": "k", "PUBLIC_BASE_URL": "https://cams.example.com"}
        )
    assert "PUBLIC_BASE_URL" not in caplog.text


def test_bad_catalogue_interval_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        bad = config.load({"YOUTUBE_API_KEY": "k", "CATALOGUE_INTERVAL_HOURS": "x"})
    assert bad.catalogue_interval_hours == 6
    assert "CATALOGUE_INTERVAL_HOURS" in caplog.text


def test_unknown_exclude_category_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load({"YOUTUBE_API_KEY": "k", "EXCLUDE_CATEGORIES": "Relgion"})
    assert "relgion" in caplog.text.lower()


def test_legacy_v1_var_renamed_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load({"YOUTUBE_API_KEY": "k", "EXCLUDED_CATEGORIES": "Sports"})
    # points the migrator at the v2 name
    assert "EXCLUDED_CATEGORIES" in caplog.text
    assert "EXCLUDE_CATEGORIES" in caplog.text


def test_legacy_v1_var_removed_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.config"):
        config.load({"YOUTUBE_API_KEY": "k", "MAX_VIDEOS_PER_CYCLE": "500"})
    assert "MAX_VIDEOS_PER_CYCLE" in caplog.text
