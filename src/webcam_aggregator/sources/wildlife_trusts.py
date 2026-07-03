from __future__ import annotations

import re
from typing import override

from ..fetch import FetcherProtocol
from ..models import Candidate
from .base import HtmlScraperSource

_INDEX = "https://www.wildlifetrusts.org/webcams"
_LINK = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.S)
# external cam pages on the regional trust sites (drop social/share/nav/asset links)
_IS_CAM = re.compile(r"(webcam|-cam|/cam|camera|live[- ])", re.I)
_NOT_CAM = re.compile(
    r"facebook|twitter|linkedin|wa\.me|sharer|instagram|whatsapp|googletag|/files/"
    r"|\.(jpg|png|css|js)|wildlifetrusts\.org/(webcams|get-)",
    re.I,
)
_TRUST_PREFIX = re.compile(
    r"^.*?Wildlife Trust\s+", re.I
)  # "<Region> Wildlife Trust …"
_TITLE_TAIL = re.compile(r"\s+(Watch|See|View|Live\b|Click)\b.*$", re.I)


def _clean_title(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"&#0?39;|&apos;|&rsquo;", "'", t)
    t = re.sub(r"&[a-z]+;|&#\d+;", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _TRUST_PREFIX.sub("", t)
    t = _TITLE_TAIL.sub("", t)
    return t[:60].strip()


class WildlifeTrustsSource(HtmlScraperSource[str]):
    """The Wildlife Trusts webcams index links out to ~17 regional-trust cam pages, mostly
    YouTube embeds the standard ladder resolves. ctx is the index link text (regional
    prefix stripped); they're all wildlife cams -> category Animals. Pages whose embed is
    JS/consent-gated (no id in the static HTML) yield nothing and drop."""

    name: str = "wildlife-trusts"
    _titles: dict[str, str]

    @override
    def __init__(self, fetch: FetcherProtocol) -> None:
        super().__init__(fetch)
        self._titles = {}

    @override
    def _page_urls(self) -> list[str]:
        idx = self._fetch.get(_INDEX) or ""
        for href, text in _LINK.findall(idx):
            if _IS_CAM.search(href) and not _NOT_CAM.search(href):
                self._titles.setdefault(href, _clean_title(text))
        return sorted(self._titles)

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, str]:
        return "Animals", self._titles.get(url, "")

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: str
    ) -> str:
        return ctx
