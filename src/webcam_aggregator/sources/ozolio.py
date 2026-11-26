"""Ozolio source: cameras sitemap -> explore pages, resolved at serve time."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, override

from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import HtmlScraperSource, decode_entities

if TYPE_CHECKING:
    from collections.abc import Iterator

# Ozolio (resort/beach cams, mostly Hawaii) publishes one /explore/<CID> page per cam
# via its Yoast cameras sitemap. The page builds its player client-side against
# relay.ozolio.com's session API (see extractors.ozolio), so the only thing the HTML
# is good for is the server-rendered <title> — discovery therefore stays a
# one-fetch-per-cam title scrape and leaves the session dance to liveness/serve time.
_SITEMAP = "https://www.ozolio.com/cameras-sitemap.xml"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_EXPLORE = re.compile(r"https?://www\.ozolio\.com/explore/[A-Z0-9]+$")
# "Keauhou Bay, Hawaii - Webcam - Ozolio" -> "Keauhou Bay, Hawaii"
_TITLE = re.compile(r"<title>([^<]+?)-\s*Webcam\s*-\s*Ozolio\s*</title>", re.IGNORECASE)


class OzolioSource(HtmlScraperSource[str]):
    """Ozolio: cameras sitemap -> per-cam /explore/<CID> pages.

    The candidate target is the explore page itself: the stream URL only exists inside a
    per-session relay exchange, so OzolioResolver mints it fresh from the CID at resolve
    time and there is no stable stream URL to merge on (predisc_key None). A page with
    no Ozolio `<title>` (an error or consent page) drops. No category -> "Other".
    """

    name: str = "ozolio"

    @override
    def _page_urls(self) -> list[str]:
        sm = self._fetch.get(_SITEMAP) or ""
        return sorted(
            {loc.strip() for loc in _LOC.findall(sm) if _EXPLORE.match(loc.strip())}
        )

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _TITLE.search(html)
        # nothing on the page maps to a category
        return None, decode_entities(m.group(1)).strip() if m else ""

    @override
    def _candidates(self, html: str, url: str) -> Iterator[Candidate]:
        if _TITLE.search(html):
            yield Candidate(
                title="",
                angle_key=None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=url,
                predisc_key=None,
            )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
