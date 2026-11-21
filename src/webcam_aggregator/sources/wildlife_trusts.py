from __future__ import annotations

import html as htmllib
import json
import re
from collections.abc import Iterator
from dataclasses import replace
from itertools import chain
from typing import override
from urllib.parse import quote, urljoin, urlsplit

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import HtmlScraperSource, extract_candidates, predisc_key
from .camsecure import hls_from_player

_SITE = "https://www.wildlifetrusts.org"
_INDEX = f"{_SITE}/webcams"
_TRUSTS = f"{_SITE}/wildlife-trusts"

# Index: each cam is a featured card whose one link is the cam page (or the YouTube
# video/channel itself) and whose h3 is the cam's name.
_CARD = re.compile(
    r'featured-cards-custom[^"]*"\s*>\s*<a href="([^"]+)"(.*?)</a>', re.S | re.I
)
_CARD_TITLE = re.compile(r"card__title[^>]*>([^<]+)<", re.I)
# The trusts page: each trust's own site is its card's schema.org `url` link.
_TRUST_SITE = re.compile(r'<a href="(https?://[^"]+)"[^>]*itemprop="url"', re.I)

# A trust site's sitemap holds thousands of pages; these are the ones named for a cam
# or for the osprey/peregrine nests the trusts stream. A page is only a candidate
# source: liveness keeps the ones whose embed is live, so a false positive here costs
# a fetch, never a dead playlist entry.
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_ROBOTS_SITEMAP = re.compile(r"^sitemap:\s*(\S+)", re.I | re.M)
_CAM_PATH = re.compile(
    r"web-?cam|cams?(?=[/_.-]|$)|camera|live-?stream|wildlife-windows"
    r"|watch-wildlife-online|osprey|peregrine",
    re.I,
)
# ...minus the article, species, event and how-to pages that mention a cam: they
# embed recordings, or only the trust's channel link in the site chrome.
_NOT_CAM_PATH = re.compile(
    r"/(?:blogs?|news|stories|events?|whats-on|our-events|species|wildlife-explorer"
    r"|content_block|resources?|support-us|appeals?|donate|shop|product|jobs|cy)/"
    r"|/\d{4}/\d{2}/|camera-trap|trail-?cam|how-to|competition|faqs?\b",
    re.I,
)
_MAX_SITEMAPS = 40  # per trust: bounds a runaway sitemap index

# Embeds the standard ladder misses on these pages. It reads only a page's first
# iframe (here usually the tag-manager one) and not the consent-gated or script
# forms, so every attribute holding a player URL is scanned instead.
_PLAYER_ATTR = re.compile(
    r'\b(?:src|data-src|data-src-cmplz|data-litespeed-src)="('
    r"(?:https?:)?//(?:www\.)?(?:youtube(?:-nocookie)?\.com/embed/|camstreamer\.com/embed/"
    r'|camsecure\.(?:co|uk)/|live\.streamdays\.com/)[^"]+)"',
    re.I,
)
_TAG = re.compile(r"<(?:iframe|script)\b[^>]*>", re.I)
_TAG_TITLE = re.compile(r'\btitle="([^"]+)"', re.I)
_GENERIC_TITLE = re.compile(r"^(?:youtube( video player)?|video|webcam|iframe)$", re.I)
_QUOTED_TITLE = re.compile(r'^youtube video\s*["“](.+)["”]$', re.I)  # one embed plugin
_YT_ID = re.compile(r"youtube\.com/watch\?v=([\w-]{11})$")
_OEMBED = "https://www.youtube.com/oembed?format=json&url="
_YT_EMBED = re.compile(
    r"youtube(?:-nocookie)?\.com/embed/(?!(?:videoseries|live_stream)\b)([\w-]{11})"
)
# A channel's "current live" embed (Scottish Wildlife Trust's ospreys).
_YT_LIVE_EMBED = re.compile(r"youtube\.com/embed/live_stream\?channel=([\w-]+)", re.I)
# The index links some cams straight at a channel's /streams or /videos tab.
_YT_CHANNEL_TAB = re.compile(
    r"^(https://www\.youtube\.com/(?:@[\w.-]+|channel/[\w-]+))/(?:streams|videos)/?$"
)
_YT_CHANNEL_LIVE = re.compile(r"youtube\.com/(?:channel/[\w-]+|@[\w.-]+)/live$", re.I)
# camstreamer's embed page wraps a YouTube embed of the camera's live stream.
_CAMSTREAMER_YT = re.compile(r"youtube\.com/embed/([\w-]{11})")
_PAGE_TITLE = re.compile(
    r'<meta property="og:title" content="([^"]+)"|<title>([^<]+)</title>', re.I
)
_TITLE_SUFFIX = re.compile(r"\s*[|–-]\s*[^|–-]*(?:Wildlife|Trust)[^|–-]*$", re.I)


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _cand(target: str, page_url: str, title: str = "") -> Candidate:
    return Candidate(
        title=title,
        angle_key=None,
        category=None,
        source=WildlifeTrustsSource.name,
        source_page_url=page_url,
        target_url=target,
        predisc_key=predisc_key(target),
    )


