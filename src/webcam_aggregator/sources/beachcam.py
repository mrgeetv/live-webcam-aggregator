from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from typing import override
from urllib.parse import quote

from ..models import Candidate
from .base import HtmlScraperSource, predisc_key, with_location_parts

_INDEX = "https://beachcam.meo.pt/livecams/"
# Index links are absolute; the bare /livecams/ index itself has an empty slug and
# never matches.
_LINK = re.compile(r'href="https?://beachcam\.meo\.pt/livecams/([^"/#?]+)/"')
# Each cam page carries its stream in the player div's data-video-url attribute
# (video-auth1.iol.pt/auth-beachcam/<slug>/playlist.m3u8). The shared ladder is
# deliberately not used: every page's footer links the site's
# youtube.com/@Beachcamportugal channel, which the ladder's channel rule would turn
# into a bogus extra candidate on all ~190 pages.
_VIDEO = re.compile(r'data-video-url="(https?://[^"]+\.m3u8)"')
# The bare playlist URL answers 403; the site's JW player appends
# ?wmsAuthSign=<token> fetched from this IOL endpoint (anonymous userId works).
# The token is valid ~24h — comfortably past the default 6h rebuild, which mints a
# fresh one. The manifest then returns Nimble child/segment URLs whose
# nimblesessionid+wmsAuthSign query is minted for whoever fetched it, so
# `video-auth1.iol.pt` must be in serving._PROXY_SEGMENT_HOSTS (a segment stripped
# of its query 404s).
_TOKEN_API = "https://services.iol.pt/matrix?userId="
# a real token is a base64 blob; anything else (an error page) is not a token
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")
_TITLE = re.compile(r'liveCamsHeader__title">([^<]*)<')
# the header label names the cam's municipality/region: "LIVECAMS / Cascais"
_REGION = re.compile(r'liveCamsHeader__label">LIVECAMS\s*/\s*([^<]*)<')

# ctx = (page title, region from the header label)
_Ctx = tuple[str, str | None]


class BeachcamSource(HtmlScraperSource[_Ctx]):
    """beachcam.meo.pt: ~190 MEO Beachcam livecams, blanket-categorised "Beaches" —
    almost all are beach/surf spots, and the few lake/wake-park/city outliers are a
    better trade than ~190 Portuguese-titled cams landing in "Other" (the English
    title-keyword fallback can't read them). One stream per page in a data-video-url
    attribute, playable only with a per-build ?wmsAuthSign= auth token."""

    name: str = "beachcam"
    # per-build auth token, fetched alongside the index in _page_urls
    _token: str | None = None

    @override
    def _page_urls(self) -> list[str]:
        html = self._fetch.get(_INDEX) or ""
        tok = (self._fetch.get(_TOKEN_API) or "").strip()
        self._token = tok if _TOKEN_SHAPE.match(tok) else None
        slugs = sorted(set(_LINK.findall(html)))
        return [f"https://beachcam.meo.pt/livecams/{s}/" for s in slugs]

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        tm = _TITLE.search(html)
        title = unescape(tm.group(1)).strip() if tm else ""
        if not title:
            # last resort: the URL slug ("costa-da-caparica")
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        rm = _REGION.search(html)
        region = unescape(rm.group(1)).strip() if rm else None
        return "Beaches", (title, region)

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        targets = list(dict.fromkeys(_VIDEO.findall(html)))
        multi = len(targets) > 1
        for i, bare in enumerate(targets):
            # without a token the bare URL still ships: liveness drops it as a
            # dead manifest, which keeps a broken token endpoint visible.
            # quote the token (Wowza wmsAuthSign is base64 and can contain '+', which
            # a bare query would decode to a space -> 403); '=' padding stays literal.
            target = (
                f"{bare}?wmsAuthSign={quote(self._token, safe='=')}"
                if self._token
                else bare
            )
            yield Candidate(
                title="",
                angle_key=str(i) if multi else None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                # keyed on the BARE url: the auth token is per-build noise that
                # would break cross-build stability of the merge key
                predisc_key=predisc_key(bare),
            )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        title, region = ctx
        parts = ["Portugal"] + ([region] if region else [])
        return with_location_parts(title, parts)
