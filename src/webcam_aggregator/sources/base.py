from __future__ import annotations

import html as htmllib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import Generic, Protocol, TypeVar
from urllib.parse import unquote, urljoin, urlsplit

from ..fetch import FetcherProtocol, thread_imap
from ..models import Candidate

# `embed/videoseries?list=` (a playlist) and `embed/live_stream?channel=` (a channel's
# current live) are 11 characters long, exactly like a video id: the lookahead keeps
# them from reading as one.
_YT_VIDEO = re.compile(
    r"youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)"
    r"(?!(?:videoseries|live_stream)\b)([A-Za-z0-9_-]{11})"
)
_YT_PLAYLIST = re.compile(r"youtube(?:-nocookie)?\.com/embed\?list=([A-Za-z0-9_-]+)")
_YT_CHANNEL = re.compile(
    r"youtube\.com/(channel/[A-Za-z0-9_-]+|@[A-Za-z0-9_.-]+)(?:/live)?"
)
# A playlist is a whole URL-like run that holds `.m3u8`, tested after matching: a
# greedy body before a required `.m3u8` re-scans the rest of the run from every `http`
# in it, and `re` holds the GIL while it does (see the AGENTS.md regex lesson).
_URL_RUN = re.compile(r"https?:[\\/]+[^\"'\s\\]+")
_WC_STREAMS = re.compile(
    r"""streams\[(\d+)\]\s*=\s*(["'])((?:\\.|(?!\2)[^\\\n])*+)\2"""
)
_WC_PLAYER = re.compile(r"worldcams\.tv\\?/player\?url=([^\"'\s\\]+)")
_IFRAME_SRC = re.compile(r'src=\\?"([^"\\]+)')
_IFRAME_TAG = re.compile(r'<iframe[^<>]+src=["\']([^"\']+)["\']', re.I)
# Still-image "cams" listed alongside real streams (worldcams' streams[] array, the
# EarthCam long tail). Matched on the URL PATH so a query string can't false-trigger.
_IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|bmp)$", re.I)
# A feratel cam's identity is its numeric id, not the URL shape: the same cam appears
# as webtv.feratel.com/webtv/?cam=<id> (our canonical form) and as webtvfc.… with
# extra design/pg params in third-party embeds, so keying on the id is what lets the
# feratel source's copy merge with the ones cxtvlive/camscape pick up.
_FERATEL_QUERY = re.compile(r"webtv(?:fc)?\.feratel\.com/webtv/\?([^\"'\s]*)")
_CAM_PARAM = re.compile(r"\bcam=(\d+)")

# Iframes that can never carry a stream: the tag-manager/consent frames pages embed
# alongside their real player (GTM's <noscript> ns.html is the common one), and the
# Waze live traffic map that traffic-cam pages embed beside or instead of one. The
# extraction ladder falls back to a page's first iframe that could be a player, so
# without this list one of these would be taken for the player, or become a
# candidate and then a `no-extractor` drop — noise in the very list that is supposed
# to say which extractor to write next.
#
# Add a host here ONLY if it serves no video at all. A video embed we merely can't
# play (ivideon, rtsp.me, angelcam) must NOT go here: it is a real coverage gap, and
# its place is the `no-extractor` report where it stays visible as work someone
# could pick up.
_NEVER_A_STREAM_HOSTS = ("googletagmanager.com", "waze.com")

# Strips entire "Source: <a ...>...</a>" attribution block (including the URL inside).
# The link text stops at the next <a or </a, so an unclosed link costs one span, not
# a scan to the end of the page.
_ATTRIBUTION_BLOCK = re.compile(
    r"Source:\s*(?:&nbsp;\s*)?<a\b[^<>]*>(?:[^<]|<(?!/?a\b))*+</a>", re.I
)


class Source(Protocol):
    name: str

    def discover(self) -> Iterable[Candidate]: ...


def _strip_attribution(html: str) -> str:
    return _ATTRIBUTION_BLOCK.sub("", html)


