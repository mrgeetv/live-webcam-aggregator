from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import Generic, Protocol, TypeVar
from urllib.parse import unquote, urlsplit

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate

_YT_VIDEO = re.compile(
    r"youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)([A-Za-z0-9_-]{11})"
)
_YT_PLAYLIST = re.compile(r"youtube(?:-nocookie)?\.com/embed\?list=([A-Za-z0-9_-]+)")
_YT_CHANNEL = re.compile(
    r"youtube\.com/(channel/[A-Za-z0-9_-]+|@[A-Za-z0-9_.-]+)(?:/live)?"
)
_M3U8 = re.compile(r"https?:[\\/]+[^\"'\s\\]+\.m3u8[^\"'\s\\]*")
_WC_STREAMS = re.compile(r"""streams\[(\d+)\]\s*=\s*(["'])((?:\\.|(?!\2).)*)\2""")
_WC_PLAYER = re.compile(r"worldcams\.tv\\?/player\?url=([^\"'\s\\]+)")
_IFRAME_SRC = re.compile(r'src=\\?"([^"\\]+)')
_IFRAME_TAG = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
# Strips entire "Source: <a ...>...</a>" attribution block (including the URL inside)
_ATTRIBUTION_BLOCK = re.compile(
    r"Source:\s*(?:&nbsp;\s*)?<a\b[^>]*>.*?</a>", re.I | re.S
)


class Source(Protocol):
    name: str

    def discover(self) -> Iterable[Candidate]: ...


def _strip_attribution(html: str) -> str:
    return _ATTRIBUTION_BLOCK.sub("", html)


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
        for mm in _M3U8.finditer(clean):
            plain.append(mm.group(0))
        cm = _YT_CHANNEL.search(clean)
        if cm:
            plain.append("https://www.youtube.com/" + cm.group(1) + "/live")
        if not plain:
            ifr = _IFRAME_TAG.search(clean)
            if ifr:
                plain.append(ifr.group(1))
        multi = len(plain) > 1
        pairs = [(str(i) if multi else None, t) for i, t in enumerate(plain)]
    seen: set[str] = set()
    for key, target in pairs:
        if target in seen:
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
    per-candidate title. The concurrent fetch (`thread_map`), empty-page skipping, and
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
        for url, html in zip(urls, thread_map(self._fetch.get, urls)):
            if not html:
                continue
            category, ctx = self._page_meta(html, url)
            for c in self._candidates(html, url):
                yield replace(
                    c, title=self._title_for(c, url, category, ctx), category=category
                )
