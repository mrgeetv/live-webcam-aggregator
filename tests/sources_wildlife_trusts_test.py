import time
from collections import Counter

from webcam_aggregator.models import Candidate, stable_id
from webcam_aggregator.sources.wildlife_trusts import (
    _TAG,  # pyright: ignore[reportPrivateUsage]
    WildlifeTrustsSource,
)

_INDEX = "https://www.wildlifetrusts.org/webcams"
_GTM = '<iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe>'
_CHROME = '<a href="https://www.youtube.com/channel/UCtrust/live">Our channel</a>'
_YT = "https://www.youtube.com/watch?v="


def _card(href: str, title: str) -> str:
    return (
        '<div class="paragraph paragraph--type--featured-cards-custom card">'
        f'<a href="{href}"><picture></picture><span class="card__meta">X Wildlife'
        f' Trust</span><h3 class="card__title field">{title}</h3>Watch live</a></div>'
    )


def _urlset(*paths: str, host: str = "https://www.essexwt.org") -> str:
    return (
        "<urlset>"
        + "".join(f"<url><loc>{host}/{p}</loc></url>" for p in paths)
        + "</urlset>"
    )


def _oembed(video: str) -> str:
    return (
        "https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fwww.youtube.com"
        f"%2Fwatch%3Fv%3D{video}"
    )


