from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, with_location_parts

# The site serves no sitemap, so the `webcam` post type's WP REST collection is
# the cam index: ~32 cams, well inside WP's per_page ceiling of 100, so one call
# is the whole list. It carries the page url and title but no player url — that
# is only ever in the page HTML, so the per-cam fetch below stays.
_INDEX = "https://livefromiceland.is/wp-json/wp/v2/webcam?per_page=100"
_CAM_PREFIX = "https://livefromiceland.is/webcam/"
# The player iframe is lazy-loaded (src="about:blank", the real URL in
# data-litespeed-src), so the shared ladder's iframe rule can't be trusted with it —
# grep the player URL straight out of the static HTML instead.
_PLAYER = re.compile(
    r"https://g0\.ipcamlive\.com/player/player\.php\?alias=[A-Za-z0-9_-]+"
)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_SITE_TAIL = re.compile(r"\s*[–-]\s*Live From Iceland\s*$", re.I)


class LiveFromIcelandSource(HtmlScraperSource[str]):
    """livefromiceland.is: ~32 cams (volcanoes, glaciers, Reykjavík, harbours), one
    ipcamlive player per page, served by the existing ipcamlive extractor. ctx is the
    page <title> with the site boilerplate tail stripped; the site has no per-cam
    category -> None (the title fallback categorises what it can)."""

    name: str = "livefromiceland"

    @override
    def _page_urls(self) -> list[str]:
        try:
            items = json.loads(self._fetch.get(_INDEX) or "")
        except ValueError:
            return []  # not JSON (an error page) -> the empty-guard reports it
        links = {str(it.get("link", "")) for it in items if isinstance(it, dict)}
        return sorted(u for u in links if u.startswith(_CAM_PREFIX))

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
