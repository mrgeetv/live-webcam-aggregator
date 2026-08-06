from __future__ import annotations

import io
import logging

import pytest
from googleapiclient.errors import HttpError

from webcam_aggregator.logging_redaction import RedactingFilter, scrub

# A fake value in the real AIza… shape.
_FAKE_KEY = "AIzaSyFAKEKEY_notarealkey_1234567890"


class _Resp:
    """Minimal stand-in for httplib2's response object (HttpError reads .status)."""

    status: int = 503
    reason: str = "Service Unavailable"


def _leaky_http_error() -> HttpError:
    return HttpError(
        _Resp(),  # pyright: ignore[reportArgumentType]
        b'{"error": {"message": "backend error"}}',
        uri=f"https://youtube.googleapis.com/youtube/v3/videos?id=abc&key={_FAKE_KEY}",
    )


# ---------------------------------------------------------------------------
# scrub()
# ---------------------------------------------------------------------------


def test_scrub_redacts_google_api_key() -> None:
    raw = (
        "HttpError 403 when requesting "
        f"https://youtube.googleapis.com/youtube/v3/search?q=x&key={_FAKE_KEY} "
        'returned "quota exceeded"'
    )
    out = scrub(raw)
    assert _FAKE_KEY not in out
    assert "key=REDACTED" in out
    assert "quota exceeded" in out  # the diagnostically useful part survives


@pytest.mark.parametrize("param", ["key", "developerKey", "developer_key", "api_key"])
def test_scrub_covers_key_param_spellings(param: str) -> None:
    assert "SECRETVALUE" not in scrub(f"https://e.example/v3/x?{param}=SECRETVALUE")


def test_scrub_is_case_insensitive() -> None:
    assert "SECRETVALUE" not in scrub("https://e.example/x?KEY=SECRETVALUE")


def test_scrub_stops_at_the_next_param() -> None:
    out = scrub(f"https://e.example/x?key={_FAKE_KEY}&part=snippet")
    assert "part=snippet" in out
    assert _FAKE_KEY not in out


def test_scrub_does_not_eat_words_merely_ending_in_key() -> None:
    assert scrub("https://e.example/x?monkey=banana") == (
        "https://e.example/x?monkey=banana"
    )


def test_scrub_still_redacts_underscore_separated_keys() -> None:
    assert scrub("session_key=abc123") == "session_key=REDACTED"


def test_scrub_leaves_clean_text_untouched() -> None:
    msg = "youtube search stopped after 0 items"
    assert scrub(msg) == msg


# ---------------------------------------------------------------------------
# RedactingFilter
# ---------------------------------------------------------------------------


def test_filter_redacts_a_real_httperror(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("webcam-aggregator.redaction-real")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.redaction-real"):
        log.warning("live_ids failed: %s", _leaky_http_error())
    assert _FAKE_KEY not in caplog.text
    assert "REDACTED" in caplog.text
    assert "503" in caplog.text  # still diagnosable


def test_filter_redacts_args_not_just_the_format_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = logging.getLogger("webcam-aggregator.redaction-args")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.redaction-args"):
        log.warning("url=%s", f"https://e.example/x?key={_FAKE_KEY}")
    assert _FAKE_KEY not in caplog.text


def test_filter_preserves_non_string_arg_formatting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Clean non-str args must pass through unchanged, or %d formatting breaks."""
    log = logging.getLogger("webcam-aggregator.redaction-int")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.redaction-int"):
        log.warning("dropped %d of %d", 7, 12)
    assert "dropped 7 of 12" in caplog.text


def test_filter_handles_dict_args(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("webcam-aggregator.redaction-dict")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.redaction-dict"):
        log.warning("url=%(u)s", {"u": f"https://e.example/x?key={_FAKE_KEY}"})
    assert _FAKE_KEY not in caplog.text


def test_filter_on_handler_covers_child_loggers() -> None:
    """Filters on a LOGGER only see records logged directly on it. Every module here
    logs through a child logger, so the filter has to live on the handler."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    parent = logging.getLogger("webcam-aggregator-handlertest")
    parent.addHandler(handler)
    parent.setLevel(logging.WARNING)
    parent.propagate = False
    try:
        logging.getLogger("webcam-aggregator-handlertest.sources.youtube").warning(
            "boom: %s", f"https://e.example/x?key={_FAKE_KEY}"
        )
    finally:
        parent.removeHandler(handler)
        handler.close()
    out = buf.getvalue()
    assert _FAKE_KEY not in out
    assert "key=REDACTED" in out
