import logging
from typing import Any

import pytest

from webcam_aggregator.sources.youtube_api import YoutubeApiSource


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
    cands = list(YoutubeApiSource(client, query="cam").discover())
    assert [c.predisc_key for c in cands] == ["yt:aaaaaaaaaaa", "yt:bbbbbbbbbbb"]
    # 1st call has no window; 2nd carries publishedBefore = 1st window's last publishedAt
    assert "publishedBefore" not in client.search().calls[0]
    assert client.search().calls[1]["publishedBefore"] == "2026-01-02T00:00:00Z"


def test_discover_stops_on_quota_error(caplog: pytest.LogCaptureFixture) -> None:
    src = YoutubeApiSource(_FakeClient(search=[RuntimeError("403 quota")]), query="cam")
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.sources.youtube"):
        assert list(src.discover()) == []
    assert "youtube search stopped" in caplog.text


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
    src = YoutubeApiSource(_FakeClient(videos=[resp]), query="cam")
    # returns {live_id: category_name}; categoryId 19 → "Travel & Events"
    assert src.live_ids(["live1", "ended", "vod"]) == {"live1": "Travel & Events"}
