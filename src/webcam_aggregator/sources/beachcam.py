"""beachcam.meo.pt source: Portuguese beach cams, blanket category Beaches."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, override

from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import (
    HtmlScraperSource,
    decode_entities,
    predisc_key,
    with_location_parts,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

_INDEX = "https://beachcam.meo.pt/livecams/"
# Index links are absolute; the bare /livecams/ index itself has an empty slug and
# never matches.
_LINK = re.compile(r'href="https?://beachcam\.meo\.pt/livecams/([^"/#?]+)/"')
# Each cam page carries its stream in the player div's data-video-url attribute
# (video-auth1.iol.pt/auth-beachcam/<slug>/playlist.m3u8). The shared ladder is
# deliberately not used: every page's footer links the site's
# youtube.com/@Beachcamportugal channel, which the ladder's channel rule would turn
# into a bogus extra candidate on every page.
_VIDEO = re.compile(r'data-video-url="(https?://[^"]+\.m3u8)"')
# That playlist answers 403 bare: extractors.beachcam appends the auth token it
# needs at resolve time, so the target stays the bare URL.
_TITLE = re.compile(r'<[^<>]*liveCamsHeader__title">([^<]*)<')
# the header label names the cam's municipality/region: "LIVECAMS / Cascais"
_REGION = re.compile(r'<[^<>]*liveCamsHeader__label">LIVECAMS\s*/([^<]*)<')

# ctx = (page title, region from the header label)
_Ctx = tuple[str, str | None]


class BeachcamSource(HtmlScraperSource[_Ctx]):
    """beachcam.meo.pt: MEO Beachcam livecams, blanket-categorised "Beaches".

    Almost all are beach/surf spots, and the few lake/wake-park/city outliers are a
    better trade than every Portuguese-titled cam landing in "Other" (the
    English title-keyword fallback can't read them). One stream per page in a
    data-video-url attribute.
    """

    name: str = "beachcam"

    @override
    def _page_urls(self) -> list[str]:
        html = self._fetch.get(_INDEX) or ""
        slugs = sorted(set(_LINK.findall(html)))
        return [f"https://beachcam.meo.pt/livecams/{s}/" for s in slugs]

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        tm = _TITLE.search(html)
        title = decode_entities(tm.group(1)).strip() if tm else ""
        if not title:
            # last resort: the URL slug ("costa-da-caparica")
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        rm = _REGION.search(html)
        region = decode_entities(rm.group(1)).strip() if rm else None
        return "Beaches", (title, region)

    @override
    def _candidates(self, html: str, url: str) -> Iterable[Candidate]:
        targets = list(dict.fromkeys(_VIDEO.findall(html)))
        multi = len(targets) > 1
        for i, target in enumerate(targets):
            yield Candidate(
                title="",
                angle_key=str(i) if multi else None,
                category=None,
                source=self.name,
                source_page_url=url,
                target_url=target,
                predisc_key=predisc_key(target),
            )

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        title, region = ctx
        parts = ["Portugal"] + ([region] if region else [])
        return with_location_parts(title, parts)