class WildlifeTrustsSource(HtmlScraperSource[str]):
    """The Wildlife Trusts' cams, from two lists: the national /webcams index (the
    curated cards, some on non-trust sites) and every trust's own sitemap, which
    carries cam pages the index never links. The national site sits behind a
    Cloudflare fingerprint gate, so this source's Fetcher impersonates a browser.

    Pages embed YouTube (often behind a consent placeholder), camstreamer (a wrapped
    YouTube, resolved here so it dedups on its `yt:` key), camsecure (resolved to its
    open HLS, as camscape does) and direct HLS. All wildlife -> category Animals."""

    name: str = "wildlife-trusts"
    _titles: dict[str, str]
    _direct: list[Candidate]
    _embedded: set[tuple[str, str]]  # (page, target) pairs read from a player embed

    @override
    def __init__(self, fetch: FetcherProtocol) -> None:
        super().__init__(fetch)
        self._titles = {}
        self._direct = []
        self._embedded = set()

    @override
    def _page_urls(self) -> list[str]:
        self._titles = {}
        self._direct = []
        self._embedded = set()
        pages: set[str] = set()
        for href, body in _CARD.findall(self._fetch.get(_INDEX) or ""):
            url = htmllib.unescape(href)
            m = _CARD_TITLE.search(body)
            title = _text(m.group(1)) if m else ""
            if "youtube.com/" in url:
                # Not a page to scrape: the card IS the video (or the channel).
                tab = _YT_CHANNEL_TAB.match(url)
                target = f"{tab.group(1)}/live" if tab else url
                self._direct.append(_cand(target, _INDEX, title))
                continue
            self._titles.setdefault(url, title)
            pages.add(url)
        sites = {
            f"https://{urlsplit(u).hostname}"
            for u in _TRUST_SITE.findall(self._fetch.get(_TRUSTS) or "")
        }
        for found in thread_map(self._cam_pages, sorted(sites)):
            pages.update(found)
        return sorted(pages)

    def _cam_pages(self, origin: str) -> list[str]:
        """The cam-named pages in one trust's sitemap (following a sitemap index).
        Most trusts serve it at /sitemap.xml; the rest name it in robots.txt."""
        queue, seen, pages = [f"{origin}/sitemap.xml"], set[str](), list[str]()
        while queue and len(seen) < _MAX_SITEMAPS:
            sitemap = queue.pop(0)
            if sitemap in seen:
                continue
            seen.add(sitemap)
            body = self._fetch.get(sitemap) or ""
            if not body and len(seen) == 1:
                robots = self._fetch.get(f"{origin}/robots.txt") or ""
                queue.extend(
                    u for u in _ROBOTS_SITEMAP.findall(robots) if u.startswith(origin)
                )
            locs = [htmllib.unescape(u) for u in _LOC.findall(body)]
            if "<sitemapindex" in body:
                queue.extend(u for u in locs if u.startswith(origin))
                continue
            for u in locs:
                path = urlsplit(u).path
                if _CAM_PATH.search(path) and not _NOT_CAM_PATH.search(path):
                    pages.append(u)
        return pages

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        title = self._titles.get(url, "")
        if not title:
            m = _PAGE_TITLE.search(html)
            title = _TITLE_SUFFIX.sub(
                "", _text((m.group(1) or m.group(2)) if m else "")
            )
        return "Animals", title

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return cand.title or ctx

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        found: dict[str, Candidate] = {}

        def add(target: str, title: str = "") -> None:
            if target not in found:
                found[target] = _cand(target, url, title)

        for tag in _TAG.findall(html):
            m = _PLAYER_ATTR.search(tag)
            if not m:
                continue
            target = urljoin(url, htmllib.unescape(m.group(1)))
            tm = _TAG_TITLE.search(tag)
            title = _text(tm.group(1)) if tm else ""
            quoted = _QUOTED_TITLE.match(title)
            title = quoted.group(1) if quoted else title
            title = "" if _GENERIC_TITLE.match(title) else title
            for resolved in self._resolve_embed(target):
                add(resolved, title)
                self._embedded.add((url, resolved))
        for c in extract_candidates(html, page_url=url, source=self.name):
            add(c.target_url)
        cands = list(found.values())
        # Every trust page's chrome links the trust's YouTube channel; it is only
        # the cam when the page has nothing else.
        real = [c for c in cands if not _YT_CHANNEL_LIVE.search(c.target_url)]
        cands = real or cands
        if len(cands) > 1:
            # Several cams on one page, often other projects' (the Derby blog embeds
            # Rutland's and Holkham's): an untitled YouTube one takes the video's own
            # title, and any still untitled are numbered after the page. angle_key
            # keeps each cam's stable id distinct.
            titles = thread_map(self._yt_title, cands)
            untitled = titles.count("")
            page = self._page_meta(html, url)[1]
            n = 0
            for i, (c, t) in enumerate(zip(cands, titles)):
                if not t and untitled > 1:
                    n += 1
                    t = f"{page} {n}"
                cands[i] = replace(c, angle_key=str(i), title=t)
        return cands

    def _yt_title(self, cand: Candidate) -> str:
        if cand.title or not _YT_ID.search(cand.target_url):
            return cand.title
        body = self._fetch.get(_OEMBED + quote(cand.target_url, safe="")) or ""
        try:
            title = json.loads(body).get("title", "")
        except ValueError:
            return ""
        return title if isinstance(title, str) else ""

    def _resolve_embed(self, target: str) -> list[str]:
        """An embed URL -> the target(s) the extractors serve."""
        live = _YT_LIVE_EMBED.search(target)
        if live:
            return [f"https://www.youtube.com/channel/{live.group(1)}/live"]
        video = _YT_EMBED.search(target)
        if video:
            return [f"https://www.youtube.com/watch?v={video.group(1)}"]
        if "youtube" in target:
            return []  # a playlist: recordings, never a live cam
        host = urlsplit(target).hostname or ""
        if host.endswith("camstreamer.com"):
            m = _CAMSTREAMER_YT.search(self._fetch.get(target) or "")
            return [f"https://www.youtube.com/watch?v={m.group(1)}"] if m else [target]
        if host.startswith("camsecure."):
            # The m3u8 is the key the camsecure source shares; it is not derivable
            # from the player URL, so read it from the player page.
            return [hls_from_player(self._fetch.get(target) or "", target) or target]
        # streamdays: a tokened player with no extractor yet — kept as a visible
        # no-extractor drop rather than hidden.
        return [target]

    @override
    def discover(self) -> Iterator[Candidate]:
        # A target reached from several pages is one cam, named by the page that
        # embeds it, else by an index card, else by whichever page links it.
        # Cards that are YouTube links come after the pages, which read them.
        best: dict[str, tuple[int, Candidate]] = {}
        for c in chain(super().discover(), self._direct_cands()):
            rank = self._rank(c)
            if rank == 2 and _YT_CHANNEL_LIVE.search(c.target_url):
                # A bare link to a channel's current live, on a page we found by its
                # URL: usually site chrome, and when that channel IS live it is one
                # of the cams already listed by video id (Rutland's blog page).
                continue
            if c.target_url not in best or rank < best[c.target_url][0]:
                best[c.target_url] = (rank, c)
        for _, c in best.values():
            yield c

    def _rank(self, c: Candidate) -> int:
        if (c.source_page_url, c.target_url) in self._embedded:
            return 0
        if c.source_page_url == _INDEX or c.source_page_url in self._titles:
            return 1
        return 2

    def _direct_cands(self) -> Iterator[Candidate]:
        for c in self._direct:
            yield replace(c, category="Animals")
