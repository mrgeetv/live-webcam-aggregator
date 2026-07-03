from webcam_aggregator.sources.wildlife_trusts import WildlifeTrustsSource

_INDEX = "https://www.wildlifetrusts.org/webcams"
_INDEX_HTML = """
<a href="https://www.cumbriawildlifetrust.org.uk/osprey-webcam">Cumbria Wildlife Trust Foulshaw Moss ospreys Watch live</a>
<a href="https://www.durhamwt.com/kittiwake-cam">Durham Wildlife Trust Durham&#039;s kittiwakes</a>
<a href="https://www.essexwt.org.uk/wildlife/webcams/badger">Essex Wildlife Trust Essex&#039;s badgers Watch</a>
<a href="https://www.facebook.com/sharer?u=x">Share</a>
<a href="https://www.wildlifetrusts.org/get-involved">Get involved</a>
"""

_PAGES = {
    _INDEX: _INDEX_HTML,
    "https://www.cumbriawildlifetrust.org.uk/osprey-webcam": (
        '<iframe src="https://www.youtube.com/embed/aaaaaaaaaaa"></iframe>'
    ),
    "https://www.durhamwt.com/kittiwake-cam": (
        '<a href="https://www.youtube.com/watch?v=bbbbbbbbbbb">live</a>'
    ),
    # consent/JS-gated: no youtube id in static HTML -> yields nothing, drops
    "https://www.essexwt.org.uk/wildlife/webcams/badger": "<p>loading…</p>",
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_wildlife_trusts_extracts_youtube_titles_and_category():
    cands = list(WildlifeTrustsSource(_FakeFetch()).discover())
    by_pred = {c.predisc_key: c for c in cands}

    # the two pages with a static YouTube embed; the JS-gated badger page drops
    assert set(by_pred) == {"yt:aaaaaaaaaaa", "yt:bbbbbbbbbbb"}

    # titles: the "<Region> Wildlife Trust" prefix + trailing "Watch…" stripped
    assert by_pred["yt:aaaaaaaaaaa"].title == "Foulshaw Moss ospreys"
    assert by_pred["yt:bbbbbbbbbbb"].title == "Durham's kittiwakes"

    for c in cands:
        assert c.source == "wildlife-trusts"
        assert c.category == "Animals"  # all wildlife cams
        assert "youtube.com" in c.target_url

    # social + nav links are never treated as cams
    assert not any(
        "facebook" in c.target_url or "get-involved" in c.target_url for c in cands
    )
