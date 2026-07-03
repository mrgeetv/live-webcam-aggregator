from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import unquote, urljoin, urlsplit

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import predisc_key

_BASE = "https://www.camsecure.co.uk"
_SITEMAP = f"{_BASE}/sitemap.xml"

# Whether a sitemap page is a cam is decided by the iframe/HLS check below, NOT its URL —
# many cam pages have no "webcam" in the name. `_SKIP` only drops pages that DO embed a
# demo player but aren't a single cam (homepage, the demo index, product/info pages).
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
# Each cam page embeds its player in an iframe on camsecure.co/.uk (`httpswebcam/…`); that
# player page is a video.js with a direct `/HLS/<name>.m3u8` (open CDN — segments need no
# token/Referer; the player PAGE serves a decoy without `Referer: camsecure.co.uk`, so the
# hosts are in `_REFERER_HOSTS`).
_PLAYER_IFRAME = re.compile(
    r'<iframe[^>]+src="(https?://camsecure\.[a-z.]+/httpswebcam/[^"]+)"', re.I
)
_HLS_SRC = re.compile(r'<source[^>]+src="([^"]+\.m3u8[^"]*)"', re.I)
_TITLE = re.compile(r"<title>([^<]+)</title>", re.I)
# "Brixham Harbour Live Streaming Webcam" -> "Brixham Harbour"; titles that LEAD with
# boilerplate ("Live Coastal Shipping Webcam from Coastwatch Redcar") fall back to the
# "from <place>" tail, then to the URL filename.
_BOILER = re.compile(r"\s*\b(?:live\s+)?(?:streaming\s+)?web\s?cam\b.*$", re.I)
_LEADING_BOILER = re.compile(r"^(?:live|streaming|coastal)\b", re.I)
_FROM = re.compile(r"\bfrom\s+(.+?)\s*$", re.I)


def _name_from_url(url: str) -> str:
    slug = unquote(url.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"\.html?$", "", slug, flags=re.I)
    slug = re.sub(r"(?i)[_\- ]*(?:large|webcam|cam)\d*$", "", slug)
    slug = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)  # camelCase -> spaces
    return re.sub(r"[_\-\s]+", " ", slug).strip().title()


def _title_of(html: str, url: str) -> str:
    m = _TITLE.search(html)
    raw = m.group(1).strip() if m else ""
    t = _BOILER.sub("", raw).strip()
    if not t or _LEADING_BOILER.match(t):
        fm = _FROM.search(raw)
        t = fm.group(1).strip() if fm else ""
    return t or _name_from_url(url)


class CamSecureSource:
    """CamSecure's sitemap -> per-cam pages -> the player iframe's direct HLS. A page is a
    cam iff it embeds a camsecure player iframe whose player page has an HLS `<source>`;
    both hops are fetched concurrently."""

    name: str = "camsecure"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        sm = self._fetch.get(_SITEMAP) or ""
        pages = sorted(
            {
                loc.replace(" ", "%20")
                for loc in _LOC.findall(sm)
                if "camsecure.co.uk" in loc.lower()
                and urlsplit(loc).path.strip("/")  # not the homepage
                and not any(s in loc.lower() for s in _SKIP)
            }
        )
        # hop 1: cam pages -> (page, title, player url) for those embedding a player
        found: list[tuple[str, str, str]] = []
        for page, html in zip(pages, thread_map(self._fetch.get, pages)):
            ifr = _PLAYER_IFRAME.search(html or "")
            if ifr:
                found.append((page, _title_of(html or "", page), ifr.group(1)))
        # hop 2: player pages -> direct HLS (concurrent)
        seen: set[str] = set()
        players = thread_map(self._fetch.get, [f[2] for f in found])
        for (page, title, player), pg in zip(found, players):
            src = _HLS_SRC.search(pg or "")
            if not src:
                continue
            m3u8 = urljoin(player, src.group(1))
            if "rtsp.me" in m3u8 or m3u8 in seen:
                continue  # rtsp.me stub passes liveness but 404s; or a dupe stream
            seen.add(m3u8)
            yield Candidate(
                title=title,
                angle_key=None,
                category=None,  # no category in the feed -> "Other"
                source=self.name,
                source_page_url=page,
                target_url=m3u8,
                predisc_key=predisc_key(m3u8),
            )
