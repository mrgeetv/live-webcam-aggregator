from __future__ import annotations

import re
from collections.abc import Iterator

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate

# Ozolio (resort/beach cams, mostly Hawaii) publishes one /explore/<CID> page per cam
# via its Yoast cameras sitemap. The page builds its player client-side against
# relay.ozolio.com's session API (see extractors.ozolio), so the only thing the HTML
# is good for is the server-rendered <title> — discovery therefore stays a
# one-fetch-per-cam title scrape and leaves the session dance to liveness/serve time.
_SITEMAP = "https://www.ozolio.com/cameras-sitemap.xml"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_EXPLORE = re.compile(r"https?://www\.ozolio\.com/explore/[A-Z0-9]+$")
# "Keauhou Bay, Hawaii - Webcam - Ozolio" -> "Keauhou Bay, Hawaii"
_TITLE = re.compile(r"<title>([^<]+?)\s*-\s*Webcam\s*-\s*Ozolio\s*</title>", re.I)


class OzolioSource:
    """Ozolio's cameras sitemap -> per-cam /explore/<CID> pages. The candidate target
    is the explore page itself: the stream URL only exists inside a per-session relay
    exchange, so OzolioResolver mints it fresh from the CID at resolve time and there
    is no stable stream URL to merge on (predisc_key None). No category -> "Other"."""

    name: str = "ozolio"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        sm = self._fetch.get(_SITEMAP) or ""
        pages = sorted(
            {loc.strip() for loc in _LOC.findall(sm) if _EXPLORE.match(loc.strip())}
        )
        for page, html in zip(pages, thread_map(self._fetch.get, pages)):
            m = _TITLE.search(html or "")
            if not m:
                continue  # fetch failed, or the page isn't a rendered cam page
            yield Candidate(
                title=m.group(1).strip(),
                angle_key=None,
                category=None,  # nothing on the page maps to a category
                source=self.name,
                source_page_url=page,
                target_url=page,
                predisc_key=None,
            )
