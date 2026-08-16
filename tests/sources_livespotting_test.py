import json
from pathlib import Path

from webcam_aggregator.sources.livespotting import LivespottingSource

_FIX = Path(__file__).parent / "fixtures"

# a second country page: one cam of its own, plus a repeat of a deutschland cam
# (a cam linked from two pages must yield one candidate) and an id whose API
# fetch fails (offline cam)
_GRIECHENLAND_HTML = """
<a href="/griechenland/alonia/1xueldy2" ><div class="col s5 thumb"></div></a>
<a href="/griechenland/alonia/dead0000" ></a>
<a href="/deutschland/altefaehr/4egsy980" ></a>
"""

_API = "https://player.livespotting.com/v2/livesource/"

_INFOS = {
    "4egsy980": {
        "id": "4egsy980",
        "name": "HD Livestream aus dem Seebad Altefähr auf Rügen",
        "city": "Altefähr",
        "country": "Deutschland",
        "source": "https://cdn.livespotting.com/vpu/elgcq5ks/4egsy980_hub.m3u8",
        "image": "https://cdn.livespotting.com/vpu/elgcq5ks/4egsy980.jpg",
    },
    # no `source` field (cam exists but has no stream) -> dropped
    "vgiy5566": {
        "id": "vgiy5566",
        "name": "Webcam in der Kieler Förde am Leuchtturm Friedrichsort",
        "city": "Kiel",
        "country": "Deutschland",
    },
    "1xueldy2": {
        "id": "1xueldy2",
        "name": "Webcam mit Blick über die Hügel von Pieria in Griechenland",
        "city": "Alonia",
        "country": "Griechenland",
        "source": "https://cdn.livespotting.com/vpu/10d4axm7/1xueldy2_hub.m3u8",
    },
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        if url == "https://www.livespotting.tv/deutschland":
            return (_FIX / "livespotting_country.html").read_text()
        if url == "https://www.livespotting.tv/griechenland":
            return _GRIECHENLAND_HTML
        if url.startswith(_API):
            cam_id = url.removeprefix(_API).split("?")[0]
            info = _INFOS.get(cam_id)
            return json.dumps(info) if info else None
        return None  # the other country pages: fetch failed


def test_livespotting_two_hop_discovery():
    cands = list(LivespottingSource(_FakeFetch()).discover())
    by_url = {c.target_url: c for c in cands}

    # 4egsy980 + 1xueldy2 survive; vgiy5566 has no stream URL, dead0000's API
    # fetch fails, and the cross-page repeat of 4egsy980 collapses to one
    assert set(by_url) == {
        "https://cdn.livespotting.com/vpu/elgcq5ks/4egsy980_hub.m3u8",
        "https://cdn.livespotting.com/vpu/10d4axm7/1xueldy2_hub.m3u8",
    }

    # location parts the name doesn't already carry get appended (city first)
    de = by_url["https://cdn.livespotting.com/vpu/elgcq5ks/4egsy980_hub.m3u8"]
    assert de.title == "HD Livestream aus dem Seebad Altefähr auf Rügen — Deutschland"
    gr = by_url["https://cdn.livespotting.com/vpu/10d4axm7/1xueldy2_hub.m3u8"]
    assert (
        gr.title
        == "Webcam mit Blick über die Hügel von Pieria in Griechenland — Alonia"
    )

    assert (
        de.source_page_url
        == "https://www.livespotting.tv/deutschland/altefaehr/4egsy980"
    )
    for c in cands:
        assert c.source == "livespotting"
        assert c.category is None  # no category anywhere -> "Other"
        assert (c.predisc_key or "").startswith("hls:")  # direct HLS, mergeable

    # the /vpu/<hash>/<id> thumbnail paths in the fixture never become cam ids
    assert not any("elgcq5ks" in c.source_page_url for c in cands)


def test_livespotting_survives_all_fetches_failing():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(LivespottingSource(_Dead()).discover()) == []
