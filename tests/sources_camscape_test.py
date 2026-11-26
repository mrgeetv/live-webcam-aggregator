import logging
from typing import TYPE_CHECKING

from webcam_aggregator.sources.camscape import CamscapeSource

if TYPE_CHECKING:
    import pytest

_BASE = "https://www.camscape.com"


def _cam_page(
    streams_json: str, tags: str = "beaches", locs: tuple[str, ...] = ()
) -> str:
    # Every page's nav links the seasonal category; only the where-links are the cam's.
    nav = '<a class="ni" href="/showing/christmas/">Christmas</a>'
    tag_html = "".join(
        f'<a class="where-link" href="/showing/{t}/">x</a>' for t in tags.split()
    )
    loc_html = "".join(
        f'<a class="where-link" href="/location/{loc}/">x</a>' for loc in locs
    )
    return (
        f"{nav}{loc_html}{tag_html}"
        f'<script id="streams-data" type="application/json">{streams_json}</script>'
    )


def _site(
    cams: dict[str, str], slugs: tuple[str, ...] = ("beaches",)
) -> dict[str, str]:
    """Build the index pages around `cams` (cam url -> page html).

    They are the category index, and a sitemap index pointing at one webcam sitemap
    listing every cam.
    """
    sitemap = f"{_BASE}/webcam-sitemap.xml"
    return {
        f"{_BASE}/showing/": "".join(f'<a href="/showing/{s}/">x</a>' for s in slugs),
        f"{_BASE}/sitemap_index.xml": f"<sitemap><loc>{sitemap}</loc></sitemap>",
        sitemap: "".join(f"<url><loc>{u}</loc></url>" for u in cams),
        **cams,
    }


class _FakeFetch:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return self._pages.get(url)


def test_camscape_discover_multistream_category_location_and_dedup() -> None:
    cam = f"{_BASE}/webcam/dawlish-webcams/"
    streams = (
        '[{"name":"Marine Parade Cam","url":"https://content.jwplatform.com/players/A.html"},'
        '{"name":"Salty Cottage Cam","url":"https://www.youtube.com/embed/aaaaaaaaaaa"}]'
    )
    pages = {
        **_site(
            {cam: _cam_page(streams, tags="beaches trains-railways", locs=("devon",))},
            ("beaches",),
        ),
    }
    cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert len(cands) == 2  # both angles
    titles = {c.title for c in cands}
    # names from the JSON, geo from the location where-link, category dropped
    assert "Marine Parade Cam — Devon" in titles
    assert "Salty Cottage Cam — Devon" in titles
    assert all(c.category == "Beaches" for c in cands)  # first mapped tag
    assert all(c.source == "camscape" for c in cands)
    yt = next(c for c in cands if "youtube" in c.target_url)
    assert yt.predisc_key == "yt:aaaaaaaaaaa"  # dedups with youtube-api


def test_camscape_ignores_the_nav_category_link() -> None:
    """Every page's nav links /showing/christmas/.

    An untagged cam must stay uncategorised (-> title fallback), not land in Seasonal.
    """
    cam = f"{_BASE}/webcam/x/"
    streams = '[{"name":"Live","url":"https://www.youtube.com/embed/ccccccccccc"}]'
    pages = _site({cam: _cam_page(streams, tags="")})
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.category is None


def test_camscape_reads_every_webcam_sitemap_and_only_those() -> None:
    a, b = f"{_BASE}/webcam/a/", f"{_BASE}/webcam/b/"
    streams = '[{{"name":"Live","url":"https://www.youtube.com/embed/{}"}}]'
    pages = {
        f"{_BASE}/sitemap_index.xml": (
            f"<loc>{_BASE}/webcam-sitemap.xml</loc>"
            f"<loc>{_BASE}/webcam-sitemap2.xml</loc>"
            f"<loc>{_BASE}/location-sitemap.xml</loc>"
        ),
        f"{_BASE}/webcam-sitemap.xml": f"<loc>{a}</loc>",
        f"{_BASE}/webcam-sitemap2.xml": f"<loc>{b}</loc>",
        f"{_BASE}/location-sitemap.xml": f"<loc>{_BASE}/webcam/not-fetched/</loc>",
        a: _cam_page(streams.format("d" * 11)),
        b: _cam_page(streams.format("e" * 11)),
    }
    cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert {c.source_page_url for c in cands} == {a, b}


