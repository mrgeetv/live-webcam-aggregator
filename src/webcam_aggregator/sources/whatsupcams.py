"""whatsupcams.com source: Yoast sitemaps -> cam pages -> CDN API direct HLS."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, override
from urllib.parse import urlsplit

from webcam_aggregator.fetch import thread_imap
from webcam_aggregator.hosts import on_host
from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import (
    HtmlScraperSource,
    decode_entities,
    predisc_key,
    with_location_parts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_SITEMAP_INDEX = "https://www.whatsupcams.com/en/sitemap_index.xml"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
# The Yoast index also lists post/page/country/region/town/camera_type sitemaps;
# only the webcams-sitemap*.xml files carry the cam detail pages.
_CAM_SITEMAP = re.compile(r"/webcams-sitemap\d*\.xml$")
# The page's own cam id is the JSON-LD embedUrl (services.whatsupcams.com/wgt/<id>/).
# The snapshot thumbnail URLs also carry ids but include the "nearby cams" strip's,
# so only the wgt URL is unambiguous.
_WGT_ID = re.compile(r"services\.whatsupcams\.com/wgt/([A-Za-z0-9_]+)")
_OG_TITLE = re.compile(r'property="og:title" content="([^"]+)"')
# Per-id stream JSON: `hls.url` is a stable tokenless media playlist (open CDN —
# no Referer, no session binding; served by DirectHls). The cdn-0NN host varies
# per cam, so the URL cannot be derived from the id — this API is the mapping.
_API = "https://www.whatsupcams.com/cdn-api/streams/"
# Path segments in a cam page URL: en/webcams/<country>/<region>/<town>/<slug>.
_CAM_PAGE_DEPTH = 6


class WhatsupcamsSource(HtmlScraperSource[str]):
    """whatsupcams.com: a Croatian-run, Adriatic-heavy network of cams.

    Yoast sitemap -> per-cam pages (cam id + title) -> per-id CDN API (direct open HLS),
    the API call being the page's second hop. A page without a wgt embed id isn't a cam
    page and drops; an id whose API fetch fails drops. No per-cam category in the page
    -> "Other" (title fallback).
    """

    name: str = "whatsupcams"

    @override
    def _page_urls(self) -> list[str]:
        idx = self._fetch.get(_SITEMAP_INDEX) or ""
        maps = [
            u
            for u in _LOC.findall(idx)
            if on_host(u, "whatsupcams.com") and _CAM_SITEMAP.search(urlsplit(u).path)
        ]
        pages: set[str] = set()
        for sm in thread_imap(self._fetch.get, maps):
            for loc in _LOC.findall(sm or ""):
                # cam pages sit at /en/webcams/<country>/<region>/<town>/<slug>/;
                # the sitemap also lists the bare /en/webcams/ archive page. Anchor to
                # our own host too: a tampered sitemap must not steer the build-time
                # fetcher at arbitrary third-party hosts.
                depth = len(urlsplit(loc).path.strip("/").split("/"))
                if on_host(loc, "whatsupcams.com") and depth >= _CAM_PAGE_DEPTH:
                    pages.add(loc)
        return sorted(pages)

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        tm = _OG_TITLE.search(html)
        # no category in the page -> "Other" (title fallback)
        return None, decode_entities(tm.group(1)).strip() if tm else ""

    @override
    def _candidates(self, html: str, url: str) -> Iterator[Candidate]:
        m = _WGT_ID.search(html)
        if not m:
            return
        try:
            data = json.loads(self._fetch.get(_API + m.group(1)) or "")
        except ValueError:
            return
        hls = data.get("hls") if isinstance(data, dict) else None
        stream = str(hls.get("url") or "") if isinstance(hls, dict) else ""
        if ".m3u8" not in stream:
            return
        yield Candidate(
            title="",
            angle_key=None,
            category=None,
            source=self.name,
            source_page_url=url,
            target_url=stream,
            predisc_key=predisc_key(stream),
        )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        # country/region/town from the page path, appended when the title doesn't
        # already name them
        parts = [
            s.replace("-", " ").title()
            for s in urlsplit(url).path.strip("/").split("/")[2:5]
        ]
        return with_location_parts(ctx, parts)
