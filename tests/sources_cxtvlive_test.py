from __future__ import annotations

from webcam_aggregator.fetch import FetcherProtocol
from webcam_aggregator.sources.cxtvlive import CxtvliveSource


class _FakeFetcher:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0, /) -> str | None:
        return self._pages.get(url)


_SITEMAP = "<url><loc>https://www.cxtvlive.com/live-camera/acapulco-playa</loc></url>"
_CAM = (
    "<h1>Acapulco Playa</h1>"
    '<a href="https://www.cxtvlive.com/cameras/category/beaches">Beaches</a>'
    '<iframe src="https://www.youtube.com/embed/aaaaaaaaaaa"></iframe>'
)


def test_discovers_youtube_with_category() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            "https://www.cxtvlive.com/sitemap.xml": _SITEMAP,
            "https://www.cxtvlive.com/live-camera/acapulco-playa": _CAM,
        }
    )
    cands = list(CxtvliveSource(fetch=f).discover())
    assert cands
    assert cands[0].source == "cxtvlive"
    assert cands[0].category == "Beaches"
    assert cands[0].predisc_key == "yt:aaaaaaaaaaa"


def test_mjpeg_only_page_dropped() -> None:
    f: FetcherProtocol = _FakeFetcher(
        {
            "https://www.cxtvlive.com/sitemap.xml": "<loc>https://www.cxtvlive.com/live-camera/dead</loc>",
            "https://www.cxtvlive.com/live-camera/dead": "<h1>Dead</h1> no streamable media here",
        }
    )
    assert list(CxtvliveSource(fetch=f).discover()) == []
