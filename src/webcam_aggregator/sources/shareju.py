from __future__ import annotations

import json
import re
from collections.abc import Iterator

from ..fetch import FetcherProtocol
from ..models import Candidate
from .base import predisc_key

# Share-Ju (its-i.com) lists every cam on the ONE /camera index as a schema.org
# ItemList of VideoObjects (name, YouTube embedUrl, per-cam page url, address, and
# the cam's tags inside the description). Category pages (/camera/<tag>) are strict
# subsets of it, and the per-cam detail pages build their embed in JS with nothing
# in the static HTML — so the index JSON-LD is both the cheapest AND the only
# reliable source: one fetch covers the whole site (~475 cams).
_INDEX = "https://its-i.com/camera"

_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
# Every name is wrapped in the same boilerplate:
# 【ライブカメラ映像再生】<cam name>【Share-Ju】公開カメラ
_NAME_PREFIX = re.compile(r"^【[^】]*】")
_NAME_SUFFIX = re.compile(r"【Share-Ju】.*$")
# The cam's tags sit in the description as このカメラに付されているタグ（tag tag …）.
_TAG_BLOCK = re.compile(r"このカメラに付されているタグ（([^）]*)）")

# tag -> a categories._MAP key (mapped to the unified taxonomy at build). The site
# orders each cam's tags specific -> generic, so the FIRST tag that maps wins.
# Town/scenic/tourist tags stay unmapped on purpose: those cams carry a " — <geo>"
# title suffix, so the title fallback files them under Travel & Events.
_TAG_CATEGORY: dict[str, str] = {
    "河川": "Rivers Lakes",
    "水害監視カメラ": "Rivers Lakes",
    "交通情報": "Traffic",
    "交通状況": "Traffic",
    "道路": "Traffic",
    "渋滞": "Traffic",
    "交通": "Traffic",
    "鉄道": "Trains",
    "新幹線": "Trains",
    "駅": "Trains",
    "空港": "Airports",
    "飛行機": "Airports",
    "港": "Ports",
    "海": "Beaches",
    "海岸": "Beaches",
    "ビーチ": "Beaches",
    "スキー": "Ski Resorts",
    "スノーボード": "Ski Resorts",
    "スキー場": "Ski Resorts",
    "ゲレンデ": "Ski Resorts",
    "富士山": "Mountains",
    "山": "Mountains",
    "城": "Castles",
    "神社": "Churches",
    "寺": "Churches",
}


def _title(item: dict[str, object]) -> str:
    name = _NAME_SUFFIX.sub(
        "", _NAME_PREFIX.sub("", str(item.get("name") or ""))
    ).strip()
    # Geo suffix built by hand: base.with_location_parts normalises to [a-z0-9], which
    # erases Japanese text entirely, so its dedup/containment logic can't run here.
    # The site truncates the locality with a trailing ellipsis (港区…) — strip it.
    addr: object = item.get("locationCreated")
    if isinstance(addr, dict):
        addr = addr.get("address")
    locality = region = ""
    if isinstance(addr, dict):
        locality = str(addr.get("addressLocality") or "").rstrip("…").strip()
        region = str(addr.get("addressRegion") or "").strip()
    parts = [p for p in (locality, region) if p and p not in name]
    return f"{name} — {', '.join(parts)}" if parts else name


def _category(item: dict[str, object]) -> str | None:
    m = _TAG_BLOCK.search(str(item.get("description") or ""))
    tags = m.group(1).split() if m else []
    return next((_TAG_CATEGORY[t] for t in tags if t in _TAG_CATEGORY), None)


class SharejuSource:
    """Share-Ju (its-i.com) Japanese live-cam directory, via the /camera index's
    JSON-LD ItemList. Almost every cam is a Japanese-titled YouTube live an English
    SEARCH_QUERY never surfaces; the few third-party iframes stay visible
    `no-extractor` drops."""

    name: str = "shareju"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        html = self._fetch.get(_INDEX)
        if not html:
            return
        seen: set[str] = set()
        for block in _LD_JSON.findall(html):
            try:
                # strict=False: the blob carries raw control characters inside
                # strings (a literal newline in one embedUrl), which strict JSON rejects.
                data = json.loads(block, strict=False)
            except ValueError:
                continue
            if not isinstance(data, dict) or data.get("@type") != "ItemList":
                continue
            elements = data.get("itemListElement")
            if not isinstance(elements, list):
                continue
            for e in elements:
                item = e.get("item") if isinstance(e, dict) else None
                if not isinstance(item, dict) or item.get("@type") != "VideoObject":
                    continue
                embed = str(item.get("embedUrl") or "").strip()
                if not embed:
                    continue
                key = predisc_key(embed)
                # Normalise YouTube embeds to watch URLs (the form the yt-dlp path and
                # cross-source `yt:` dedup expect); anything else rides as-is.
                target = (
                    f"https://www.youtube.com/watch?v={key[3:]}"
                    if key and key.startswith("yt:")
                    else embed
                )
                if target in seen:
                    continue
                seen.add(target)
                yield Candidate(
                    title=_title(item),
                    angle_key=None,
                    category=_category(item),
                    source=self.name,
                    source_page_url=str(item.get("url") or "").strip() or target,
                    target_url=target,
                    predisc_key=key,
                )
