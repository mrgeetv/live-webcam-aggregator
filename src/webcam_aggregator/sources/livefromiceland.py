from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, with_location_parts

# WordPress sitemap for the `webcam` post type — the complete cam list.
_SITEMAP = "https://livefromiceland.is/wp-sitemap-posts-webcam-1.xml"
_LOC = re.compile(r"<loc>(https://livefromiceland\.is/webcam/[^<]+)</loc>")
# The player iframe is lazy-loaded (src="about:blank", the real URL in
# data-litespeed-src), so the shared ladder's iframe rule can't be trusted with it —
# grep the player URL straight out of the static HTML instead.
_PLAYER = re.compile(
    r"https://g0\.ipcamlive\.com/player/player\.php\?alias=[A-Za-z0-9_-]+"
)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_SITE_TAIL = re.compile(r"\s*[–-]\s*Live From Iceland\s*$", re.I)


class LiveFromIcelandSource(HtmlScraperSource[str]):
    """livefromiceland.is: ~30 cams (volcanoes, glaciers, Reykjavík, harbours), one
    ipcamlive player per page, served by the existing ipcamlive extractor. ctx is the
    page <title> with the site boilerplate tail stripped; the site has no per-cam
    category -> None (the title fallback categorises what it can)."""

    name: str = "livefromiceland"

    @override
    def _page_urls(self) -> list[str]:
        xml = self._fetch.get(_SITEMAP) or ""
        return sorted(set(_LOC.findall(xml)))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _TITLE.search(html)
        title = _SITE_TAIL.sub("", unescape(m.group(1)).strip()) if m else ""
        if not title:
            # last resort: the URL slug ("vestmannaeyjar-heimaklettur")
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        return None, title

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        targets = list(dict.fromkeys(_PLAYER.findall(html)))
        multi = len(targets) > 1
        for i, target in enumerate(targets):
            yield Candidate(
                title="",
                angle_key=str(i) if multi else None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                predisc_key=None,  # an ipcamlive alias has nothing to merge on
            )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return with_location_parts(ctx, ["Iceland"])
