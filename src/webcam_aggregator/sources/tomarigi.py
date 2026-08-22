from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html import unescape

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import predisc_key

# The sitemap is the ONLY complete index: both map views (/ and /world) are
# client-rendered, and the /spots/<slug> listings cover just the ~1300 Japanese cams
# of a ~4900-cam, mostly-international corpus. robots.txt allows the pages and the
# sitemap, and disallows the site's own /api/ — so pages it is.
_SITEMAP = "https://tomarigi.me/sitemap.xml"
_LOC = re.compile(r"<loc>(https://tomarigi\.me/spot/[0-9a-f-]{36})</loc>")
_LD_JSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

# Every cam page's VideoObject description ends with a sentence naming the cam's
# category: "…(提供: <channel>)。<category>のライブカメラを地図から探せる…". That is the
# only per-cam category on the page, and unlike the /spots/<slug> listings it covers
# the international cams too.
_CATEGORY = re.compile(r"。([^」。]{1,8})のライブカメラ")

# The site's categories -> categories._MAP keys (mapped to the unified taxonomy at
# build time). An unknown slug passes through RAW so it lands in "Unmapped Category"
# with a warning naming it, rather than hiding in "Other" — a tenth category appearing
# on the site should be visible work, not silence.
_CATEGORY_MAP: dict[str, str] = {
    "海": "Beaches",
    "自然": "Parks",
    "街": "Cities",
    "名所": "Sights",
    "鉄道": "Trains",
    "空港": "Airports",
    "動物": "Animals",
    "その他": "Other",
}
# 河川・道路 ("rivers & roads" — the site's disaster-monitoring bucket) is a near-even
# mix of river gauges and road junctions, so ONE mapping would be wrong half the time.
# None sends it to the title fallback, which splits it per-cam.
_UNCATEGORISED = frozenset({"河川・道路"})


def _category(description: str) -> str | None:
    m = _CATEGORY.search(description)
    if not m:
        return None
    slug = m.group(1)
    return None if slug in _UNCATEGORISED else _CATEGORY_MAP.get(slug, slug)


def _video_object(html: str) -> dict[str, object] | None:
    """The cam page's VideoObject JSON-LD (it also carries a WebSite block)."""
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(unescape(block))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return None


class TomarigiSource:
    """tomarigi.me (とまり木): a curated directory of ~4900 YouTube live cams —
    Japanese-branded but mostly international. Sitemap -> per-cam page, whose
    VideoObject JSON-LD carries the watch URL, the name and the category, so there
    is nothing to extract from the player and every cam dedups by its `yt:` key.
    A page with no VideoObject (a withdrawn cam still in the sitemap) drops."""

    name: str = "tomarigi"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        xml = self._fetch.get(_SITEMAP) or ""
        pages = sorted(set(_LOC.findall(xml)))
        seen: set[str] = set()
        for page, html in zip(pages, thread_map(self._fetch.get, pages)):
            item = _video_object(html or "")
            if item is None:
                continue
            key = predisc_key(str(item.get("contentUrl") or ""))
            if not key or not key.startswith("yt:") or key in seen:
                continue
            seen.add(key)
            yield Candidate(
                title=str(item.get("name") or "").strip(),
                angle_key=None,
                category=_category(str(item.get("description") or "")),
                source=self.name,
                source_page_url=page,
                target_url=f"https://www.youtube.com/watch?v={key[3:]}",
                predisc_key=key,
            )
