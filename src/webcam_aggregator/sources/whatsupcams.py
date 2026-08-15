from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html import unescape
from urllib.parse import urlsplit

from ..fetch import FetcherProtocol, thread_map
from ..models import Candidate
from .base import predisc_key, with_location_parts

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


class WhatsupcamsSource:
    """whatsupcams.com (Croatian-run network, Adriatic-heavy): Yoast sitemap ->
    per-cam pages (cam id + title) -> per-id CDN API (direct open HLS). A page
    without a wgt embed id isn't a cam page and drops; an id whose API fetch
    fails drops. No per-cam category in the page -> "Other" (title fallback)."""

    name: str = "whatsupcams"
    _fetch: FetcherProtocol

    def __init__(self, fetch: FetcherProtocol) -> None:
        self._fetch = fetch

    def discover(self) -> Iterator[Candidate]:
        idx = self._fetch.get(_SITEMAP_INDEX) or ""
        maps = [u for u in _LOC.findall(idx) if _CAM_SITEMAP.search(urlsplit(u).path)]
        pages: set[str] = set()
        for sm in thread_map(self._fetch.get, maps):
            for loc in _LOC.findall(sm or ""):
                # cam pages sit at /en/webcams/<country>/<region>/<town>/<slug>/;
                # the sitemap also lists the bare /en/webcams/ archive page
                if len(urlsplit(loc).path.strip("/").split("/")) >= 6:
                    pages.add(loc)
        page_list = sorted(pages)
        # hop 1: cam pages -> (page, title, cam id)
        found: list[tuple[str, str, str]] = []
        for page, html in zip(page_list, thread_map(self._fetch.get, page_list)):
            m = _WGT_ID.search(html or "")
            if not m:
                continue
            tm = _OG_TITLE.search(html or "")
            title = unescape(tm.group(1)).strip() if tm else ""
            found.append((page, title, m.group(1)))
        # hop 2: per-id CDN API -> the cam's HLS URL (concurrent)
        seen: set[str] = set()
        raws = thread_map(self._fetch.get, [_API + f[2] for f in found])
        for (page, title, _cam_id), raw in zip(found, raws):
            try:
                data = json.loads(raw or "")
            except ValueError:
                continue
            hls = data.get("hls") if isinstance(data, dict) else None
            url = str(hls.get("url") or "") if isinstance(hls, dict) else ""
            if ".m3u8" not in url or url in seen:
                continue
            seen.add(url)
            # country/region/town from the page path, appended when the title
            # doesn't already name them
            parts = [
                s.replace("-", " ").title()
                for s in urlsplit(page).path.strip("/").split("/")[2:5]
            ]
            yield Candidate(
                title=with_location_parts(title, parts),
                angle_key=None,
                category=None,  # no category in the page -> "Other" (title fallback)
                source=self.name,
                source_page_url=page,
                target_url=url,
                predisc_key=predisc_key(url),
            )
