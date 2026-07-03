from __future__ import annotations

import logging
import re
from typing import override

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import HtmlScraperSource, with_location_parts

log = logging.getLogger("webcam-aggregator.skyline")

_BASE = "https://www.skylinewebcams.com"

# Skyline category-page slug -> a categories._MAP key (mapped to the unified taxonomy at
# catalogue build). "live-web" is Skyline's generic "Live Web Cams" bucket — no real
# category, so it (and cams only reachable via country/region pages) falls to "Other".
_CATEGORY: dict[str, str | None] = {
    "beach": "Beaches",
    "city": "Cities",
    "nature-mountain": "Mountains",
    "seaport": "Ports",
    "ski": "Ski Resorts",
    "lake": "Rivers Lakes",
    "animals": "Animals",
    "unesco": "Sights",
    "volcanoes": "Volcanoes",
    "live-web": None,
}

# Skyline's category index lists every category page; crawling it (vs hardcoding the
# slugs above) means a newly-added category surfaces — logged + -> "Unmapped Category".
_CAT_INDEX = re.compile(r"/en/live-cams-category/([a-z0-9-]+)-cams\.html")

# Cam detail links are RELATIVE (no leading slash); the country/region/city nav links are
# absolute with a `btn …tag` class. The leading-slash distinction keeps the two apart.
_CAM_LINK = re.compile(r'href="(en/webcam/[a-z0-9/-]+\.html)"')
_COUNTRY_LINK = re.compile(r'href="(/en/webcam/[a-z-]+\.html)"')
_SUBREGION_LINK = re.compile(
    r'href="(/en/webcam/[a-z0-9/-]+\.html)" class="btn[^"]*tag"'
)

_H1 = re.compile(r"<h1[^>]*>([^<]{1,120})</h1>")
_LIVECAM_SUFFIX = re.compile(r"\s*live cam\s*$", re.I)
_BREADCRUMB = re.compile(r'<ol class="breadcrumb".*?</ol>', re.S)
_BREADCRUMB_NAME = re.compile(r'<span itemprop="name">([^<]+)</span>')

# Skyline's own Clappr HLS (token regenerated per page-load, so resolved fresh at
# serve-time by SkylineExtractor — the candidate target is the cam PAGE) vs a "from the
# web" YouTube embed (point straight at the watch URL for the existing yt-dlp resolver).
_TOKEN = re.compile(r"source:'livee?\.m3u8\?a=[a-z0-9]+'")
_VIDEOID = re.compile(r"videoId:'([A-Za-z0-9_-]{11})'")

# ctx = (cam name, breadcrumb geo parts, general -> specific)
_Ctx = tuple[str, list[str]]


class SkylineSource(HtmlScraperSource[_Ctx]):
    name: str = "skyline"
    _cat: dict[str, str | None]

    @override
    def __init__(self, fetch: FetcherProtocol) -> None:
        super().__init__(fetch)
        self._cat = {}  # cam URL -> category (only set from the category pages)

    def _category_slugs(self) -> list[str]:
        """Skyline's category slugs from its index page, so a newly-added category
        surfaces instead of being silently missed. Falls back to the known slugs if the
        index is unreachable."""
        html = self._fetch.get(f"{_BASE}/en/live-cams.html") or ""
        return list(dict.fromkeys(_CAT_INDEX.findall(html))) or list(_CATEGORY)

    @override
    def _page_urls(self) -> list[str]:
        cams: set[str] = set()
        seed = ""
        slugs = self._category_slugs()
        unmapped = sorted(s for s in slugs if s not in _CATEGORY)
        if unmapped:
            log.warning(
                "skyline: %d category page(s) not in _CATEGORY (-> Unmapped Category): %s",
                len(unmapped),
                ", ".join(unmapped),
            )
        cat_urls = [f"{_BASE}/en/live-cams-category/{s}-cams.html" for s in slugs]
        # Category pages give the bulk of the cams AND their category (an unknown slug
        # passes through as the raw slug, which maps to "Unmapped Category").
        for slug, html in zip(slugs, thread_map(self._fetch.get, cat_urls)):
            html = html or ""
            seed = seed or html
            cat = _CATEGORY.get(slug, slug)
            for rel in _CAM_LINK.findall(html):
                u = f"{_BASE}/{rel}"
                cams.add(u)
                if cat and u not in self._cat:
                    self._cat[u] = cat
        # Country pages (from the nav) -> regions -> cities: BFS over the `btn tag` geo
        # links, picking up cams Skyline never filed under a category (-> "Other"). The
        # visited set bounds it and absorbs the back-to-parent links.
        visited: set[str] = set()
        frontier = list({f"{_BASE}{h}" for h in _COUNTRY_LINK.findall(seed)})
        while frontier:
            frontier = [u for u in frontier if u not in visited]
            visited.update(frontier)
            if not frontier:
                break
            nxt: list[str] = []
            for html in thread_map(self._fetch.get, frontier):
                html = html or ""
                for rel in _CAM_LINK.findall(html):
                    cams.add(f"{_BASE}/{rel}")
                for sub in _SUBREGION_LINK.findall(html):
                    u = f"{_BASE}{sub}"
                    if u not in visited:
                        nxt.append(u)
            frontier = nxt
        return sorted(cams)

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        m = _H1.search(html)
        name = _LIVECAM_SUFFIX.sub("", m.group(1).strip()) if m else ""
        bc = _BREADCRUMB.search(html)
        geo = [g.strip() for g in _BREADCRUMB_NAME.findall(bc.group(0))] if bc else []
        return self._cat.get(url), (name, geo)

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        name, geo = ctx
        # geo is the breadcrumb (English place names), not the native URL path.
        return with_location_parts(name, geo, drop=category or "")

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        if _TOKEN.search(html):
            target, key = url, None  # serve-time SkylineExtractor resolves page -> HLS
        else:
            m = _VIDEOID.search(html)
            if not m:
                return []  # offline / no player on the page
            target = f"https://www.youtube.com/watch?v={m.group(1)}"
            key = f"yt:{m.group(1)}"  # dedups against youtube-api + scraped YT embeds
        return [
            Candidate(
                title="",
                angle_key=None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                predisc_key=key,
            )
        ]
