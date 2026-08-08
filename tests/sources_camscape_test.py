import pytest

from webcam_aggregator.sources.camscape import CamscapeSource

_BASE = "https://www.camscape.com"


def _cam_page(
    streams_json: str, tags: str = "beaches", locs: tuple[str, ...] = ()
) -> str:
    tag_html = "".join(f'<a href="/showing/{t}/">x</a>' for t in tags.split())
    loc_html = "".join(f'<a href="/location/{loc}/">x</a>' for loc in locs)
    return f'{tag_html}{loc_html}<script>var c={{"streams":{streams_json}}};</script>'


class _FakeFetch:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return self._pages.get(url)


def test_camscape_discover_multistream_category_location_and_dedup():
    cam = f"{_BASE}/webcam/dawlish-webcams/"
    streams = (
        '[{"name":"Marine Parade Cam","url":"https://content.jwplatform.com/players/A.html"},'
        '{"name":"Salty Cottage Cam","url":"https://www.youtube.com/embed/aaaaaaaaaaa"}]'
    )
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/beaches/">B</a>',
        f"{_BASE}/showing/beaches/": f'<a href="{cam}">d</a>',
        cam: _cam_page(
            streams, tags="beaches trains-railways", locs=("devon", "england")
        ),
    }
    cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert len(cands) == 2  # both angles
    titles = {c.title for c in cands}
    # names from the JSON, geo from /location tags (specific->general), category dropped
    assert "Marine Parade Cam — Devon, England" in titles
    assert "Salty Cottage Cam — Devon, England" in titles
    assert all(c.category == "Beaches" for c in cands)  # first mapped tag
    assert all(c.source == "camscape" for c in cands)
    yt = next(c for c in cands if "youtube" in c.target_url)
    assert yt.predisc_key == "yt:aaaaaaaaaaa"  # dedups with youtube-api


def test_camscape_normalises_twitch_embed_for_ytdlp():
    cam = f"{_BASE}/webcam/x/"
    streams = '[{"name":"Live","url":"https://player.twitch.tv/?channel=foo&parent=www.camscape.com"}]'
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/cityscapes/">C</a>',
        f"{_BASE}/showing/cityscapes/": f'<a href="{cam}">x</a>',
        cam: _cam_page(streams, tags="cityscapes"),
    }
    cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert cands[0].target_url == "https://www.twitch.tv/foo"


def test_camscape_resolves_camsecure_player_embed_to_its_hls():
    """camsecure embeds are resolved at discovery, not left as a page URL: the m3u8
    is the key dedup shares with the camsecure source, and it can't be derived from
    the embed URL (cityair1.html serves cityair.m3u8)."""
    cam = f"{_BASE}/webcam/manchester-city-airport-heliport-cams/"
    player = "https://camsecure.co/httpswebcam/cityair/cityair1.html"
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/beaches/">B</a>',
        f"{_BASE}/showing/beaches/": f'<a href="{cam}">d</a>',
        cam: _cam_page(f'[{{"name":"Apron","url":"{player}"}}]'),
        player: '<video><source src="/HLS/cityair.m3u8" type="application/x-mpegURL">',
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == "https://camsecure.co/HLS/cityair.m3u8"
    # the hls: key is what merges this with the camsecure source's own entry
    assert c.predisc_key == "hls:https://camsecure.co/HLS/cityair.m3u8"


def test_camscape_follows_a_camsecure_cam_page_to_its_player():
    """Some camscape embeds point at a camsecure CAM page rather than the player;
    that needs the extra iframe hop the camsecure source also does."""
    cam = f"{_BASE}/webcam/sennen-cove-beach-webcams/"
    page = "https://www.camsecure.co.uk/sennen_cove_webcam.html"
    player = "https://camsecure.co/httpswebcam/camsecure/sennen.html"
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/beaches/">B</a>',
        f"{_BASE}/showing/beaches/": f'<a href="{cam}">d</a>',
        cam: _cam_page(f'[{{"name":"Sennen","url":"{page}"}}]'),
        page: f'<iframe src="{player}"></iframe>',
        player: '<video><source src="https://camsecure.uk/HLS/sennen.m3u8">',
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == "https://camsecure.uk/HLS/sennen.m3u8"


def test_camscape_keeps_an_unresolvable_camsecure_embed_visible():
    """If neither hop yields an HLS the original URL stands, so the cam still shows
    up as a no-extractor drop rather than vanishing silently."""
    cam = f"{_BASE}/webcam/x/"
    embed = "https://camsecure.co/httpswebcam/gone/gone.html"
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/beaches/">B</a>',
        f"{_BASE}/showing/beaches/": f'<a href="{cam}">d</a>',
        cam: _cam_page(f'[{{"name":"Gone","url":"{embed}"}}]'),
        embed: "<h1>404</h1>",
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == embed


def test_camscape_leaves_a_direct_camsecure_m3u8_alone():
    """Many camsecure embeds are already the .m3u8 — no hop needed, and fetching one
    as if it were a player page would be a wasted request."""
    cam = f"{_BASE}/webcam/newlyn-harbour-webcams/"
    m3u8 = "https://camsecure.co/HLS/newlyn.m3u8"
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/beaches/">B</a>',
        f"{_BASE}/showing/beaches/": f'<a href="{cam}">d</a>',
        cam: _cam_page(f'[{{"name":"Newlyn","url":"{m3u8}"}}]'),
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == m3u8  # untouched; _FakeFetch has no entry for it


def test_camscape_unknown_tag_flagged_unmapped(caplog: pytest.LogCaptureFixture):
    import logging

    cam = f"{_BASE}/webcam/drone-cam/"
    streams = '[{"name":"Drone","url":"https://www.youtube.com/embed/bbbbbbbbbbb"}]'
    pages = {
        f"{_BASE}/showing/": f'<a href="{_BASE}/showing/drones/">D</a>',
        f"{_BASE}/showing/drones/": f'<a href="{cam}">x</a>',
        cam: _cam_page(streams, tags="drones"),  # 'drones' isn't in _CATEGORY
    }
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.camscape"):
        cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    # the unknown tag passes through as the raw slug -> map_category -> "Unmapped Category"
    assert cands[0].category == "drones"
    assert any("drones" in r.getMessage() for r in caplog.records)  # crawl-first log
