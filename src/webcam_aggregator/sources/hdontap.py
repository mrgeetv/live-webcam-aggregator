from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from typing import override

from ..extractors.hdontap import stream_src
from ..models import Candidate
from .base import HtmlScraperSource, extract_candidates

_SITEMAP = "https://hdontap.com/sitemap.xml"
# Cam pages are /stream/<id>/<slug>/. Their /embed/ twins sit in the sitemap's
# <video:player_loc> tags, so the <loc> scan never sees them — but the numeric id is
# still the dedupe key, because a cam can be listed under more than one slug.
_LOC = re.compile(r"<loc>(https://hdontap\.com/stream/(\d+)/[^<]*)</loc>")
_TITLE = re.compile(r"<title>([^<]*)</title>", re.I)
# "Carmel-By-The-Sea Live Webcam - HDOnTap" -> "Carmel-By-The-Sea"; the boilerplate
# tail is stripped once, anchored at the end, so a mid-name "Cam" ("PA Peregrine
# Falcon Cam Live Webcam") survives.
_SUFFIX = re.compile(r"\s*[-|]\s*HDOnTap\s*$", re.I)
_BOILER = re.compile(
    r"\s*\b(?:live\s+)?(?:streaming\s+|beach\s+)?(?:web\s?cam|cam)\s*$", re.I
)


def _title_of(html: str, url: str) -> str:
    m = _TITLE.search(html)
    raw = _SUFFIX.sub("", unescape(m.group(1)).strip()) if m else ""
    if not raw:  # no <title> -> prettified slug
        raw = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
    t = _BOILER.sub("", raw).strip()
    return t or raw


class HdOnTapSource(HtmlScraperSource[str]):
    """HDOnTap's sitemap lists every cam page (/stream/<id>/<slug>/). A page with a
    `player-data` blob is a native-HLS cam: its candidate is the PAGE URL, resolved
    fresh at serve time by `HdontapResolver`, because the blob's HLS URL carries a
    per-page-fetch t/e token (which also means an `hls:` predisc_key could never
    merge — hence None). Pages without the blob fall back to the standard ladder
    (roughly a quarter are plain YouTube embeds; an offline page carries neither and
    yields nothing). The site has no category taxonomy -> None, so the title-keyword
    fallback picks up the beaches/wildlife cams."""

    name: str = "hdontap"

    @override
    def _page_urls(self) -> list[str]:
        sm = self._fetch.get(_SITEMAP) or ""
        seen: set[str] = set()
        out: list[str] = []
        for url, cam_id in sorted(set(_LOC.findall(sm))):
            if cam_id not in seen:
                seen.add(cam_id)
                out.append(url)
        return out

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        return None, _title_of(html, url)

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        if stream_src(html):
            yield Candidate(
                title="",
                angle_key=None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=url,
                predisc_key=None,  # token URL is per-fetch; nothing stable to merge on
            )
        else:
            yield from extract_candidates(html, page_url=url, source=self.name)
