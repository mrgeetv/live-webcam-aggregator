from __future__ import annotations

import json
import re
import string
from collections.abc import Iterable
from html import unescape
from typing import override
from urllib.parse import urlsplit

from ..models import Candidate
from .base import HtmlScraperSource, predisc_key

_BASE = "https://www.webcamera.pl"

# /mapa inlines `var MAP_MARKERS = {...}` — every mapped cam's page URL + display
# name. It is NOT the complete list (most ski-resort cams are unmapped), so the
# category listings below are unioned in for enumeration.
_MAP_PAGE = f"{_BASE}/mapa"

# The site's clean top-level sections, priority-ordered: the first listing that
# names a cam wins, so the specific label (a seaside or lakeside cam, a city
# street that also appears under "gory") beats the broader one. Values are
# source-level categories `categories._MAP` already knows. The regional/seasonal
# /kategoria pages (bieszczady, lato, tv, …) are deliberately not crawled —
# they're tags, not categories.
_CATEGORY_PAGES: tuple[tuple[str, str], ...] = (
    ("plaze-i-morze", "Beaches"),
    ("mazury", "Rivers Lakes"),
    ("miasta", "Cities"),
    ("stacje-narciarskie", "Ski Resorts"),
    ("gory", "Mountains"),
)
# A category page's cam grid sits between these two markers; links outside it
# (nav, footer, the "recently added" strip) would mis-categorise cams.
_LISTING_START = 'id="inline-camera-listing"'
_LISTING_END = "searchbox-cams"
_HREF = re.compile(r'href="(https://[a-z0-9.-]+\.webcamera\.pl/[^"]*)"')

# The player config is inline JSON; `video_src` is the HLS URL, ROT13-obfuscated
# (the page JS decodes it with the same letter shift before handing it to hls.js).
_VIDEO_SRC = re.compile(r'"video_src":"((?:[^"\\]|\\.)*)"')
_A, _L = string.ascii_uppercase, string.ascii_lowercase
_ROT13 = str.maketrans(_A + _L, _A[13:] + _A[:13] + _L[13:] + _L[:13])

_H1 = re.compile(r'<h1 id="cam-description-toggler">([^<]*)', re.I)
# Trailing listing badges, not part of the cam's name.
_BADGE = re.compile(r"(?:\s*[-–]?\s*(?:nowość|nowosc|kup dostęp))+\s*$", re.I)
# Compilation feeds that rotate through other cams' streams (one shared tv_cam
# feed per playlist) — dups of cams we already carry, and the PREMIUM ones are
# paywalled besides.
_SKIP_NAME = re.compile(r"playlist|premium", re.I)


def _cam_page(url: str) -> str | None:
    """Canonical cam-page URL, or None for non-cam links (nav, static assets).
    A cam lives either on its own subdomain or under www's /kamera/ path."""
    s = urlsplit(url)
    host = (s.hostname or "").lower()
    if not host.endswith(".webcamera.pl"):
        return None
    sub = host[: -len(".webcamera.pl")]
    if sub == "www":
        m = re.match(r"/kamera/[a-z0-9-]+/", s.path)
        return f"https://www.webcamera.pl{m.group(0)}" if m else None
    if not sub or "." in sub or sub in ("static", "b2b", "pogoda", "imageserver"):
        return None
    return f"https://{host}/"


def _slug_title(url: str) -> str:
    s = urlsplit(url)
    host = s.hostname or ""
    # subdomain cams carry their slug in the host, /kamera/ cams in the path
    slug = (
        s.path.rstrip("/").rsplit("/", 1)[-1]
        if host.startswith("www.")
        else host.split(".", 1)[0]
    )
    return slug.replace("-", " ").title()


class WebcameraPlSource(HtmlScraperSource[str]):
    """webcamera.pl: ~600 Polish cams (Baltic beaches, Tatra/ski resorts, towns),
    each on its own subdomain with the HLS URL ROT13-encoded in the inline player
    config — decoded here at discovery, served by DirectHls. Enumeration is the
    /mapa marker JSON unioned with the five top-level category listings (the map
    misses most ski cams; the listings carry the category). ctx is the cam's
    Polish display name (the page h1). Streams are Nimble: the manifest mints a
    `nimblesessionid` that must survive into segment fetches, so webcamera.pl is
    segment-proxied."""

    name: str = "webcamerapl"

    # populated by _page_urls, which discover() always runs first
    _names: dict[str, str]
    _cats: dict[str, str]

    def _markers(self) -> list[dict[str, object]]:
        html = self._fetch.get(_MAP_PAGE) or ""
        i = html.find("var MAP_MARKERS")
        if i < 0:
            return []
        try:
            data, _ = json.JSONDecoder().raw_decode(html, html.index("{", i))
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        return [v for v in data.values() if isinstance(v, dict)]

    @override
    def _page_urls(self) -> list[str]:
        self._names = {}
        self._cats = {}
        pages: set[str] = set()
        skip: set[str] = set()
        for v in self._markers():
            raw_url = v.get("url")
            name = v.get("name")
            url = _cam_page(raw_url) if isinstance(raw_url, str) else None
            if not url:
                continue
            if v.get("is_inactive") or (
                isinstance(name, str) and _SKIP_NAME.search(name)
            ):
                # keep skipped urls out even when a category listing re-lists them
                skip.add(url)
                continue
            pages.add(url)
            if isinstance(name, str):
                self._names.setdefault(url, name)
        for slug, category in _CATEGORY_PAGES:
            html = self._fetch.get(f"{_BASE}/kategoria,{slug}") or ""
            i = html.find(_LISTING_START)
            if i < 0:
                continue
            j = html.find(_LISTING_END, i)
            for href in _HREF.findall(html[i : j if j > 0 else len(html)]):
                url = _cam_page(href)
                if url and url not in skip:
                    pages.add(url)
                    self._cats.setdefault(url, category)
        return sorted(pages)

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _H1.search(html)
        raw = unescape(m.group(1)).strip() if m else self._names.get(url, "")
        title = _BADGE.sub("", raw).strip() or _slug_title(url)
        return self._cats.get(url), title

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        m = _VIDEO_SRC.search(html)
        if not m:
            # premium/offline pages render no public player config
            return
        src = m.group(1).replace("\\/", "/").translate(_ROT13)
        if ".m3u8" not in src:
            # an .mp4 video_src is a rebroadcast placeholder, not a live stream
            return
        yield Candidate(
            title="",
            angle_key=None,
            category=None,
            source=self.name,
            source_page_url=url,
            target_url=src,
            predisc_key=predisc_key(src),
        )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
