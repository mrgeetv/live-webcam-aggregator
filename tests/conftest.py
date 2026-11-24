from __future__ import annotations

import pytest

from webcam_aggregator import serving


@pytest.fixture(autouse=True)
def fresh_serve_log_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test a fresh serve-path log limiter.

    The serve path logs a repeated failure once a minute; a fresh limiter per test
    stops one test's lines suppressing the next one's.
    """
    monkeypatch.setattr(serving, "repeats", serving.RepeatLimiter())
