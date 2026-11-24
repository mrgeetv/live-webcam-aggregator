from __future__ import annotations

import json
import re
from collections.abc import Iterator

from ..fetch import FetcherProtocol, thread_imap
from ..models import Candidate
from .base import decode_entities, predisc_key

# The /world/spots/<region> listings (paginated /<n>) are the complete index: together
# they link every cam, Japanese ones included (under asia). Both map views are
# client-rendered off the site's /api/, which robots.txt disallows; the /spots/<slug>
# and /world/country/<cc> listings are subsets. The sitemap names the listing pages
# and every listing links its neighbours and the other regions, so links are followed
# too: a page the sitemap doesn't name yet still gets read.
_SITE = "https://tomarigi.me"
_SITEMAP = f"{_SITE}/sitemap.xml"
_LISTING_PATH = r"/world/spots/[a-z-]+(?:/\d+)?"
_LISTING_LOC = re.compile(rf"<loc>https://tomarigi\.me({_LISTING_PATH})</loc>")
_LISTING_HREF = re.compile(rf'href="({_LISTING_PATH})"')
# The listing's own links, not its ItemList JSON-LD: the ItemList is truncated short
# of the cams the page links.
_SPOT_HREF = re.compile(r'href="(/spot/[0-9a-f-]{36})"')
_LD_JSON = re.compile(
    r'<script type="application/ld\+json"[^<>]*>((?:[^<]|<(?!/?script))*+)</script>'
)

# Every cam page's VideoObject description ends with a sentence naming the cam's
# category, in the shape …(提供: <channel>)。<category>のライブカメラを地図から探せる….
# That is the only per-cam category on the page, and unlike the /spots/<slug>
# listings it covers the international cams too.
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
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return None


class TomarigiSource:
    """tomarigi.me (とまり木): a curated directory of YouTube live cams —
    Japanese-branded but mostly international. Sitemap -> region listing pages ->
    per-cam page, whose VideoObject JSON-LD carries the YouTube embed URL, the name
    and the category, so there is nothing to extract from the player and every cam
    dedups by its `yt:` key. A page with no VideoObject (a withdrawn cam) drops."""

    name: str = "tomarigi"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        xml = self._fetch.get(_SITEMAP) or ""
        listings: set[str] = set(_LISTING_LOC.findall(xml))
        read: set[str] = set()
        spots: set[str] = set()
        while todo := sorted(listings - read):
            read.update(todo)
            for html in thread_imap(self._fetch.get, [_SITE + p for p in todo]):
                spots.update(_SPOT_HREF.findall(html or ""))
                listings.update(_LISTING_HREF.findall(html or ""))
        pages = sorted(_SITE + path for path in spots)
        seen: set[str] = set()
        for page, html in zip(pages, thread_imap(self._fetch.get, pages), strict=True):
            item = _video_object(html or "")
            if item is None:
                continue
            key = predisc_key(str(item.get("embedUrl") or ""))
            if not key or not key.startswith("yt:") or key in seen:
                continue
            seen.add(key)
            yield Candidate(
                title=decode_entities(str(item.get("name") or "")).strip(),
                angle_key=None,
                category=_category(str(item.get("description") or "")),
                source=self.name,
                source_page_url=page,
                target_url=f"https://www.youtube.com/watch?v={key[3:]}",
                predisc_key=key,
            )
