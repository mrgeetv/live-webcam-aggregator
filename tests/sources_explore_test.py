from webcam_aggregator.sources.explore import ExploreOrgSource

_STREAMS_JSON = """{"streams":[
 {"id":1,"name":"Brown Bear Cam","state":"live",
  "playlistUrl":"https://outbound-production.explore.org/stream-production-1/.m3u8"},
 {"id":2,"name":"Great Horned Owl","state":"on_demand",
  "playlistUrl":"https://outbound-production.explore.org/stream-production-2/.m3u8"},
 {"id":3,"name":"Tropical Reef","state":"live",
  "playlistUrl":"https://outbound-production.explore.org/stream-production-3/.m3u8"},
 {"id":4,"name":"No Stream","state":"live","playlistUrl":""},
 {"id":5,"name":"Brown Bear (dup feed)","state":"live",
  "playlistUrl":"https://outbound-production.explore.org/stream-production-1/.m3u8"}
]}"""


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _STREAMS_JSON if "streams.json" in url else None


def test_explore_keeps_live_m3u8_and_dedups():
    cands = list(ExploreOrgSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # live + .m3u8 only: on_demand and empty-playlistUrl drop; the dup feed collapses
    assert set(by_title) == {"Brown Bear Cam", "Tropical Reef"}
    assert (
        by_title["Brown Bear Cam"].target_url
        == "https://outbound-production.explore.org/stream-production-1/.m3u8"
    )
    for c in cands:
        assert c.source == "explore"
        assert c.category is None  # no category in the feed -> "Other"
        assert (c.predisc_key or "").startswith("hls:")  # direct HLS


def test_explore_handles_missing_and_bad_json():
    class _Broken:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return "not json" if "streams.json" in url else None

    assert list(ExploreOrgSource(_Broken()).discover()) == []
