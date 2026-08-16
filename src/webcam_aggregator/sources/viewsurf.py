from __future__ import annotations

import logging
import re
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, predisc_key, with_location_parts

log = logging.getLogger("webcam-aggregator.viewsurf")

_SITEMAP = "https://www.viewsurf.com/sitemap.xml"

# viewsurf univers slug -> a categories._MAP key (mapped to the unified taxonomy at
# build). Surf spots are beach views — there is no finer bucket for them. An unknown
# slug passes through raw so it lands in "Unmapped Category" and gets logged.
_CATEGORY: dict[str, str] = {
    "montagne": "Mountains",
    "plage": "Beaches",
    "surf": "Beaches",
    "trafic": "Traffic",
    "ville": "Cities",
}

# Sitemap vue URLs: /univers/<slug>/vue/<id>-<country>-<region>-<city>-<name>. The
# same cam is listed once per univers it belongs to (plage cams repeat under surf),
# so enumeration dedups on the numeric cam id.
_VUE = re.compile(
    r"<loc>(https://(?:www\.)?viewsurf\.com/univers/([a-z0-9-]+)/vue/(\d+)-[a-z0-9-]+)"
    r"</loc>"
)
_UNIVERS = re.compile(r"/univers/([a-z0-9-]+)/vue/")
# Only the country is safely splittable out of the slug: it is the token right after
# the id (single-word for every cam listed), while region/city/name are multi-word
# and have no delimiter between them.
_COUNTRY = re.compile(r"/vue/\d+-([a-z0-9]+)")
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_TAGS = re.compile(r"<[^>]+>")
# The cam page's player iframe is a Quanteec/joada embed. The uuid is the cam's
# stable identity (it encodes an internal cam id) and `type` says continuous live vs
# a refreshed short clip; everything else in the query is presentation (title text,
# timezone, a render timestamp), so the candidate keeps only uuid+type — a stable
# target for the viewsurf extractor to resolve.
_EMBED = re.compile(
    r"platforms\d+\.joada\.net/embeded/embeded\.html"
    r"\?uuid=([0-9a-f-]+)&(?:amp;)?type=(live|vod)"
)


class ViewsurfSource(HtmlScraperSource[str]):
    """viewsurf.com: sitemap -> per-cam vue pages -> the Quanteec/joada player embed.

    Titles come from the page h1 (city + cam name) plus the country from the URL
    slug; the category is the univers segment of the vue URL. Pages with no joada
    embed (panoramic-photo cams) yield nothing and drop. `ctx` is the finished
    display title."""

    name: str = "viewsurf"

    @override
    def _page_urls(self) -> list[str]:
        sm = self._fetch.get(_SITEMAP) or ""
        by_id: dict[str, tuple[str, str]] = {}
        slugs: set[str] = set()
        for url, univers, cam_id in _VUE.findall(sm):
            slugs.add(univers)
            # One page per cam; prefer a listing whose univers we can map, so a cam
            # that is in both a mapped and an unmapped univers keeps a real category.
            if cam_id not in by_id or (
                by_id[cam_id][1] not in _CATEGORY and univers in _CATEGORY
            ):
                by_id[cam_id] = (url, univers)
        # Crawl-first coverage: the sitemap is the category index, so flag univers
        # slugs we have no mapping for (their cams also land in "Unmapped Category")
        # instead of silently dropping them to "Other".
        unmapped = sorted(s for s in slugs if s not in _CATEGORY)
        if unmapped:
            log.warning(
                "viewsurf: %d univers slug(s) not in _CATEGORY"
                " (-> Unmapped Category): %s",
                len(unmapped),
                ", ".join(unmapped),
            )
        return [by_id[k][0] for k in sorted(by_id, key=int)]

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _UNIVERS.search(url)
        slug = m.group(1) if m else ""
        category = _CATEGORY.get(slug, slug or None)
        h1 = _H1.search(html)
        # h1 is "City <small>Cam name</small>" — flatten the markup to one title.
        title = unescape(_TAGS.sub(" ", h1.group(1))) if h1 else ""
        title = re.sub(r"\s+", " ", title).strip()
        cm = _COUNTRY.search(url)
        country = cm.group(1).title() if cm else ""
        return category, with_location_parts(
            title, [country] if country else [], drop=category or ""
        )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        m = _EMBED.search(html)
        if not m:
            return []
        target = (
            "https://platforms5.joada.net/embeded/embeded.html"
            f"?uuid={m.group(1)}&type={m.group(2)}"
        )
        return [
            Candidate(
                title="",
                angle_key=None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                predisc_key=predisc_key(target),
            )
        ]
