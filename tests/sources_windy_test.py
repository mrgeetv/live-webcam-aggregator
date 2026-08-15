import json
import re

from webcam_aggregator.sources.windy import WindySource

_NEARBY = re.compile(r"nearby=(-?[\d.]+),(-?[\d.]+).*offset=(\d+)")
_STREAM = re.compile(r"/webcams/stream/(\d+)$")

# three cams somewhere on the grid: one live feratel embed, one live direct m3u8,
# one not-live stub. total=3 fits one page, so no pagination requests happen.
_CAMS = {
    "cams": [
        {
            "id": 101,
            "title": "Innsbruck: Bergisel",
            "location": {"city": "Innsbruck", "country": "Austria"},
        },
        {
            "id": 202,
            "title": "Malibu Pier",
            "location": {"city": "Malibu", "country": "United States"},
        },
        {
            "id": 303,
            "title": "Quiet Meadow",
            "location": {"city": "Nowhere", "country": "Nowhere"},
        },
    ],
    "total": 3,
}

_PAD = "<!-- " + "x" * 300 + " -->"  # push live pages past the stub-size floor
_PAGES = {
    101: (
        _PAD + '<iframe src="https://webtv.feratel.com/webtv/'
        '?cam&#x3D;5751&amp;lg&#x3D;en"></iframe>'
    ),
    202: (
        _PAD + '<iframe src="https://wzmedia.dot.ca.gov/D7/malibu.stream'
        '/playlist.m3u8"></iframe>'
    ),
    # the "not available" stub: 200 but tiny, must be treated as not live
    303: "<div>Sorry. The live stream for this webcam is currently not available.</div>",
}


class _FakeFetch:
    def __init__(self) -> None:
        self.stream_fetches: list[int] = []
        self._served_cams: bool = False

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        sm = _STREAM.search(url)
        if sm:
            cid = int(sm.group(1))
            self.stream_fetches.append(cid)
            return _PAGES.get(cid)
        if _NEARBY.search(url):
            # the first grid point returns the cams; the rest of the world is empty
            if not self._served_cams:
                self._served_cams = True
                return json.dumps(_CAMS)
            return json.dumps({"cams": [], "total": 0})
        return None


def test_windy_enumerates_probes_and_extracts_provider_embeds():
    fetch = _FakeFetch()
    src = WindySource(fetch)
    cands = {c.target_url: c for c in src.discover()}

    # the two live cams yield their provider embeds; the stub page yields nothing
    feratel = cands["https://webtv.feratel.com/webtv/?cam=5751&lg=en"]
    hls = cands["https://wzmedia.dot.ca.gov/D7/malibu.stream/playlist.m3u8"]
    assert len(cands) == 2

    # entity-encoded iframe src decoded, shared predisc keys assigned -> dedup
    # against the first-party sources does real work
    assert feratel.predisc_key == "feratel:5751"
    assert (hls.predisc_key or "").startswith("hls:")
    assert feratel.title == "Innsbruck: Bergisel — Austria"
    # the city is already in the title, so only the country gets appended
    assert hls.title == "Malibu Pier — United States"
    for c in cands.values():
        assert c.source == "windy"
        assert c.category is None


def test_windy_state_persists_and_reprobes_live_first():
    fetch = _FakeFetch()
    src = WindySource(fetch)
    list(src.discover())
    first_round = list(fetch.stream_fetches)
    assert sorted(set(first_round)) == [101, 202, 303]

    fetch.stream_fetches.clear()
    cands = list(src.discover())
    # second build: no re-enumeration (corpus cached), live ids re-probed first
    assert fetch.stream_fetches[:2] == [101, 202]
    assert len(cands) == 2


def test_windy_survives_dead_api():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(WindySource(_Dead()).discover()) == []
