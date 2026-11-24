"""The Wildlife Trusts source: the national index plus every trust's sitemap."""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urljoin, urlsplit

from webcam_aggregator.fetch import FetcherProtocol, thread_imap, thread_map
from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import (
    decode_entities,
    extract_candidates,
    predisc_key,
)
from webcam_aggregator.sources.camsecure import hls_from_player

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger("webcam-aggregator.wildlife-trusts")

_SITE = "https://www.wildlifetrusts.org"
_INDEX = f"{_SITE}/webcams"
_TRUSTS = f"{_SITE}/wildlife-trusts"
_CATEGORY = "Animals"

# Index: each cam is a featured card whose link is the cam page (or the YouTube video
# or channel itself) and whose h3 is the cam's name. Cards are cut out with str.split
# and read a bounded slice at a time, so a hostile page cannot make a regex backtrack
# across the whole body.
_CARD_MARK = "paragraph--type--featured-cards-custom"
_CARD_HREF = re.compile(r'<a href="([^"<>]+)"')
# Tag keyword in a lookahead, tag skipped possessively (why: airportwebcams._TITLE).
_CARD_TITLE = re.compile(r"<(?=[^<>]*?card__title)[^<>]*+>([^<]+)<", re.IGNORECASE)
# The trusts page: each trust's own site is its card's schema.org `url` link.
_TRUST_SITE = re.compile(
    r'<a href="(https?://[^"<>]+)"[^<>]*itemprop="url"', re.IGNORECASE
)

# A trust site's sitemap holds thousands of pages; these are the ones named for a cam
# or for the osprey/peregrine nests the trusts stream. A page is only a place to look:
# liveness keeps the cams that are live, so a false positive costs a fetch, never a
# dead playlist entry.
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[\s*)?([^<\s\]]+)")
_ROBOTS_SITEMAP = re.compile(r"^sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_CAM_PATH = re.compile(
    r"web-?cam|cams?\d*(?=[/_.-]|$)|camera|live-?stream|(?:^|[/_-])live(?=[/_.-]|$)"
    r"|wildlife-windows|watch-wildlife-online|osprey|peregrine",
    re.IGNORECASE,
)
# ...minus the article, species, event and how-to pages that mention a cam: they
# embed recordings, or only the trust's channel link in the site chrome.
_NOT_CAM_PATH = re.compile(
    r"/(?:blogs?|news|stories|events?|whats-on|our-events|species|wildlife-explorer"
    r"|content_block|resources?|support-us|appeals?|donate|shop|product|jobs|cy)/"
    r"|/\d{4}/\d{2}/|camera-trap|trail-?cam|how-to|competition|faqs?\b",
    re.IGNORECASE,
)
_MAX_SITEMAPS = 40  # per trust: bounds a runaway sitemap index
_MAX_PAGES = 100  # per trust: bounds a runaway sitemap

# Player embeds, read from every tag rather than the ladder's one fallback iframe:
# every iframe on a multi-cam page, script embeds, and consent placeholders that hold
# the real URL in data-src-cmplz. `\bsrc` also matches inside `data-src` and
# `data-litespeed-src`. `[^<>]` keeps a tag match from running past the next tag.
_TAG = re.compile(r"<(?:iframe|script)\b[^<>]*>", re.IGNORECASE)
_PLAYER_ATTR = re.compile(
    r"""\b(?:src|data-src-cmplz)=["']((?:https?:)?//(?:www\.)?"""
    r"(?:youtube(?:-nocookie)?\.com/embed|camstreamer\.com/embed|camsecure\.[a-z.]+"
    r"""|live\.streamdays\.com)/[^"'\s<>]+)""",
    re.IGNORECASE,
)
_TAG_TITLE = re.compile(r"""\btitle=["']([^"'<>]+)""", re.IGNORECASE)
_GENERIC_TITLE = re.compile(
    r"^(?:youtube( video player)?|video|webcam|iframe)$", re.IGNORECASE
)
# One embed plugin titles its iframes `YouTube video "<title>"`.
_QUOTED_TITLE = re.compile(r'^youtube video\s*["“](.+)["”]$', re.IGNORECASE)
# A channel's "current live" embed (Scottish Wildlife Trust's ospreys).
# The query is matched whole and then searched for `channel=`: an optional `…&` span
# before a required `channel=` re-scans the rest of the URL from every start in it.
_YT_LIVE_QUERY = re.compile(
    r"youtube(?:-nocookie)?\.com/embed/live_stream\?([^\"'\s<>]*)",
    re.IGNORECASE,
)
_CHANNEL_PARAM = re.compile(r"(?:^|[?&])channel=([\w-]+)", re.IGNORECASE)
# The index links some cams straight at a channel's /streams or /videos tab.
_YT_CHANNEL_TAB = re.compile(
    r"^(https://www\.youtube\.com/(?:@[\w.-]+|channel/[\w-]+))/(?:streams|videos)/?$"
)
_YT_CHANNEL_LIVE = re.compile(
    r"youtube\.com/(?:channel/[\w-]+|@[\w.-]+)/live$", re.IGNORECASE
)
_YT_EMBED_URL = re.compile(r"youtube(?:-nocookie)?\.com/embed/[\w-]+")
_OEMBED = "https://www.youtube.com/oembed?format=json&url="
_PAGE_TITLE = re.compile(
    r'<meta property="og:title" content="([^"<>]+)"|<title>([^<]+)</title>',
    re.IGNORECASE,
)
_TITLE_SUFFIX = re.compile(
    r"(?<!\s)\s*[|–-](?=[^|–-]*(?:Wildlife|Trust))[^|–-]*$", re.IGNORECASE
)