def _not_a_stream(target: str) -> bool:
    """True for an embed target that can never be a stream, so must not become a
    candidate: a non-http(s) placeholder (a consent manager parks embeds on
    about:blank), a still image (worldcams and the EarthCam long tail list JPEG cams
    beside real ones), or a host serving no video at all (_NEVER_A_STREAM_HOSTS,
    subdomain-aware so `www.`/`ns.` variants match without listing each one). Left
    in, each is a doomed `no-extractor` drop inflating the counts that say which
    extractor to write next."""
    parts = urlsplit(target)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme not in ("http", "https")
        or bool(_IMAGE_EXT.search(parts.path))
        or any(host == h or host.endswith("." + h) for h in _NEVER_A_STREAM_HOSTS)
    )


def _angle_targets(html: str) -> list[tuple[str | None, str]]:
    # worldcams multi-cam pages store each embed as streams[<stream-id>] = '<iframe…>'
    # (single OR double quoted). The stream-id is the join key to the per-cam name in
    # the streams__item selector, so carry it through as the angle_key.
    out: list[tuple[str | None, str]] = []
    for sid, _quote, raw in _WC_STREAMS.findall(html):
        m = _IFRAME_SRC.search(raw)
        if m:
            out.append((sid, m.group(1)))
    return out


def predisc_key(target: str) -> str | None:
    t = (
        unquote(re.sub(r"^https?:.*?url=", "", target))
        if "player?url=" in target
        else target
    )
    m = _YT_VIDEO.search(t)
    if m:
        return f"yt:{m.group(1)}"
    for fq in _FERATEL_QUERY.finditer(t):
        cam = _CAM_PARAM.search(fq.group(1))
        if cam:
            return f"feratel:{cam.group(1)}"
    if ".m3u8" in t:
        # Strip only unambiguous token params. NOT generic single-letter names like
        # `st`/`e` — those can be legitimate stream selectors, and stripping them
        # would collapse two distinct streams to one key (dedup would drop one).
        norm = re.sub(r"[?&](token|expire|hdnts)=[^&]*", "", t).rstrip("?&")
        return f"hls:{norm}"
    return None


def _norm(s: str) -> str:
    """Lower-case, drop apostrophes, punctuation -> spaces (for substring matching)."""
    s = s.lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _location_parts(page_url: str) -> list[str]:
    """Prettified URL path segments, general -> specific (country, place, name)."""
    path = urlsplit(page_url).path.strip("/")
    return [
        s.replace("-", " ").title() for s in path.split("/") if s and s != "live-camera"
    ]


def with_location_parts(title: str, parts: list[str], *, drop: str = "") -> str:
    """Append the location `parts` (general -> specific) the title doesn't already name,
    keeping the cam's category (`drop`) out of the suffix and dropping repeated parts
    (some breadcrumbs name the same place at two levels, e.g. a parish == its only town).
    Shared by URL-path sources and sources whose geo lives elsewhere (a breadcrumb)."""
    nt = _norm(title)
    dn = _norm(drop)
    extra: list[str] = []
    seen: set[str] = set()
    for p in reversed(parts):
        n = _norm(p)
        if n and n != dn and n not in seen and (not title.strip() or n not in nt):
            extra.append(p)
            seen.add(n)
    if not title.strip():
        return ", ".join(extra) or title
    return f"{title} — {', '.join(extra)}" if extra else title


def with_location(title: str, page_url: str, *, drop: str = "") -> str:
    """Append the URL location segments the title doesn't already name.

    worldcams h1s usually already include the place, so we only add what's new
    (e.g. the country) — avoiding "Dusseldorf Airport — Dusseldorf, Germany" — while
    still distinguishing generic titles ("Italy Beaches — Cinque Terre"). `drop` is
    the cam's category: players show it as the group, so we keep it out of the suffix
    too (no "Playa del Inglés — Beaches, Gran Canaria, Spain").
    """
    return with_location_parts(title, _location_parts(page_url), drop=drop)