_PAGES = {
    _INDEX: "".join(
        [
            _card("https://www.essexwt.org/owl-cam", "Essex&#039;s barn owls"),
            _card(f"{_YT}aaaaaaaaaaa", "Rutland ospreys"),
            _card("https://www.youtube.com/@Puffins/streams", "Skomer puffins"),
            # the same video the hub page embeds, in another URL shape
            _card("https://www.youtube.com/live/eeeeeeeeeee?feature=share", "Bats"),
            _card("https://www.essexwt.org/feeder", "Garden feeder"),
            _card("https://www.essexwt.org/marsh", "Marsh"),
            _card("https://www.essexwt.org/pine", "Pine marten"),
            '<a href="https://www.facebook.com/sharer?u=x">Share</a>',
        ]
    ),
    "https://www.wildlifetrusts.org/wildlife-trusts": (
        '<a href="http://www.essexwt.org/" class="button" itemprop="url">Visit</a>'
        '<a href="https://www.essexwt.org/donate">Donate</a>'
        # listed bare; its sitemap index names the www host
        '<a href="https://durham.example/" itemprop="url">Visit</a>'
    ),
    "https://www.essexwt.org/sitemap.xml": (
        "<sitemapindex><sitemap><loc>https://www.essexwt.org/sitemap.xml?page=1</loc>"
        "</sitemap><sitemap><loc>https://elsewhere.example/sitemap.xml</loc>"
        "</sitemap><sitemap><loc>https://www.essexwt.org.evil.example/s.xml</loc>"
        "</sitemap></sitemapindex>"
    ),
    "https://www.essexwt.org/sitemap.xml?page=1": _urlset(
        "wildlife/webcams",
        "barn-owl-cam-update",
        "osprey-blogs",
        "rutlandospreys",
        "donate",
        "blog/owl-cam-is-back",
        "how-to-trail-camera",
    ),
    # Off-site sitemaps hold cam-named pages: following one would fetch them.
    "https://elsewhere.example/sitemap.xml": _urlset("webcam", host="https://e.x"),
    "https://www.essexwt.org.evil.example/s.xml": _urlset(
        "webcam", host="https://www.essexwt.org.evil.example"
    ),
    "https://durham.example/sitemap.xml": (
        "<sitemapindex><sitemap><loc>https://www.durham.example/sitemap.xml?page=1"
        "</loc></sitemap></sitemapindex>"
    ),
    "https://www.durham.example/sitemap.xml?page=1": (
        "<urlset><url><loc><![CDATA[https://www.durham.example/kittiwake-live]]>"
        "</loc></url></urlset>"
    ),
    "https://www.durham.example/kittiwake-live": (
        '<iframe src="https://www.youtube.com/embed/fffffffffff"></iframe>'
    ),
    # The hub: a consent-gated YouTube with a plugin-quoted title; a camstreamer and a
    # camsecure behind the GTM iframe; a tokened streamdays script; a playlist; a
    # channel live embed; the channel link every page's chrome carries.
    "https://www.essexwt.org/wildlife/webcams": (
        "<title>Webcams | Essex Wildlife Trust</title>"
        + _GTM
        + _CHROME
        + (
            '<iframe src="about:blank" data-src-cmplz="https://www.youtube.com/embed/'
            'bbbbbbbbbbb" title="Youtube video &quot;Barn owl nest box&quot;"></iframe>'
            '<iframe src="https://camstreamer.com/embed/abc" title="YouTube"></iframe>'
            '<iframe src="https://camsecure.uk/httpswebcam/ewt/ewt1.html"></iframe>'
            '<script src="https://live.streamdays.com/6o8zzn7m"></script>'
            '<iframe src="https://www.youtube.com/embed/videoseries?list=PLx"></iframe>'
            '<iframe data-src="https://www.youtube-nocookie.com/embed/live_stream?'
            'autoplay=1&amp;channel=UCbats"></iframe>'
            '<iframe src="https://www.youtube.com/embed/eeeeeeeeeee"></iframe>'
        )
    ),
    "https://camstreamer.com/embed/abc": (
        '<iframe src="https://www.youtube.com/embed/ccccccccccc?autoplay=1">'
    ),
    "https://camsecure.uk/httpswebcam/ewt/ewt1.html": (
        '<video><source src="https://camsecure.uk/HLS/ewt.m3u8"></video>'
    ),
    # Two linked cams: one titled by YouTube's oEmbed, the other by the page.
    "https://www.essexwt.org/rutlandospreys": (
        "<title>Rutland ospreys | Essex Wildlife Trust</title>"
        + _CHROME
        + f'<a href="{_YT}aaaaaaaaaaa">Bay</a>'
        + f'<a href="{_YT}ddddddddddd">Lagoon</a>'
    ),
    _oembed("ddddddddddd"): '{"title": "Lagoon 4 nest", "type": "video"}',
    # An article whose only YouTube is the owl channel's link; the owl-cam page
    # embeds it.
    "https://www.essexwt.org/barn-owl-cam-update": (
        "<title>Barn owl update | Essex Wildlife Trust</title>"
        '<a href="https://www.youtube.com/channel/UCowls/live">Watch</a>'
    ),
    # A bare channel link alone on a sitemap page is not a cam.
    "https://www.essexwt.org/osprey-blogs": (
        '<a href="https://www.youtube.com/@OspreyProject/live">Our channel</a>'
    ),
    # The channel's current live, behind a consent placeholder: the ladder's iframe
    # fallback must not add the placeholder's about:blank as a second cam.
    "https://www.essexwt.org/owl-cam": (
        '<iframe src="about:blank" data-src-cmplz="//www.youtube.com/embed/'
        'live_stream?channel=UCowls"></iframe>'
        '<a href="https://www.youtube.com/channel/UCowls/live">Watch on YouTube</a>'
    ),
    # A card page whose only YouTube is the trust's channel link: that IS the cam.
    "https://www.essexwt.org/feeder": _CHROME,
    # Pages with nothing for the ladder but its iframe fallback: a consent
    # placeholder (about:blank) and a bare player URL. One cam each, not two.
    "https://www.essexwt.org/marsh": (
        '<iframe src="about:blank" data-src-cmplz="https://camsecure.uk/httpswebcam/'
        'ewt/ewt2.html"></iframe>'
    ),
    "https://www.essexwt.org/pine": (
        '<iframe src="https://camsecure.uk/httpswebcam/ewt/ewt3.html"></iframe>'
    ),
    "https://camsecure.uk/httpswebcam/ewt/ewt2.html": (
        '<video><source src="https://camsecure.uk/HLS/ewt2.m3u8"></video>'
    ),
    "https://camsecure.uk/httpswebcam/ewt/ewt3.html": (
        '<video><source src="https://camsecure.uk/HLS/ewt3.m3u8"></video>'
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def _discover() -> dict[str, Candidate]:
    return {c.target_url: c for c in WildlifeTrustsSource(_FakeFetch()).discover()}


def test_wildlife_trusts_finds_cams_from_cards_sitemaps_and_every_embed_form() -> None:
    assert set(_discover()) == {
        # index cards: a YouTube card is the cam, a channel tab becomes its live
        f"{_YT}aaaaaaaaaaa",
        "https://www.youtube.com/@Puffins/live",
        # a card page with only the trust's channel link
        "https://www.youtube.com/channel/UCtrust/live",
        # a consent-gated channel-live embed
        "https://www.youtube.com/channel/UCowls/live",
        # the hub: consent YouTube, camstreamer -> its YouTube, camsecure -> its HLS,
        # streamdays kept as a visible no-extractor drop, a channel-live embed beside
        # other cams, and a video also carded under another URL shape
        f"{_YT}bbbbbbbbbbb",
        f"{_YT}ccccccccccc",
        "https://camsecure.uk/HLS/ewt.m3u8",
        "https://camsecure.uk/HLS/ewt2.m3u8",
        "https://camsecure.uk/HLS/ewt3.m3u8",
        "https://live.streamdays.com/6o8zzn7m",
        "https://www.youtube.com/channel/UCbats/live",
        f"{_YT}eeeeeeeeeee",
        # sitemap pages the index never links, one on a www host the trust lists bare
        f"{_YT}ddddddddddd",
        f"{_YT}fffffffffff",
    }


def test_wildlife_trusts_names_each_cam_from_its_best_source() -> None:
    cams = _discover()
    # card title, unescaped; reached by an embed (owl-cam) and a link (the article)
    owls = cams["https://www.youtube.com/channel/UCowls/live"]
    assert owls.title == "Essex's barn owls"
    assert owls.source_page_url == "https://www.essexwt.org/owl-cam"
    assert cams["https://www.youtube.com/@Puffins/live"].title == "Skomer puffins"
    # a plugin's quoting unwrapped; a generic "YouTube" iframe title ignored
    assert cams[f"{_YT}bbbbbbbbbbb"].title == "Barn owl nest box"
    # a page's untitled cams are numbered after it (the trust suffix stripped)
    assert cams[f"{_YT}ccccccccccc"].title == "Webcams 1"
    assert cams["https://camsecure.uk/HLS/ewt.m3u8"].title == "Webcams 2"
    # an untitled YouTube beside another cam takes the video's oEmbed title...
    assert cams[f"{_YT}ddddddddddd"].title == "Lagoon 4 nest"
    # ...and a cam a page only links loses to the card that names it
    rutland = cams[f"{_YT}aaaaaaaaaaa"]
    assert (rutland.title, rutland.source_page_url) == ("Rutland ospreys", _INDEX)
    # a page whose only cam the ladder's fallback re-reads still has one cam
    assert cams["https://camsecure.uk/HLS/ewt2.m3u8"].title == "Marsh"
    assert cams["https://camsecure.uk/HLS/ewt3.m3u8"].title == "Pine marten"
    # a video carded as /live/<id> and embedded on the hub is one cam: the embed's
    assert cams[f"{_YT}eeeeeeeeeee"].source_page_url == (
        "https://www.essexwt.org/wildlife/webcams"
    )


def test_wildlife_trusts_keeps_chrome_and_noise_out() -> None:
    cams = _discover()
    # a bare channel link alone on a sitemap page is chrome, not a cam
    assert "https://www.youtube.com/@OspreyProject/live" not in cams
    # a placeholder, a resolved embed's raw URL and an off-site sitemap's page are
    # not cams; nor is a playlist
    for noise in (
        "about:blank",
        "https://camstreamer.com/embed/abc",
        "https://camsecure.uk/httpswebcam/ewt/ewt1.html",
        "https://e.x/webcam",
        "https://www.essexwt.org.evil.example/webcam",
    ):
        assert noise not in cams
    assert not any("videoseries" in t for t in cams)
    for c in cams.values():
        assert c.source == "wildlife-trusts"
        assert c.category == "Animals"


def test_wildlife_trusts_stable_ids_are_unique_and_follow_the_cam() -> None:
    """The id belongs to the cam, not to its position.

    Every YouTube card shares the index page, and a page's embeds can be added, removed
    or reordered.
    """
    cams = list(WildlifeTrustsSource(_FakeFetch()).discover())
    assert max(Counter(stable_id(c) for c in cams).values()) == 1

    hub = "https://www.essexwt.org/wildlife/webcams"
    before = {c.target_url: stable_id(c) for c in cams if c.source_page_url == hub}
    reordered = dict(_PAGES)
    reordered[hub] = (
        '<iframe src="https://www.youtube.com/embed/zzzzzzzzzzz"></iframe>'
        + _PAGES[hub]
    )

    class _Fetch:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return reordered.get(url)

    after = {
        c.target_url: stable_id(c)
        for c in WildlifeTrustsSource(_Fetch()).discover()
        if c.source_page_url == hub
    }
    assert before
    assert all(after[t] == before[t] for t in before)


def test_wildlife_trusts_finds_a_sitemap_named_only_in_robots_txt() -> None:
    pages = {
        "https://wp.example/robots.txt": (
            "User-agent: *\nSitemap: https://wp.example/sitemap_index.xml\n"
            "Sitemap: https://other.example/sitemap.xml\n"
        ),
        "https://wp.example/sitemap_index.xml": (
            "<urlset><url><loc>https://wp.example/badger-cam/</loc></url></urlset>"
        ),
        "https://other.example/sitemap.xml": _urlset(
            "webcam", host="https://other.example"
        ),
    }

    class _Fetch:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return pages.get(url)

    src = WildlifeTrustsSource(_Fetch())
    found = src._cam_pages("https://wp.example")  # pyright: ignore[reportPrivateUsage]
    assert found == ["https://wp.example/badger-cam/"]


def test_wildlife_trusts_sitemap_filter_keeps_cam_pages_only() -> None:
    src = WildlifeTrustsSource(_FakeFetch())
    trust = "https://www.essexwt.org"
    found = src._cam_pages(trust)  # pyright: ignore[reportPrivateUsage]
    assert found == [
        f"{trust}/wildlife/webcams",
        f"{trust}/barn-owl-cam-update",
        f"{trust}/osprey-blogs",
        f"{trust}/rutlandospreys",
    ]


def test_wildlife_trusts_markup_scan_stays_linear_on_hostile_pages() -> None:
    """`re` holds the GIL, so a backtracking blow-up on one page stalls playback too.

    This source's own scans (cards, player tags) must stay linear on unclosed tags and
    link-less cards.
    """
    hostile = "<iframe " * 200_000 + "featured-cards-custom" * 50_000

    class _Fetch:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return hostile if url == _INDEX else None

    started = time.monotonic()
    _TAG.findall(hostile)
    list(WildlifeTrustsSource(_Fetch())._cards())  # pyright: ignore[reportPrivateUsage]
    assert time.monotonic() - started < 5