# Where a target was found, best first. A cam reached from several places takes its
# name from the best one: the page that embeds it, then an index card, then a page
# that merely links it.
_EMBEDDED, _CARD, _LINKED = 0, 1, 2


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", decode_entities(re.sub(r"<[^<>]+>", " ", raw))).strip()


def _site(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _cand(target: str, page_url: str, title: str) -> Candidate:
    key = predisc_key(target)
    return Candidate(
        title=title,
        # Keyed by the cam, not its position: the stable id must not move to another
        # cam when a page gains, loses or reorders embeds, and the index's YouTube
        # cards all share one page.
        angle_key=key or target,
        category=_CATEGORY,
        source=WildlifeTrustsSource.name,
        source_page_url=page_url,
        target_url=target,
        predisc_key=key,
    )


class WildlifeTrustsSource:
    """The Wildlife Trusts' cams, from two lists.

    The lists are the national /webcams index (curated cards, some on non-trust sites)
    and every trust's own sitemap, which carries cam pages the index never links. The
    national site is behind a Cloudflare fingerprint gate, so this source's Fetcher
    impersonates a browser.

    Pages embed YouTube (often behind a consent placeholder), camstreamer (a wrapped
    YouTube, resolved here so it dedups on its `yt:` key), camsecure (resolved to its
    open HLS, as camscape does) and direct HLS. All wildlife -> category Animals.
    """

    name: str = "wildlife-trusts"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        """Take the `fetch` used for every request."""
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        """Yield the cams from the index cards and the trust sitemaps, once per cam."""
        found: list[tuple[int, Candidate]] = []
        card_titles: dict[str, str] = {}
        for href, title in self._cards():
            if "youtube.com/" in href:
                # Not a page to scrape: the card IS the video (or the channel).
                tab = _YT_CHANNEL_TAB.match(href)
                target = f"{tab.group(1)}/live" if tab else href
                found.append((_CARD, _cand(target, _INDEX, title)))
            else:
                card_titles.setdefault(href, title)
        trusts = _TRUST_SITE.findall(self._fetch.get(_TRUSTS) or "")
        origins = sorted({f"https://{urlsplit(u).hostname}" for u in trusts})
        pages = set(card_titles)
        for trust_pages in thread_map(self._cam_pages, origins):
            pages.update(trust_pages)
        ordered = sorted(pages)
        for url, html in zip(
            ordered, thread_imap(self._fetch.get, ordered), strict=True
        ):
            if html:
                found.extend(self._page(url, html, card_titles.get(url)))
        best: dict[str, tuple[int, Candidate]] = {}
        for rank, c in found:
            cam = c.predisc_key or c.target_url
            if cam not in best or rank < best[cam][0]:
                best[cam] = (rank, c)
        for _, c in best.values():
            yield c

    def _cards(self) -> Iterator[tuple[str, str]]:
        for chunk in (self._fetch.get(_INDEX) or "").split(_CARD_MARK)[1:]:
            block = chunk[:4000]
            href = _CARD_HREF.search(block)
            if href:
                title = _CARD_TITLE.search(block)
                name = _text(title.group(1)) if title else ""
                yield htmllib.unescape(href.group(1)), name

    def _cam_pages(self, origin: str) -> list[str]:
        """Return the cam-named pages in one trust's sitemap (via a sitemap index).

        Most trusts serve it at /sitemap.xml; the rest name it in robots.txt. Only the
        trust's own site is followed, `www.` or not: the trusts list it either way.
        """
        site = _site(origin)
        queue, seen, pages = [f"{origin}/sitemap.xml"], set[str](), list[str]()
        while queue and len(seen) < _MAX_SITEMAPS and len(pages) < _MAX_PAGES:
            sitemap = queue.pop(0)
            if sitemap in seen:
                continue
            seen.add(sitemap)
            body = self._fetch.get(sitemap) or ""
            if not body and len(seen) == 1:
                robots = self._fetch.get(f"{origin}/robots.txt") or ""
                queue.extend(
                    u for u in _ROBOTS_SITEMAP.findall(robots) if _site(u) == site
                )
            locs = [htmllib.unescape(u) for u in _LOC.findall(body)]
            locs = [u for u in locs if _site(u) == site]
            if "<sitemapindex" in body:
                queue.extend(locs)
                continue
            for u in locs:
                path = urlsplit(u).path
                if _CAM_PATH.search(path) and not _NOT_CAM_PATH.search(path):
                    pages.append(u)
        if queue or len(pages) >= _MAX_PAGES:
            log.info("%s: sitemap walk hit its bound; cam pages may be missed", site)
        return pages[:_MAX_PAGES]

    def _page(
        self, url: str, html: str, card_title: str | None
    ) -> list[tuple[int, Candidate]]:
        titled: dict[str, str] = {}  # embedded target -> the embed's own title, or ""
        raw_embeds: set[str] = set()
        for tag in _TAG.findall(html):
            m = _PLAYER_ATTR.search(tag)
            if not m:
                continue
            raw = urljoin(url, htmllib.unescape(m.group(1)))
            raw_embeds.add(raw)
            tm = _TAG_TITLE.search(tag)
            title = _text(tm.group(1)) if tm else ""
            quoted = _QUOTED_TITLE.match(title)
            title = quoted.group(1) if quoted else title
            title = "" if _GENERIC_TITLE.match(title) else title
            for target in self._resolve_embed(raw):
                titled.setdefault(target, title)
        linked = [
            c.target_url
            for c in extract_candidates(html, page_url=url, source=self.name)
            # The ladder's iframe fallback re-reads an embed resolved above; that is
            # not a further cam.
            if c.target_url not in titled and c.target_url not in raw_embeds
        ]
        # Every trust page's chrome links the trust's YouTube channel. A linked channel
        # live is the cam only on an index card's page with nothing else; elsewhere it
        # is chrome, and when that channel is live it duplicates a cam listed by id.
        channel = [t for t in linked if _YT_CHANNEL_LIVE.search(t)]
        linked = [t for t in linked if not _YT_CHANNEL_LIVE.search(t)]
        if not titled and not linked and card_title is not None:
            linked = channel
        targets = [*titled, *linked]
        titles = [titled.get(t, "") for t in targets]
        if len(targets) > 1:
            # Several cams on one page, often other projects' (the Derby blog embeds
            # Rutland's and Holkham's): an untitled YouTube one takes the video's own
            # title, and any still untitled are numbered after the page.
            titles = thread_map(self._yt_title, list(zip(targets, titles, strict=True)))
        page_title = card_title or self._page_title(html)
        untitled = titles.count("")
        n = 0
        out: list[tuple[int, Candidate]] = []
        for target, found in zip(targets, titles, strict=True):
            title = found
            if not title:
                n += 1
                title = f"{page_title} {n}" if untitled > 1 else page_title
            if target in titled:
                rank = _EMBEDDED
            else:
                rank = _CARD if card_title is not None else _LINKED
            out.append((rank, _cand(target, url, title)))
        return out

    @staticmethod
    def _page_title(html: str) -> str:
        m = _PAGE_TITLE.search(html)
        return _TITLE_SUFFIX.sub("", _text((m.group(1) or m.group(2)) if m else ""))

    def _yt_title(self, target_title: tuple[str, str]) -> str:
        target, title = target_title
        if title or not (predisc_key(target) or "").startswith("yt:"):
            return title
        body = self._fetch.get(_OEMBED + quote(target, safe="")) or ""
        try:
            found = json.loads(body).get("title", "")
        except ValueError, AttributeError:
            return ""
        return found.strip() if isinstance(found, str) else ""

    def _camstreamer_target(self, embed: str) -> str:
        """Return the YouTube video a camstreamer player wraps, else the player."""
        m = _YT_EMBED_URL.search(self._fetch.get(embed) or "")
        video = (predisc_key(m.group(0)) or "") if m else ""
        if video.startswith("yt:"):
            return f"https://www.youtube.com/watch?v={video[3:]}"
        return embed

    def _resolve_embed(self, embed: str) -> list[str]:
        """Map an embed URL to the target(s) the extractors serve."""
        for query in _YT_LIVE_QUERY.finditer(embed):
            live = _CHANNEL_PARAM.search(query.group(1))
            if live:
                return [f"https://www.youtube.com/channel/{live.group(1)}/live"]
        key = predisc_key(embed) or ""
        if key.startswith("yt:"):
            return [f"https://www.youtube.com/watch?v={key[3:]}"]
        host = urlsplit(embed).hostname or ""
        if "youtube" in host:
            return []  # a playlist: recordings, never a live cam
        if host.endswith("camstreamer.com"):
            return [self._camstreamer_target(embed)]
        if "camsecure." in host:
            # The m3u8 is the key the camsecure source shares; it is not derivable
            # from the player URL, so read it from the player page.
            return [hls_from_player(self._fetch.get(embed) or "", embed) or embed]
        # streamdays: a tokened player no extractor serves. Kept, so it shows as a
        # no-extractor drop rather than vanishing.
        return [embed]
