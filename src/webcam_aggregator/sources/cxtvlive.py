"""cxtvlive.com source: the map feed unioned with the sitemap, then cam pages."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, override

from webcam_aggregator.sources.base import (
    HtmlScraperSource,
    decode_entities,
    extract_candidates,
    with_location,
    with_location_parts,
)

if TYPE_CHECKING:
    from webcam_aggregator.models import Candidate

_BASE = "https://www.cxtvlive.com/live-camera/"
_SITEMAP = "https://www.cxtvlive.com/sitemap.php"
_MAP = "https://www.cxtvlive.com/data/cxl_mapa.php"

_SLUG = re.compile(r"live-camera/([^<\s?#]+)")
_TITLE = re.compile(r"<h1[^<>]*>([^<]{1,120})</h1>")
# The site's own YouTube channel link sits in every YouTube cam page's chrome; the
# shared ladder emits it as `…/live` beside the real video embed. Drop it when the page
# has a real candidate too (same reasoning as beachcam) — keep it only if it's the lone
# signal, when it IS the cam.
_YT_CHANNEL_LIVE = re.compile(
    r"youtube\.com/(?:channel/[\w-]+|@[\w.-]+)/live$", re.IGNORECASE
)

# Quality/format tags the map lists as a "category" alongside the real one
# ("High Definition · Cities"): skip them so the content category wins.
# A map feed entry is read up to index 7 (name, slug, geo, categories at 2/3/5/7).
_FEED_FIELDS = 8
_NOISE_TAGS = {"high definition", "high definition hd"}

# Per-cam context carried from _page_meta to _title_for: the title base and the map's
# geo parts (general -> specific), empty for cams present only in the sitemap.
_Ctx = tuple[str, list[str]]


def _primary_category(field: object) -> str | None:
    """Return the first real content category in a map entry's "A · B" category field.

    Quality tags like "High Definition" are skipped. None when nothing's left.
    """
    for raw in str(field).split("·"):
        seg = raw.strip()
        if seg and seg.lower() not in _NOISE_TAGS:
            return seg
    return None


def _geo_parts(field: object) -> list[str]:
    """Split the map's "Country - Region - City" geo field into title parts."""
    return [p.strip() for p in str(field).split(" - ") if p.strip()]


class CxtvliveSource(HtmlScraperSource[_Ctx]):
    """cxtvlive.com cams; the feed carries each cam's category and geo."""

    name: str = "cxtvlive"
    interval_hours: float = 6

    # slug -> (raw category, cam name, geo parts); populated by _page_urls from the map
    # feed, read back per page in _page_meta. Sitemap-only cams are absent (no geo/cat).
    _by_slug: dict[str, tuple[str | None, str | None, list[str]]]

    @override
    def _page_urls(self) -> list[str]:
        # The map feed (cxl_mapa.php) is the fuller, structured list — it carries each
        # cam's category and geo, which the cam pages themselves render client-side and
        # so don't serve. The sitemap (sitemap.php) is unioned in for the wider net; its
        # extra cams are scraped without the map's metadata. (sitemap.xml is gone.)
        self._by_slug = {}
        slugs: list[str] = []
        raw = self._fetch.get(_MAP)
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
            for p in data.get("p", []):
                if not isinstance(p, list) or len(p) < _FEED_FIELDS:
                    continue
                name, slug, geo, cats = p[2], p[3], p[5], p[7]
                if not isinstance(slug, str) or not slug:
                    continue
                self._by_slug[slug] = (
                    _primary_category(cats),
                    name if isinstance(name, str) and name else None,
                    _geo_parts(geo),
                )
                slugs.append(slug)
        sitemap = self._fetch.get(_SITEMAP) or ""
        slugs.extend(_SLUG.findall(sitemap))
        seen: set[str] = set()
        return [_BASE + s for s in slugs if not (s in seen or seen.add(s))]

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        cands = list(extract_candidates(html, page_url=url, source=self.name))
        real = [c for c in cands if not _YT_CHANNEL_LIVE.search(c.target_url)]
        return real or cands

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        slug = url.split("/live-camera/", 1)[-1]
        category, map_name, geo = self._by_slug.get(slug, (None, None, []))
        tm = _TITLE.search(html)
        title_base = (
            (decode_entities(tm.group(1)).strip() if tm else None)
            or map_name
            or slug.replace("-", " ").title()
        )
        return category, (title_base, geo)

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        title_base, geo = ctx
        drop = category or ""
        if geo:
            return with_location_parts(title_base, geo, drop=drop)
        return with_location(title_base, url, drop=drop)
