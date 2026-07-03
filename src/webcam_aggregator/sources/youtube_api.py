from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

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
    name: str = "youtube-api"
    _c: Any
    _query: str
    _max: int

    def __init__(self, client: Any, query: str, max_videos: int = 1000) -> None:
        self._c = client
        self._query = query
        self._max = max_videos

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
                resp = self._c.search().list(**params).execute()
            except Exception as exc:
                # Log the status only; the request URL carries the API key.
                status = getattr(getattr(exc, "resp", None), "status", None)
                log.warning(
                    "youtube search stopped after %d items (HTTP %s). Likely API "
                    "quota; raise the quota or narrow SEARCH_QUERY.",
                    len(seen),
                    status if status is not None else "?",
                )
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
            resp = (
                self._c.videos()
                .list(part="snippet,liveStreamingDetails", id=",".join(chunk))
                .execute()
            )
            for it in resp.get("items", []):
                snip = it.get("snippet", {})
                details = it.get("liveStreamingDetails", {})
                if (
                    snip.get("liveBroadcastContent") == "live"
                    and "actualEndTime" not in details
                ):
                    live[it["id"]] = _YT_CATEGORIES.get(snip.get("categoryId", ""), "")
        return live
