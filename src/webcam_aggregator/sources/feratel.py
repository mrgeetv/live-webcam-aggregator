from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, predisc_key, with_location_parts

# feratel.com's own portal lists every public cam: the sitemap carries the
# /en/webcams/<country>/<region>/<slug>-<cam id> detail pages (the one- and
# two-segment paths are country/region index pages, not cams). The page's OWN cam
# id is the one its URL ends in, never read off the markup: the main player and
# the "nearby cams" carousel are both plain src= iframes on the same webtv URL.
_SITEMAP = "https://www.feratel.com/sitemap-0.xml"
_LOC = re.compile(
    r"<loc>(https://www\.feratel\.com/en/webcams/[^/<]+/[^/<]+/[^/<]+-\d+)</loc>"
)
_TITLE = re.compile(r"<title>([^<]*)</title>")
# Read from the title text, apart from the tag: inside one pattern, an unclosed
# <title> makes every dash in it rescan to the end of the page.
_NAME = re.compile(r"^Webcams in ([^<]*?)-\s*Livecams")


class FeratelSource(HtmlScraperSource[str]):
    """feratel.com portal -> ~1000 Alpine/European cams (ski, lake, town views).
    The candidate is the canonical webtv.feratel.com/webtv/?cam=<id> player page,
    resolved by the existing metatag extractor (og:video -> a panorama-sweep MP4
    that serve_stream 302s to the player; an offline or still-image-only cam has
    no og:video content URL and drops as resolve-failed). Near-all are place
    views -> blanket "Travel & Events", matching where the geo title fallback
    would put them. ctx is the cam name from the page <title>."""

    name: str = "feratel"

    @override
    def _page_urls(self) -> list[str]:
        xml = self._fetch.get(_SITEMAP) or ""
        return sorted(set(_LOC.findall(xml)))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        n = next((n for t in _TITLE.findall(html) if (n := _NAME.match(t))), None)
        title = unescape(n.group(1)).strip() if n else ""
        if not title:
            # last resort: the URL slug minus its cam id ("gschnitz-zentrum-5751")
            slug = url.rsplit("/", 1)[-1].rsplit("-", 1)[0]
            title = slug.replace("-", " ").title()
        return "Travel & Events", title

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        cam_id = url.rsplit("-", 1)[-1]  # _LOC only admits URLs ending in -<id>
        target = f"https://webtv.feratel.com/webtv/?cam={cam_id}"
        yield Candidate(
            title="",
            angle_key=None,
            category=None,
            source=self.name,
            source_page_url=url,
            target_url=target,
            # base.predisc_key keys feratel URLs on the cam id, which merges our
            # copy with the differently-shaped embeds cxtvlive/camscape pick up
            predisc_key=predisc_key(target),
        )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        # the URL path is /en/webcams/<country>/<region>/<slug>-<id>: only the
        # country/region segments are geo (the fixed prefix would otherwise
        # leak "Webcams" into the suffix, and the slug repeats the name)
        segs = url.split("/")
        parts = [s.replace("-", " ").title() for s in segs[-3:-1]]
        return with_location_parts(ctx, parts)
