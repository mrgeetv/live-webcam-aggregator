from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from ..logging_redaction import scrub
from ..models import Candidate

log = logging.getLogger("webcam-aggregator.sources.youtube")

# YouTube's stable video-category IDs → names. Names then flow through
# categories.map_category (mapped to the unified taxonomy or kept as native).
_YT_CATEGORIES: dict[str, str] = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}


class YoutubeApiSource:
    """Discovery via the YouTube Data API.

    Takes a client FACTORY, not a client, and drops the cached client whenever a call
    fails. httplib2 evicts a connection from its pool only on `socket.timeout` — a
    `BrokenPipeError` leaves the dead socket in place, so every later call reuses it and
    fails instantly (no round trip) for as long as the client lives, which across a
    long-lived process means cycle after cycle. `num_retries` does not help: googleapiclient
    retries through that same pooled connection. Replacing the client replaces the Http
    object, and with it the pool.
    """

    name: str = "youtube-api"
    _new_client: Callable[[], Any]
    _c: Any | None
    _query: str
    _max: int
    _lock: threading.Lock

    def __init__(
        self, client_factory: Callable[[], Any], query: str, max_videos: int = 1000
    ) -> None:
        self._new_client = client_factory
        self._c = None
        self._query = query
        self._max = max_videos
        # The single httplib2 client is NOT thread-safe, and its two callers run
        # concurrently during a build: this source's own discover() (search.list) in
        # its pool lane, and live_ids() (videos.list) invoked from EVERY other source's
        # liveness phase. Without serialising, two threads share one TLS socket and it
        # corrupts (SSLError RECORD_LAYER_FAILURE / a None read). This lock guards each
        # round-trip — held only around .execute(), never across discover()'s yields.
        self._lock = threading.Lock()

    @property
    def _client(self) -> Any:
        if self._c is None:
            self._c = self._new_client()
        return self._c

    def _drop_client(self) -> None:
        """Discard the client so the next call builds a fresh connection pool."""
        self._c = None

    def discover(self) -> Iterator[Candidate]:
        # YouTube caps an eventType=live search at ~100 results via pageToken (it
        # reports an inflated totalResults but returns an empty page 3). Paginate by
        # walking backwards in time with publishedBefore instead — each window is a
        # fresh query, so this reaches the deeper results (hundreds) tokens won't.
        published_before: str | None = None
        seen: set[str] = set()
        while len(seen) < self._max:
            params: dict[str, Any] = {
                "part": "id,snippet",
                "type": "video",
                "eventType": "live",
                "maxResults": 50,
                "order": "date",
                "q": self._query,
            }
            if published_before:
                params["publishedBefore"] = published_before
            try:
                with self._lock:
                    resp = self._client.search().list(**params).execute()
            except Exception as exc:
                # Report what ACTUALLY went wrong. Blaming API quota for every failure
                # misdiagnoses the common ones — a wedged socket raises BrokenPipeError
                # with no HTTP status at all. NEVER format the exception raw:
                # googleapiclient's HttpError __str__ prints the request URI, which
                # carries the developer key.
                status = getattr(getattr(exc, "resp", None), "status", None)
                hint = (
                    " — likely API quota; raise the quota or narrow SEARCH_QUERY"
                    if status in (403, 429)
                    else ""
                )
                log.warning(
                    "youtube search stopped after %d items (HTTP %s) — %s: %s%s",
                    len(seen),
                    status if status is not None else "n/a",
                    type(exc).__name__,
                    scrub(str(exc)),
                    hint,
                )
                # The connection may be wedged (see the class docstring) — bin the
                # client so the next cycle starts from a clean pool.
                self._drop_client()
                return
            items = resp.get("items", [])
            if not items:
                break
            before = len(seen)
            for it in items:
                vid = it["id"]["videoId"]
                if vid in seen:
                    continue
                seen.add(vid)
                yield Candidate(
                    title=it["snippet"]["title"],
                    angle_key=None,
                    category=None,
                    source="youtube-api",
                    source_page_url=f"https://www.youtube.com/watch?v={vid}",
                    target_url=f"https://www.youtube.com/watch?v={vid}",
                    predisc_key=f"yt:{vid}",
                )
            next_before = items[-1].get("snippet", {}).get("publishedAt")
            if len(seen) == before or not next_before:
                break  # no new ids, or no timestamp to advance the window
            published_before = next_before

    def live_ids(self, video_ids: Iterable[str]) -> dict[str, str]:
        """Map of currently-live video id -> category name (name may be "")."""
        ids = list(video_ids)
        live: dict[str, str] = {}
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            try:
                with self._lock:
                    resp = (
                        self._client.videos()
                        .list(part="snippet,liveStreamingDetails", id=",".join(chunk))
                        .execute()
                    )
            except Exception:
                # Same wedged-connection risk as discover(). Bin the client, then let
                # the caller handle the failure as before (YT cams treated as offline).
                self._drop_client()
                raise
            for it in resp.get("items", []):
                snip = it.get("snippet", {})
                details = it.get("liveStreamingDetails", {})
                if (
                    snip.get("liveBroadcastContent") == "live"
                    and "actualEndTime" not in details
                ):
                    live[it["id"]] = _YT_CATEGORIES.get(snip.get("categoryId", ""), "")
        return live