def extract_candidates(html: str, *, page_url: str, source: str) -> Iterator[Candidate]:
    clean = _strip_attribution(html)
    # Prefer the structured streams[<id>] = '<iframe…>' embeds (keyed by stream-id);
    # fall back to a whole-page scan when a page has no such array.
    pairs: list[tuple[str | None, str]] = _angle_targets(clean)
    if not pairs:
        plain: list[str] = []
        for m in _YT_VIDEO.finditer(clean):
            plain.append(f"https://www.youtube.com/watch?v={m.group(1)}")
        for m in _YT_PLAYLIST.finditer(clean):
            plain.append(f"https://www.youtube.com/embed?list={m.group(1)}")
        for pm in _WC_PLAYER.finditer(clean):
            plain.append("https://worldcams.tv/player?url=" + pm.group(1))
        for mm in _URL_RUN.finditer(clean):
            if ".m3u8" in mm.group(0):
                plain.append(mm.group(0))
        cm = _YT_CHANNEL.search(clean)
        if cm:
            plain.append("https://www.youtube.com/" + cm.group(1) + "/live")
        if not plain:
            # The page's FIRST iframe that could carry a stream, not its first iframe:
            # CMS pages open <body> with GTM's <noscript> frame, ahead of the player.
            # The src is an HTML attribute, so its entities (`&amp;`) are decoded.
            for ifr in _IFRAME_TAG.finditer(clean):
                target = urljoin(page_url, htmllib.unescape(ifr.group(1)))
                if not _not_a_stream(target):
                    plain.append(target)
                    break
        multi = len(plain) > 1
        pairs = [(str(i) if multi else None, t) for i, t in enumerate(plain)]
    seen: set[str] = set()
    for key, raw_target in pairs:
        # Embeds are sometimes site-relative (worldcams' streams[] array mixes absolute
        # iframes with paths). Resolve against the page rather than dropping them: a
        # relative player path is perfectly usable once absolute, and a target with no
        # host is useless to every extractor and unattributable in the diagnostics.
        target = urljoin(page_url, raw_target)
        if _not_a_stream(target) or target in seen:
            continue
        seen.add(target)
        yield Candidate(
            title="",
            angle_key=key,
            category=None,
            source=source,
            source_page_url=page_url,
            target_url=target,
            predisc_key=predisc_key(target),
        )


Ctx = TypeVar("Ctx")


class HtmlScraperSource(ABC, Generic[Ctx]):
    """Shared crawl loop for HTML scraper sources. Subclasses supply three hooks —
    how to find the cam detail-page URLs, the per-page (category, context), and the
    per-candidate title. The concurrent fetch (`thread_imap`), empty-page skipping, and
    `extract_candidates` wiring live here, so each source stays a handful of regexes.
    `Ctx` is whatever per-page state a source precomputes once (e.g. a stream-id ->
    name map) and reuses across that page's candidates in `_title_for`."""

    name: str
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    @abstractmethod
    def _page_urls(self) -> list[str]:
        """All cam detail-page URLs to scrape (e.g. from a sitemap or paginated list)."""

    @abstractmethod
    def _page_meta(self, html: str, url: str) -> tuple[str | None, Ctx]:
        """Per-page (category, context). Computed once per page; ctx is handed to
        `_title_for` for every candidate on the page."""

    @abstractmethod
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: Ctx
    ) -> str:
        """Display title for one candidate, given its page's category + context."""

    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        """Per-page candidate extraction. Defaults to the shared extraction ladder;
        override for sources whose embeds it doesn't recognise (a player config with a
        site-specific token, a YouTube id in a JS var, …)."""
        return extract_candidates(html, page_url=url, source=self.name)

    def discover(self) -> Iterator[Candidate]:
        urls = self._page_urls()
        for url, html in zip(urls, thread_imap(self._fetch.get, urls)):
            if not html:
                continue
            category, ctx = self._page_meta(html, url)
            for c in self._candidates(html, url):
                yield replace(
                    c, title=self._title_for(c, url, category, ctx), category=category
                )
