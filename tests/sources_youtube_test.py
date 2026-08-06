import logging
from typing import Any, override

import pytest

from webcam_aggregator.sources.youtube_api import YoutubeApiSource

# Fake, non-functional key in the real AIza… shape.
_FAKE_KEY = "AIzaSyFAKEKEY_notarealkey_1234567890"


class _Req:
    _result: Any

    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Endpoint:
    _results: list[Any]
    _i: int
    calls: list[dict[str, Any]]

    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self._i = 0
        self.calls = []

    def list(self, **kwargs: Any) -> _Req:
        self.calls.append(kwargs)
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return _Req(r)


class _FakeClient:
    _search: _Endpoint
    _videos: _Endpoint

    def __init__(
        self,
        search: list[Any] | None = None,
        videos: list[Any] | None = None,
    ) -> None:
        self._search = _Endpoint(search or [])
        self._videos = _Endpoint(videos or [])

    def search(self) -> _Endpoint:
        return self._search

    def videos(self) -> _Endpoint:
        return self._videos


def _item(
    vid: str, title: str = "t", published: str = "2026-01-01T00:00:00Z"
) -> dict[str, Any]:
    return {
        "id": {"videoId": vid},
        "snippet": {"title": title, "publishedAt": published},
    }


def test_discover_walks_published_windows() -> None:
    # discover walks back in time via publishedBefore (NOT pageToken, which YouTube
    # caps at ~100 for live searches), dedups, and stops when a window adds nothing new.
    page1 = {"items": [_item("aaaaaaaaaaa", published="2026-01-02T00:00:00Z")]}
    page2 = {"items": [_item("bbbbbbbbbbb", published="2026-01-01T00:00:00Z")]}
    client = _FakeClient(search=[page1, page2])
    cands = list(YoutubeApiSource(lambda: client, query="cam").discover())
    assert [c.predisc_key for c in cands] == ["yt:aaaaaaaaaaa", "yt:bbbbbbbbbbb"]
    # 1st call has no window; 2nd carries publishedBefore = 1st window's last publishedAt
    assert "publishedBefore" not in client.search().calls[0]
    assert client.search().calls[1]["publishedBefore"] == "2026-01-02T00:00:00Z"


def test_discover_stops_on_quota_error(caplog: pytest.LogCaptureFixture) -> None:
    src = YoutubeApiSource(
        lambda: _FakeClient(search=[RuntimeError("403 quota")]), query="cam"
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.sources.youtube"):
        assert list(src.discover()) == []
    assert "youtube search stopped" in caplog.text


class _Resp403:
    status: int = 403


class _Denied(Exception):
    resp: _Resp403 = _Resp403()


class _LeakyError(Exception):
    """An exception whose str carries a key, like googleapiclient's HttpError."""

    @override
    def __str__(self) -> str:
        return (
            "requesting https://youtube.googleapis.com/youtube/v3/search"
            f"?q=cam&key={_FAKE_KEY}"
        )


def test_search_warning_names_the_real_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A BrokenPipeError must not be reported as an API quota problem — that guess
    sent the 2026-08 outage investigation down the wrong path for hours."""
    src = YoutubeApiSource(
        lambda: _FakeClient(search=[BrokenPipeError(32, "Broken pipe")]), query="cam"
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.sources.youtube"):
        assert list(src.discover()) == []
    assert "BrokenPipeError" in caplog.text
    assert "Broken pipe" in caplog.text
    assert "HTTP n/a" in caplog.text
    assert "quota" not in caplog.text.lower()


def test_search_warning_mentions_quota_only_on_403(
    caplog: pytest.LogCaptureFixture,
) -> None:
    src = YoutubeApiSource(
        lambda: _FakeClient(search=[_Denied("forbidden")]), query="cam"
    )
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.sources.youtube"):
        assert list(src.discover()) == []
    assert "HTTP 403" in caplog.text
    assert "quota" in caplog.text.lower()


def test_search_warning_scrubs_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    """Non-vacuous: the exception's str CONTAINS a key and it must not survive."""
    src = YoutubeApiSource(lambda: _FakeClient(search=[_LeakyError()]), query="cam")
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.sources.youtube"):
        assert list(src.discover()) == []
    assert _FAKE_KEY not in caplog.text
    assert "key=REDACTED" in caplog.text


def test_live_ids_filters_offair() -> None:
    resp = {
        "items": [
            {
                "id": "live1",
                "snippet": {"liveBroadcastContent": "live", "categoryId": "19"},
                "liveStreamingDetails": {},
            },
            {
                "id": "ended",
                "snippet": {"liveBroadcastContent": "live"},
                "liveStreamingDetails": {"actualEndTime": "x"},
            },
            {
                "id": "vod",
                "snippet": {"liveBroadcastContent": "none"},
                "liveStreamingDetails": {},
            },
        ]
    }
    src = YoutubeApiSource(lambda: _FakeClient(videos=[resp]), query="cam")
    # returns {live_id: category_name}; categoryId 19 → "Travel & Events"
    assert src.live_ids(["live1", "ended", "vod"]) == {"live1": "Travel & Events"}


# ---------------------------------------------------------------------------
# Wedged-connection recovery. httplib2 evicts a pooled connection only on
# socket.timeout, so a BrokenPipeError leaves the dead socket in place and every
# later call reuses it — that kept YouTube dead for five consecutive rebuilds on
# 2026-08-04/05. num_retries can't help; the Http object has to be replaced.
# ---------------------------------------------------------------------------


def test_discover_drops_the_client_after_a_failure() -> None:
    built: list[_FakeClient] = []

    def factory() -> _FakeClient:
        # first client always fails, replacement works
        c = (
            _FakeClient(search=[BrokenPipeError(32, "Broken pipe")])
            if not built
            else _FakeClient(search=[{"items": [_item("aaaaaaaaaaa")]}])
        )
        built.append(c)
        return c

    src = YoutubeApiSource(factory, query="cam")
    assert list(src.discover()) == []  # wedged
    assert len(built) == 1
    # The next cycle must NOT reuse the wedged client.
    cands = list(src.discover())
    assert len(built) == 2
    assert [c.predisc_key for c in cands] == ["yt:aaaaaaaaaaa"]


def test_live_ids_drops_the_client_and_reraises() -> None:
    built: list[_FakeClient] = []

    def factory() -> _FakeClient:
        c = (
            _FakeClient(videos=[BrokenPipeError(32, "Broken pipe")])
            if not built
            else _FakeClient(videos=[{"items": []}])
        )
        built.append(c)
        return c

    src = YoutubeApiSource(factory, query="cam")
    with pytest.raises(BrokenPipeError):
        src.live_ids(["a"])  # caller still sees the error, as before
    assert len(built) == 1
    assert src.live_ids(["a"]) == {}  # recovered on a fresh client
    assert len(built) == 2


def test_client_is_built_lazily_and_reused_while_healthy() -> None:
    built: list[_FakeClient] = []

    def factory() -> _FakeClient:
        c = _FakeClient(search=[{"items": []}], videos=[{"items": []}])
        built.append(c)
        return c

    src = YoutubeApiSource(factory, query="cam")
    assert built == []  # nothing built until first use
    list(src.discover())
    src.live_ids(["a"])
    assert len(built) == 1  # healthy calls share one client/connection pool
