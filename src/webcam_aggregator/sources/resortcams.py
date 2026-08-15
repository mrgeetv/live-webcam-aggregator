from __future__ import annotations

import re
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource

# WordPress sitemap for the `webcams` post type — the complete cam list. The bare
# /webcams/ index has nothing after the slash and never matches.
_SITEMAP = "https://www.resortcams.com/webcams-sitemap1.xml"
_LOC = re.compile(r"<loc>(https://www\.resortcams\.com/webcams/[^<]+)</loc>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_SITE_TAIL = re.compile(r"\s*[–-]\s*Resort Cams\s*$", re.I)


class ResortCamsSource(HtmlScraperSource[str]):
    """resortcams.com: ~100 US-Southeast ski/mountain/beach/town cams (plus a few
    outliers like Bar Harbor and Costa Rica, so no location suffix). Each page's
    player carries an open, direct Wowza HLS
    (stream.resortcams.com/live/<name>.stream/playlist.m3u8 — no token/Referer;
    segment names are stream-instance-scoped, not per-viewer) that the shared
    ladder extracts and DirectHls serves. The site's category pages all list every
    cam, so there is no per-cam type -> None (the title fallback categorises the
    "ski"/"downtown"/geo titles it can); pages with no static player (a JS-built
    or image-refresh cam) yield nothing and drop. ctx is the page <title> with the
    site boilerplate tail stripped."""

    name: str = "resortcams"

    @override
    def _page_urls(self) -> list[str]:
        xml = self._fetch.get(_SITEMAP) or ""
        return sorted(set(_LOC.findall(xml)))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _TITLE.search(html)
        title = _SITE_TAIL.sub("", unescape(m.group(1)).strip()) if m else ""
        if not title:
            # last resort: the URL slug ("blowing-rock")
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        return None, title

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
