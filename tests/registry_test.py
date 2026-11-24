from webcam_aggregator.registry import Registry


def test_host_rule_beats_m3u8_catchall() -> None:
    r = Registry(
        [(lambda u: "feratel" in u, "feratel"), (lambda u: u.endswith(".m3u8"), "hls")]
    )
    got = r.match("https://webtv.feratel.com/x.m3u8")
    assert got == "feratel"


def test_baltic_url_routes_to_baltic() -> None:
    r = Registry([(lambda u: "balticlivecam.com" in u, "baltic")])
    got = r.match("https://balticlivecam.com/cam")
    assert got == "baltic"


def test_no_match_returns_none() -> None:
    r = Registry([(lambda _u: False, "never")])
    assert r.match("https://x") is None
