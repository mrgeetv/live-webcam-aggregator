from __future__ import annotations

import json
import logging
import re
from typing import override

from ..extractors.skyline import stream_token
from ..fetch import thread_imap
from ..models import Candidate
from .base import HtmlScraperSource, decode_entities, with_location_parts

log = logging.getLogger("webcam-aggregator.skyline")

_BASE = "https://www.skylinewebcams.com"
# The English sitemap is the complete cam list, in one fetch. The geo pages (country ->
# region -> city) and category pages each omit some live cams — mostly "from the web"
# YouTube ones — so neither can stand in for it.
_SITEMAP = f"{_BASE}/sitemap_en.xml"
# A cam page sits exactly four levels under /en/webcam/ (country/region/city/cam).
# The sitemap also lists the shallower geo index pages and a /timelapse.html page
# beneath each cam, plus weather and site pages; none of those carry a player.
_CAM_PATH = r"/en/webcam/(?:[a-z0-9-]+/){3}[a-z0-9-]+\.html"
_SITEMAP_CAM = re.compile(rf"<loc>({re.escape(_BASE)}{_CAM_PATH})</loc>")

# Skyline category-page slug -> a categories._MAP key (mapped to the unified taxonomy at
# catalogue build). "live-web" is Skyline's generic "Live Web Cams" bucket — no real
# category, so it (and any cam no category page lists) falls to "Other".
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

# A category page's cam links are root-relative.
_CAM_LINK = re.compile(rf'href="({_CAM_PATH})"')

_H1 = re.compile(r"<h1[^<>]*>([^<]{1,120})</h1>")
_LIVECAM_SUFFIX = re.compile(r"(?<!\s)\s*live cam\s*$", re.I)
_LD_JSON = re.compile(
    r'<script type="application/ld\+json">((?:[^<]|<(?!/?script))*+)</script>', re.I
)

# Skyline's own Clappr HLS (its token lapses unless the page is re-fetched, so it is
# resolved fresh at serve-time by SkylineResolver — the candidate target is the cam
# PAGE) vs a "from the web" YouTube embed (point straight at the watch URL for the
# existing yt-dlp resolver). An offline cam's JSON-LD can still name a YouTube embed
# URL, but only a live one has a player config, hence `videoId:` rather than the URL.
_VIDEOID = re.compile(r"""videoId:\s*["']([A-Za-z0-9_-]{11})["']""")

# ctx = (cam name, breadcrumb geo parts, general -> specific)
_Ctx = tuple[str, list[str]]


def _breadcrumb_geo(html: str) -> list[str]:
    """The English place names from the page's JSON-LD BreadcrumbList (country ->
    region -> city), minus its "Live Cams" root, which links the /en/webcam.html
    index rather than a place."""
    for blob in _LD_JSON.findall(html):
        try:
            ld = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(ld, dict) or ld.get("@type") != "BreadcrumbList":
            continue
        return [
            str(e["name"]).strip()
            for e in ld.get("itemListElement", [])
            if isinstance(e, dict)
            and e.get("name")
            and not str(e.get("item", "")).endswith("/en/webcam.html")
        ]
    return []


class SkylineSource(HtmlScraperSource[_Ctx]):
    name: str = "skyline"
    _cat: dict[str, str | None]  # cam URL -> category, filled by _page_urls

    def _category_slugs(self) -> list[str]:
        """Skyline's category slugs from its index page, so a newly-added category
        surfaces instead of being silently missed. Falls back to the known slugs if the
        index is unreachable."""
        html = self._fetch.get(f"{_BASE}/en/categories.html") or ""
        return list(dict.fromkeys(_CAT_INDEX.findall(html))) or list(_CATEGORY)

    @override
    def _page_urls(self) -> list[str]:
        # Rebuilt each build: the source lives for the process, and a cam Skyline
        # re-files under another category must not keep its first one.
        self._cat = {}
        slugs = self._category_slugs()
        unmapped = sorted(s for s in slugs if s not in _CATEGORY)
        if unmapped:
            log.warning(
                "skyline: %d category page(s) not in _CATEGORY (-> Unmapped Category): %s",
                len(unmapped),
                ", ".join(unmapped),
            )
        cat_urls = [f"{_BASE}/en/live-cams-category/{s}-cams.html" for s in slugs]
        # A cam's category exists only as which category page lists it (an unknown slug
        # passes through as the raw slug, which maps to "Unmapped Category"). A cam on
        # several category pages takes the first in the index's order; one on none
        # stays uncategorised.
        for slug, html in zip(slugs, thread_imap(self._fetch.get, cat_urls)):
            cat = _CATEGORY.get(slug, slug)
            for rel in _CAM_LINK.findall(html or ""):
                u = f"{_BASE}{rel}"
                if cat and u not in self._cat:
                    self._cat[u] = cat
        return sorted(set(_SITEMAP_CAM.findall(self._fetch.get(_SITEMAP) or "")))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        m = _H1.search(html)
        name = _LIVECAM_SUFFIX.sub("", decode_entities(m.group(1)).strip()) if m else ""
        return self._cat.get(url), (name, _breadcrumb_geo(html))

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        name, geo = ctx
        # geo is the breadcrumb (English place names), not the native URL path.
        return with_location_parts(name, geo, drop=category or "")

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        if stream_token(html):
            target, key = url, None  # serve-time SkylineResolver resolves page -> HLS
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
