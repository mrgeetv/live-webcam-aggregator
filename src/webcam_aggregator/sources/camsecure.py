"""CamSecure source: sitemap -> cam pages -> player iframe -> direct HLS."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, override
from urllib.parse import unquote, urljoin, urlsplit

from webcam_aggregator.hosts import on_host
from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import (
    HtmlScraperSource,
    decode_entities,
    predisc_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_BASE = "https://www.camsecure.co.uk"
_SITEMAP = f"{_BASE}/sitemap.xml"

# Whether a sitemap page is a cam is decided by the iframe/HLS check below, NOT its URL
# — many cam pages have no "webcam" in the name. `_SKIP` only drops pages that DO embed
# a demo player but aren't a single cam (homepage, the demo index, product/info pages).
_SKIP = (
    "hosting",
    "features",
    "faq",
    "drivers",
    "ipnetwork",
    "map",
    "camsecure_webcam",
    "webcam_live_clock",  # clock-overlay widget demo
    "live_demo",  # the demo index page
    "/index.html",
    "site_information",
    "/players/",
    "cctv",
    "software",
)
_LOC = re.compile(r"<loc>([^<]+)</loc>")
# Each cam page embeds its player in an iframe on camsecure.co/.uk (`httpswebcam/…`);
# that player page is a video.js with a direct `/HLS/<name>.m3u8` (open CDN — segments
# need no token/Referer; the player PAGE serves a decoy without `Referer:
# camsecure.co.uk`, so the hosts are in `_REFERER_HOSTS`).
_PLAYER_IFRAME = re.compile(
    r'<iframe[^<>]+src="(https?://camsecure\.[a-z.]+/httpswebcam/[^"]+)"', re.IGNORECASE
)
# Each src is read whole and then tested for `.m3u8`: a body before a required
# `.m3u8` retries every `.m3u8` in an unclosed value (see sources.base._URL_RUN).
_SOURCE_SRC = re.compile(r'<source[^<>]+src="([^"]+)"', re.IGNORECASE)
_TITLE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
# "Brixham Harbour Live Streaming Webcam" -> "Brixham Harbour"; titles that LEAD with
# boilerplate ("Live Coastal Shipping Webcam from Coastwatch Redcar") fall back to the
# "from <place>" tail, then to the URL filename.
_BOILER = re.compile(
    r"(?<!\s)\s*\b(?:live\s+)?(?:streaming\s+)?web\s?cam\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_LEADING_BOILER = re.compile(r"^(?:live|streaming|coastal)\b", re.IGNORECASE)
_FROM = re.compile(r"\bfrom\s+(.+)", re.IGNORECASE)


def player_iframe(html: str) -> str | None:
    """Return the camsecure player iframe a cam page embeds, if any."""
    m = _PLAYER_IFRAME.search(html)
    return m.group(1) if m else None


def is_camsecure(url: str) -> bool:
    """Return whether `url` is on camsecure's site or one of its player hosts."""
    return on_host(url, "camsecure.co.uk", "camsecure.co", "camsecure.uk")


def hls_from_player(html: str, player_url: str) -> str | None:
    """Return the absolute HLS URL in a camsecure player page's video.js `<source>`."""
    src = next((s for s in _SOURCE_SRC.findall(html) if ".m3u8" in s), None)
    return urljoin(player_url, src) if src else None


def _name_from_url(url: str) -> str:
    slug = unquote(url.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"\.html?$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"(?i)(?<![_\- ])[_\- ]*(?:large|webcam|cam)\d*$", "", slug)
    slug = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)  # camelCase -> spaces
    return re.sub(r"[_\-\s]+", " ", slug).strip().title()


def _title_of(html: str, url: str) -> str:
    m = _TITLE.search(html)
    raw = decode_entities(m.group(1)).strip() if m else ""
    t = _BOILER.sub("", raw).strip()
    if not t or _LEADING_BOILER.match(t):
        # The "from <place>" tail is on the title's last line, so only that line is
        # searched, and one greedy match there reaches its end.
        fm = _FROM.search(raw.rsplit("\n", 1)[-1])
        t = fm.group(1).strip() if fm else ""
    return t or _name_from_url(url)


class CamSecureSource(HtmlScraperSource[str]):
    """CamSecure: sitemap -> per-cam pages -> the player iframe's direct HLS.

    A page is a cam iff it embeds a camsecure player iframe whose player page has an HLS
    `<source>`; the player page is the second hop, fetched in the page's own step.
    """

    name: str = "camsecure"
    interval_hours: float = 6

    @override
    def _page_urls(self) -> list[str]:
        sm = self._fetch.get(_SITEMAP) or ""
        return sorted(
            {
                loc.replace(" ", "%20")
                for loc in _LOC.findall(sm)
                if on_host(loc, "camsecure.co.uk")
                and urlsplit(loc).path.strip("/")  # not the homepage
                and not any(s in loc.lower() for s in _SKIP)
            }
        )

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        return None, _title_of(html, url)  # no category on the site -> "Other"

    @override
    def _candidates(self, html: str, url: str) -> Iterator[Candidate]:
        player = player_iframe(html)
        if not player:
            return
        m3u8 = hls_from_player(self._fetch.get(player) or "", player)
        # rtsp.me's stub manifest passes liveness but its segments 404
        if not m3u8 or "rtsp.me" in m3u8:
            return
        yield Candidate(
            title="",
            angle_key=None,
            category=None,
            source=self.name,
            source_page_url=url,
            target_url=m3u8,
            predisc_key=predisc_key(m3u8),
        )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
