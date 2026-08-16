from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from typing import override

from ..models import Candidate
from .base import HtmlScraperSource, extract_candidates, predisc_key

# The "by webcam speed" index is the enumeration seam: its <strong>Streaming</strong>
# section lists exactly the ~700 pages with a live stream, so we never fetch the
# ~2000 still-image cam pages the full A-to-Z index would hand us.
_INDEX = "https://airportwebcams.net/airport-webcams-by-webcam-speed/"
_STREAMING = "<strong>Streaming</strong>"
_LINK = re.compile(r'href="(https://airportwebcams\.net/[^"]+)"')
# Same shape as the base ladder's channel rule, but applied with finditer: airport
# pages list one channel PER CAMERA (Schiphol has 8) and the shared ladder keeps
# only the first. The site's own footer channel is the youtube.com/user/… form,
# which this deliberately does not match.
_CHANNEL = re.compile(r"youtube\.com/(channel/[A-Za-z0-9_-]+|@[A-Za-z0-9_.-]+)")
# Channels below this heading are spotters who stream now and then, not 24/7 cams —
# mostly offline, and each one costs the build a yt-dlp resolve. Only fall back to
# them when a page lists no 24/7 channel at all (Zurich's are all in this section).
_OCCASIONAL = "Live streamers with regular broadcasts"
# WordPress permalink heading: <h2><a href=… rel="bookmark" …>Zurich Airport Webcam</a>
_TITLE = re.compile(r'rel="bookmark"[^>]*>([^<]+)')
_TITLE_TAIL = re.compile(r"\s*Webcams?\s*$", re.I)


class AirportWebcamsSource(HtmlScraperSource[str]):
    """airportwebcams.net: an airport-cam directory whose pages link each camera's
    YouTube channel (its /streams page) rather than a fixed video id — the current
    live stream is resolved via the channel's /live URL at build/serve time. A
    channel not currently live is a resolve-failed drop; that churn is normal for
    this source. All cams -> category Airports; ctx is the page's airport title."""

    name: str = "airportwebcams"

    @override
    def _page_urls(self) -> list[str]:
        html = self._fetch.get(_INDEX) or ""
        i = html.find(_STREAMING)
        if i < 0:
            return []
        j = html.find("<strong>", i + len(_STREAMING))
        section = html[i : j if j >= 0 else len(html)]
        return list(dict.fromkeys(_LINK.findall(section)))

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        m = _TITLE.search(html)
        title = unescape(m.group(1)).strip() if m else ""
        if not title:
            # last resort: the URL slug ("zurich-airport-webcam")
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        return "Airports", _TITLE_TAIL.sub("", title).strip()

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        i = html.find(_OCCASIONAL)
        head = html if i < 0 else html[:i]
        chans = [m.group(1) for m in _CHANNEL.finditer(head)]
        if not chans:
            chans = [m.group(1) for m in _CHANNEL.finditer(html)]
        chans = list(dict.fromkeys(chans))
        if not chans:
            # no channel at all: the standard ladder still catches the direct
            # video embeds / m3u8 a few pages carry instead
            yield from extract_candidates(html, page_url=url, source=self.name)
            return
        multi = len(chans) > 1
        for ch in chans:
            target = f"https://www.youtube.com/{ch}/live"
            yield Candidate(
                title="",
                # the handle, not a list index: a channel added to the page must
                # not shift every other camera's stable id
                angle_key=ch if multi else None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                predisc_key=predisc_key(target),
            )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