def test_camscape_normalises_twitch_embed_for_ytdlp() -> None:
    cam = f"{_BASE}/webcam/x/"
    streams = '[{"name":"Live","url":"https://player.twitch.tv/?channel=foo&parent=www.camscape.com"}]'
    pages = {
        **_site({cam: _cam_page(streams, tags="cityscapes")}, ("cityscapes",)),
    }
    cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert cands[0].target_url == "https://www.twitch.tv/foo"


def test_camscape_resolves_camsecure_player_embed_to_its_hls() -> None:
    """Camsecure embeds are resolved at discovery, not left as a page URL.

    The m3u8 is the key dedup shares with the camsecure source, and it can't be derived
    from the embed URL (cityair1.html serves cityair.m3u8).
    """
    cam = f"{_BASE}/webcam/manchester-city-airport-heliport-cams/"
    player = "https://camsecure.co/httpswebcam/cityair/cityair1.html"
    pages = {
        **_site(
            {cam: _cam_page(f'[{{"name":"Apron","url":"{player}"}}]')}, ("beaches",)
        ),
        player: '<video><source src="/HLS/cityair.m3u8" type="application/x-mpegURL">',
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == "https://camsecure.co/HLS/cityair.m3u8"
    # the hls: key is what merges this with the camsecure source's own entry
    assert c.predisc_key == "hls:https://camsecure.co/HLS/cityair.m3u8"


def test_camscape_follows_a_camsecure_cam_page_to_its_player() -> None:
    """Some camscape embeds point at a camsecure CAM page rather than the player.

    That needs the extra iframe hop the camsecure source also does.
    """
    cam = f"{_BASE}/webcam/sennen-cove-beach-webcams/"
    page = "https://www.camsecure.co.uk/sennen_cove_webcam.html"
    player = "https://camsecure.co/httpswebcam/camsecure/sennen.html"
    pages = {
        **_site(
            {cam: _cam_page(f'[{{"name":"Sennen","url":"{page}"}}]')}, ("beaches",)
        ),
        page: f'<iframe src="{player}"></iframe>',
        player: '<video><source src="https://camsecure.uk/HLS/sennen.m3u8">',
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == "https://camsecure.uk/HLS/sennen.m3u8"


def test_camscape_keeps_an_unresolvable_camsecure_embed_visible() -> None:
    """If neither hop yields an HLS, the original URL stands.

    The cam still shows up as a no-extractor drop rather than vanishing silently.
    """
    cam = f"{_BASE}/webcam/x/"
    embed = "https://camsecure.co/httpswebcam/gone/gone.html"
    pages = {
        **_site({cam: _cam_page(f'[{{"name":"Gone","url":"{embed}"}}]')}, ("beaches",)),
        embed: "<h1>404</h1>",
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == embed


def test_camscape_leaves_a_direct_camsecure_m3u8_alone() -> None:
    """Many camsecure embeds are already the .m3u8.

    No hop needed, and fetching one as if it were a player page would be a wasted
    request.
    """
    cam = f"{_BASE}/webcam/newlyn-harbour-webcams/"
    m3u8 = "https://camsecure.co/HLS/newlyn.m3u8"
    pages = {
        **_site(
            {cam: _cam_page(f'[{{"name":"Newlyn","url":"{m3u8}"}}]')}, ("beaches",)
        ),
    }
    (c,) = list(CamscapeSource(_FakeFetch(pages)).discover())
    assert c.target_url == m3u8  # untouched; _FakeFetch has no entry for it


def test_camscape_unknown_tag_flagged_unmapped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cam = f"{_BASE}/webcam/drone-cam/"
    streams = '[{"name":"Drone","url":"https://www.youtube.com/embed/bbbbbbbbbbb"}]'
    pages = {
        **_site(
            {cam: _cam_page(streams, tags="drones")}, ("drones",)
        ),  # 'drones' isn't in _CATEGORY
    }
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.camscape"):
        cands = list(CamscapeSource(_FakeFetch(pages)).discover())
    # the unknown tag passes through as the raw slug -> map_category ->
    # "Unmapped Category"
    assert cands[0].category == "drones"
    assert any("drones" in r.getMessage() for r in caplog.records)  # crawl-first log
