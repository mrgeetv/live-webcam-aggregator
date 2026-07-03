from webcam_aggregator.sources.earthcam import EarthCamSource

# get_locations_network wraps places in {"data":[...]}; get_locations is a bare list.
_NETWORK_JSON = """{"status":"200","data":[{"places":[
 {"name":"Abbey Road Crossing Cam","url":"https://www.earthcam.com/world/england/london/abbeyroad/","country":"England","city":"London"},
 {"name":"Times Square","url":"https://www.earthcam.com/usa/newyork/timessquare/?cam=tsrobo1","country":"United States","state":"New York","city":"New York"},
 {"name":"Client Landing","url":"https://www.earthcam.com/clients/uky/","country":"United States"},
 {"name":"My Cam","url":"http://myearthcam.com/seaair"}
]}]}"""
_GLOBAL_JSON = """[{"places":[
 {"name":"Some Beach","url":"https://www.youtube.com/watch?v=aaaaaaaaaaa","country":"Spain","city":"Barcelona"},
 {"name":"Riga Old Town","url":"https://balticlivecam.com/cameras/riga/old-town/","country":"Latvia","city":"Riga"},
 {"name":"Steyr","url":"http://www.steyr.at/webcam","country":"Austria"},
 {"name":"Dup YT","url":"https://youtu.be/aaaaaaaaaaa"}
]}]"""


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        if "get_locations_network" in url:
            return _NETWORK_JSON
        if "get_locations" in url:
            return _GLOBAL_JSON
        return None


def test_earthcam_keeps_routable_drops_unservable_and_dedups():
    cands = list(EarthCamSource(_FakeFetch()).discover())
    by_target = {c.target_url: c for c in cands}

    # EarthCam-own geographic cam pages kept (/world/ + /usa/)
    abbey = "https://www.earthcam.com/world/england/london/abbeyroad/"
    assert abbey in by_target
    assert any("/usa/newyork/timessquare/" in t for t in by_target)

    # partner YouTube normalised to a watch URL + de-duped across BOTH feeds (one, not two)
    yt = [c for c in cands if c.predisc_key == "yt:aaaaaaaaaaa"]
    assert len(yt) == 1
    assert yt[0].target_url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"

    # partner balticlivecam kept (routes to our baltic extractor)
    assert any("balticlivecam.com" in t for t in by_target)

    # unservable dropped: /clients/ landing page, myearthcam root, external partner site
    assert not any("/clients/" in t for t in by_target)
    assert not any("myearthcam.com" in t for t in by_target)
    assert not any("steyr.at" in t for t in by_target)

    # no content category in the feed -> all "Other"; titles carry the geo
    assert all(c.source == "earthcam" and c.category is None for c in cands)
    assert by_target[abbey].title == "Abbey Road Crossing Cam — London, England"


def test_earthcam_handles_missing_and_bad_json():
    class _Broken:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return "not json at all" if "network" in url else None

    assert list(EarthCamSource(_Broken()).discover()) == []
