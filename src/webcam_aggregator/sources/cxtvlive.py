from __future__ import annotations

import re
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, with_location

_SLUG = re.compile(r"live-camera/([^<\s]+)")
_TITLE = re.compile(r"<h1[^>]*>([^<]{1,120})</h1>")
_CATEGORY = re.compile(r"cameras/category/([a-z0-9-]+)")


class CxtvliveSource(HtmlScraperSource[str]):
    name: str = "cxtvlive"

    @override
    def _page_urls(self) -> list[str]:
        sm = self._fetch.get("https://www.cxtvlive.com/sitemap.xml") or ""
        slugs = list(dict.fromkeys(_SLUG.findall(sm)))
        return ["https://www.cxtvlive.com/live-camera/" + s for s in slugs]

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        tm = _TITLE.search(html)
        cm = _CATEGORY.search(html)
        category = cm.group(1).replace("-", " ").title() if cm else None
        # ctx is the title base: the page <h1>, or a prettified slug as a fallback.
        slug = url.split("/live-camera/", 1)[-1]
        title_base = tm.group(1).strip() if tm else slug.replace("-", " ").title()
        return category, title_base

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return with_location(ctx, url, drop=category or "")
