from __future__ import annotations

import re
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, with_location

_MAX_LIST_PAGES = 80
_LINKS = re.compile(r'class="cam-promo__title"[^>]*>\s*<a href="([^"]+)"')
_TITLE = re.compile(r"<h1[^>]*>([^<]{1,120})</h1>")
_CATEGORY = re.compile(r'Category:\s*(?:&nbsp;)?<a href="/([^/]+)/">([^<]+)</a>')
# Per-cam names on multi-stream pages: <a … id="streams__item_<stream-id>" …>Name</a>
_STREAM_NAMES = re.compile(r'id="streams__item_(\d+)"[^>]*>\s*([^<]+?)\s*</a>')

# ctx = (page <h1> title, {stream-id: specific cam name})
_Ctx = tuple[str, dict[str, str]]


class WorldcamsSource(HtmlScraperSource[_Ctx]):
    name: str = "worldcams"

    @override
    def _page_urls(self) -> list[str]:
        # List pages stay sequential with early-stop (politest, only ~tens of them);
        # the bulk — the per-camera detail pages — is fetched concurrently by the base.
        urls: list[str] = []
        for page in range(1, _MAX_LIST_PAGES + 1):
            html = self._fetch.get(f"https://worldcams.tv/list/?page={page}")
            links = list(dict.fromkeys(_LINKS.findall(html))) if html else []
            if not links:
                break
            urls.extend("https://worldcams.tv" + link for link in links)
        return list(dict.fromkeys(urls))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        tm = _TITLE.search(html)
        page_title = tm.group(1).strip() if tm else ""
        cm = _CATEGORY.search(html)
        category = cm.group(2).strip() if cm else None
        # stream-id -> specific cam name (multi-stream pages); single-stream pages have
        # no selector, so these fall back to the page <h1>.
        names = {sid: name.strip() for sid, name in _STREAM_NAMES.findall(html)}
        return category, (page_title, names)

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        page_title, names = ctx
        specific = names.get(cand.angle_key or "", "")
        return with_location(specific or page_title, url, drop=category or "")
