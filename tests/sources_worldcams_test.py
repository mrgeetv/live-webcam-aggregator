from webcam_aggregator.fetch import FetcherProtocol
from webcam_aggregator.sources.worldcams import WorldcamsSource


class _FakeFetcher:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return self._pages.get(url)


_LIST = (
    '<div class="cam-promo__title">'
    '<a href="/norway/finse/railroad-station">A</a></div>'
)
_CAM = (
    "<h1>Finse Railroad Station</h1>"
    'Category: &nbsp;<a href="/trains/">Trains</a>'
    'streams[0] = "<iframe src=\\"https://www.youtube.com/embed/aaaaaaaaaaa\\"></iframe>";'
)


def test_discovers_with_title_and_category() -> None:
    pages = {
        "https://worldcams.tv/list/?page=1": _LIST,
        "https://worldcams.tv/list/?page=2": "",  # empty → stop
        "https://worldcams.tv/norway/finse/railroad-station": _CAM,
    }
    fetcher: FetcherProtocol = _FakeFetcher(pages)
    cands = list(WorldcamsSource(fetch=fetcher).discover())
    assert cands
    assert all(c.source == "worldcams" for c in cands)
    # only the parts the h1 doesn't already name are appended (place is in the h1)
    assert cands[0].title == "Finse Railroad Station — Norway"
    assert cands[0].category == "Trains"
    assert cands[0].predisc_key == "yt:aaaaaaaaaaa"


def test_stops_when_page_returns_no_links() -> None:
    pages = {
        "https://worldcams.tv/list/?page=1": "<html>no cam-promo links here</html>",
    }
    urls_fetched: list[str] = []

    class _TrackingFetcher:
        _urls: list[str]

        def __init__(self, tracked: list[str]) -> None:
            self._urls = tracked

        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            self._urls.append(url)
            return pages.get(url, "")

    fetcher: FetcherProtocol = _TrackingFetcher(urls_fetched)
    cands = list(WorldcamsSource(fetch=fetcher).discover())
    assert cands == []
    # Should only have fetched the first list page (then stopped — no links)
    list_fetches = [u for u in urls_fetched if "/list/?page=" in u]
    assert list_fetches == ["https://worldcams.tv/list/?page=1"]


def test_skips_camera_page_returning_none() -> None:
    pages: dict[str, str] = {
        "https://worldcams.tv/list/?page=1": _LIST,
        "https://worldcams.tv/list/?page=2": "",
        # cam-a deliberately absent → fetcher returns None
    }
    fetcher: FetcherProtocol = _FakeFetcher(pages)
    cands = list(WorldcamsSource(fetch=fetcher).discover())
    assert cands == []


_MULTI_LIST = (
    '<div class="cam-promo__title">' '<a href="/spain/gran-canaria/beaches">B</a></div>'
)
_MULTI_CAM = (
    "<h1>Gran Canaria Beaches Webcam</h1>"
    'Category: &nbsp;<a href="/beaches/">Beaches</a>'
    '<a class="streams__item active" id="streams__item_0" href="#"'
    ' onClick="SetStream(0,1); return false;">Playa del Inglés</a>'
    '<a class="streams__item" id="streams__item_1378" href="#"'
    ' onClick="SetStream(1378,1); return false;">Maspalomas Beach</a>'
    "streams[0] = '<iframe src=\\\"https://www.youtube.com/embed/aaaaaaaaaaa\\\"></iframe>';"
    "streams[1378] = '<iframe src=\\\"https://www.youtube.com/embed/bbbbbbbbbbb\\\"></iframe>';"
)


def test_multi_stream_page_uses_per_cam_names() -> None:
    # A roundup page that embeds several cams must give each its own selector name
    # (keyed by stream-id), not the shared page <h1> for all of them.
    pages = {
        "https://worldcams.tv/list/?page=1": _MULTI_LIST,
        "https://worldcams.tv/list/?page=2": "",
        "https://worldcams.tv/spain/gran-canaria/beaches": _MULTI_CAM,
    }
    cands = list(WorldcamsSource(fetch=_FakeFetcher(pages)).discover())
    titles = {c.title for c in cands}
    # category ("Beaches") is dropped from the suffix (shown as the group)
    assert "Playa del Inglés — Gran Canaria, Spain" in titles
    assert "Maspalomas Beach — Gran Canaria, Spain" in titles
    # keyed by the worldcams stream-id → distinct, reorder-stable /stream ids
    assert {c.angle_key for c in cands} == {"0", "1378"}
