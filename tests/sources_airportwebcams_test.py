from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.airportwebcams import AirportWebcamsSource

_INDEX = "https://airportwebcams.net/airport-webcams-by-webcam-speed/"
# nav category links + a still-image section after Streaming, both must be ignored
_INDEX_HTML = """
<p><strong><a href="https://airportwebcams.net/category/streaming/">Streaming</a> |
<a href="https://airportwebcams.net/category/1-to-5-sec/">1 to 5 sec</a></strong></p>
<p><strong>Streaming</strong></p>
<p><a href="https://airportwebcams.net/ahmosuo-airfield-webcam/">Ahmosuo Airport</a><br />
<a href="https://airportwebcams.net/amsterdam-schiphol-airport-webcam/">Amsterdam Schiphol Airport</a><br />
<a href="https://airportwebcams.net/zurich-airport-webcam/">Zurich Airport</a><br />
<a href="https://airportwebcams.net/tokyo-haneda-airport-webcam-2/">Tokyo Haneda Airport</a><br />
<a href="https://airportwebcams.net/aberporth-west-wales-airport-webcam/">Aberporth Airport</a></p>
<p><strong>1 to 5 Second refresh</strong></p>
<p><a href="https://airportwebcams.net/still-image-airport-webcam/">Still Image Airport</a></p>
"""

# every real page carries the site's own footer channel in the user/… form
_FOOTER = '<a href="https://www.youtube.com/user/AirportWebcams">our channel</a>'


def _page(title: str, body: str) -> str:
    return f'<h2><a href="x" rel="bookmark" title="Permanent Link to {title}">{title}</a></h2>{body}{_FOOTER}'


_PAGES = {
    _INDEX: _INDEX_HTML,
    # single 24/7 channel -> one /live candidate, no angle key
    "https://airportwebcams.net/ahmosuo-airfield-webcam/": _page(
        "Ahmosuo Airfield Webcam",
        '<a href="https://www.youtube.com/@AhmoCamv2/streams">Ahmo Cam</a>',
    ),
    # multiple channels; the occasional-streamer section below the marker is skipped
    "https://airportwebcams.net/amsterdam-schiphol-airport-webcam/": _page(
        "Amsterdam Schiphol Airport Webcam",
        '<a href="https://www.youtube.com/@LiveVisionNL/streams">cam 1</a>'
        '<a href="https://www.youtube.com/channel/UCabc123/streams">cam 2</a>'
        "<h2>Live streamers with regular broadcasts:</h2>"
        '<a href="https://www.youtube.com/@V1aviation/streams">spotter</a>',
    ),
    # channels ONLY in the occasional section -> still used (Zurich's real shape)
    "https://airportwebcams.net/zurich-airport-webcam/": _page(
        "Zurich Airport Webcam",
        "<h2>Live streamers with regular broadcasts:</h2>"
        '<a href="https://www.youtube.com/@ZurichAirportSpotter/streams">spotter</a>',
    ),
    # no channel links at all: the standard ladder picks up the direct video embed
    "https://airportwebcams.net/tokyo-haneda-airport-webcam-2/": _page(
        "Tokyo Haneda Airport Webcam",
        '<iframe src="https://www.youtube.com/embed/0ytmbJ6mn70"></iframe>',
    ),
    # cam is an external website with no extractable embed -> yields nothing
    "https://airportwebcams.net/aberporth-west-wales-airport-webcam/": _page(
        "Aberporth West Wales Airport Webcam",
        '<a href="http://www.flyuav.co.uk/webcam/">view the webcam</a>',
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def _discover() -> list[Candidate]:
    return list(AirportWebcamsSource(_FakeFetch()).discover())


def test_only_streaming_section_pages_are_scraped():
    pages = AirportWebcamsSource(
        _FakeFetch()
    )._page_urls()  # pyright: ignore[reportPrivateUsage]
    assert "https://airportwebcams.net/still-image-airport-webcam/" not in pages
    assert not any("/category/" in u for u in pages)
    assert len(pages) == 5


def test_channels_become_live_targets_with_titles_and_category():
    cands = _discover()
    by_target = {c.target_url: c for c in cands}

    ahmo = by_target["https://www.youtube.com/@AhmoCamv2/live"]
    assert ahmo.title == "Ahmosuo Airfield"  # trailing "Webcam" stripped
    assert ahmo.angle_key is None  # single cam -> no angle
    assert ahmo.predisc_key is None  # /live resolves per-build; never merged

    for c in cands:
        assert c.source == "airportwebcams"
        assert c.category == "Airports"

    # the site's own footer channel (user/… form) never becomes a candidate
    assert not any("AirportWebcams" in c.target_url for c in cands)


def test_occasional_streamers_skipped_unless_page_has_nothing_else():
    by_page: dict[str, list[Candidate]] = {}
    for c in _discover():
        by_page.setdefault(c.source_page_url, []).append(c)

    ams = by_page["https://airportwebcams.net/amsterdam-schiphol-airport-webcam/"]
    # the two 24/7 channels kept, the post-marker spotter dropped
    assert sorted(c.angle_key or "" for c in ams) == [
        "@LiveVisionNL",
        "channel/UCabc123",
    ]
    assert all(c.target_url.endswith("/live") for c in ams)

    zrh = by_page["https://airportwebcams.net/zurich-airport-webcam/"]
    assert [c.target_url for c in zrh] == [
        "https://www.youtube.com/@ZurichAirportSpotter/live"
    ]

    # no page.channels at all -> the ladder's video-embed fallback, with a yt: key
    hnd = by_page["https://airportwebcams.net/tokyo-haneda-airport-webcam-2/"]
    assert [c.predisc_key for c in hnd] == ["yt:0ytmbJ6mn70"]
    assert hnd[0].title == "Tokyo Haneda Airport"

    # nothing extractable -> page contributes nothing
    assert (
        "https://airportwebcams.net/aberporth-west-wales-airport-webcam/" not in by_page
    )
