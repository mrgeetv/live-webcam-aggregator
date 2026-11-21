from webcam_aggregator.sources.wildlife_trusts import WildlifeTrustsSource

_GTM = '<iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe>'
_CHROME = '<a href="https://www.youtube.com/channel/UCtrust/live">Our channel</a>'


def _card(href: str, title: str) -> str:
    return (
        '<div class="paragraph paragraph--type--featured-cards-custom card">'
        f'<a href="{href}"><picture></picture><span class="card__meta">X Wildlife'
        f' Trust</span><h3 class="card__title field">{title}</h3>Watch live</a></div>'
    )


_PAGES = {
    "https://www.wildlifetrusts.org/webcams": "".join(
        [
            _card("https://www.essexwt.org/owl-cam", "Essex&#039;s barn owls"),
            _card("https://www.youtube.com/watch?v=aaaaaaaaaaa", "Rutland ospreys"),
            _card("https://www.youtube.com/@Puffins/streams", "Skomer puffins"),
            '<a href="https://www.facebook.com/sharer?u=x">Share</a>',
        ]
    ),
    "https://www.wildlifetrusts.org/wildlife-trusts": (
        '<a href="http://www.essexwt.org/" class="button" itemprop="url">Visit</a>'
        '<a href="https://www.essexwt.org/donate">Donate</a>'
    ),
    "https://www.essexwt.org/sitemap.xml": (
        "<sitemapindex><sitemap><loc>https://www.essexwt.org/sitemap.xml?page=1</loc>"
        "</sitemap><sitemap><loc>https://elsewhere.example/sitemap.xml</loc>"
        "</sitemap></sitemapindex>"
    ),
    "https://www.essexwt.org/sitemap.xml?page=1": "<urlset>"
    + "".join(
        f"<url><loc>https://www.essexwt.org/{p}</loc></url>"
        for p in (
            "wildlife/webcams",
            "barn-owl-cam-update",
            "osprey-blogs",
            "rutlandospreys",
            "donate",
            "blog/owl-cam-is-back",
            "how-to-trail-camera",
        )
    )
    + "</urlset>",
    # A consent-gated YouTube with its own title, a camstreamer behind the GTM
    # iframe, a camsecure player, a tokened streamdays script, and the channel
    # link every page's chrome carries.
    "https://www.essexwt.org/wildlife/webcams": _GTM
    + _CHROME
    + (
        '<iframe src="about:blank" data-src-cmplz="https://www.youtube.com/embed/'
        'bbbbbbbbbbb" title="Youtube video &quot;Barn owl nest box&quot;"></iframe>'
        '<iframe src="https://camstreamer.com/embed/abc"></iframe>'
        '<iframe src="https://camsecure.uk/httpswebcam/ewt/ewt1.html"></iframe>'
        '<script src="https://live.streamdays.com/6o8zzn7m"></script>'
    ),
    "https://camstreamer.com/embed/abc": (
        '<iframe src="https://www.youtube.com/embed/ccccccccccc?autoplay=1">'
    ),
    "https://camsecure.uk/httpswebcam/ewt/ewt1.html": (
        '<video><source src="https://camsecure.uk/HLS/ewt.m3u8"></video>'
    ),
    # Two untitled cams: one titled by YouTube's oEmbed, the other numbered; the
    # channel link is dropped beside them.
    "https://www.essexwt.org/rutlandospreys": (
        "<title>Rutland ospreys | Essex Wildlife Trust</title>"
        + _CHROME
        + '<a href="https://www.youtube.com/watch?v=aaaaaaaaaaa">Bay</a>'
        + '<a href="https://www.youtube.com/watch?v=ddddddddddd">Lagoon</a>'
    ),
    # An article whose only YouTube is the owl channel's link: named by the owl-cam
    # page that embeds it instead, though this page sorts first.
    "https://www.essexwt.org/barn-owl-cam-update": (
        "<title>Barn owl update | Essex Wildlife Trust</title>"
        '<a href="https://www.youtube.com/channel/UCowls/live">Watch</a>'
    ),
    # A bare channel link alone on a sitemap page is not a cam.
    "https://www.essexwt.org/osprey-blogs": (
        '<a href="https://www.youtube.com/@OspreyProject/live">Our channel</a>'
    ),
    # Only the channel's current live, behind a consent placeholder: that IS the cam.
    "https://www.essexwt.org/owl-cam": _GTM
    + (
        '<iframe src="about:blank" data-src-cmplz="//www.youtube.com/embed/'
        'live_stream?channel=UCowls"></iframe>'
    ),
    "https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fwww.youtube.com"
    "%2Fwatch%3Fv%3Dddddddddddd": '{"title": "Lagoon 4 nest", "type": "video"}',
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_wildlife_trusts_reads_index_cards_and_trust_sitemaps():
    cands = list(WildlifeTrustsSource(_FakeFetch()).discover())
    by_target = {c.target_url: c for c in cands}

    assert set(by_target) == {
        # index cards: a YouTube card is the cam, a channel tab becomes its live
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/@Puffins/live",
        # a consent-gated channel live embed
        "https://www.youtube.com/channel/UCowls/live",
        # sitemap hub page: consent YouTube, camstreamer -> its YouTube, camsecure
        # -> its HLS, streamdays kept as a visible no-extractor drop
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "https://www.youtube.com/watch?v=ccccccccccc",
        "https://camsecure.uk/HLS/ewt.m3u8",
        "https://live.streamdays.com/6o8zzn7m",
        # sitemap page the index never links
        "https://www.youtube.com/watch?v=ddddddddddd",
    }
    # The trust's channel link in the page chrome is not a cam beside real embeds,
    # nor is a bare channel link that is all a sitemap page has.
    assert "https://www.youtube.com/channel/UCtrust/live" not in by_target
    assert "https://www.youtube.com/@OspreyProject/live" not in by_target

    # card title, unescaped; the page that embeds a target beats one that links it
    owls = by_target["https://www.youtube.com/channel/UCowls/live"]
    assert owls.title == "Essex's barn owls"
    assert owls.source_page_url == "https://www.essexwt.org/owl-cam"
    assert by_target["https://www.youtube.com/@Puffins/live"].title == (
        "Skomer puffins"
    )
    # an embed's own title beats the page's (a plugin's quoting unwrapped); an
    # untitled YouTube sibling takes the video's title, failing that a number
    assert by_target["https://www.youtube.com/watch?v=bbbbbbbbbbb"].title == (
        "Barn owl nest box"
    )
    assert by_target["https://www.youtube.com/watch?v=ddddddddddd"].title == (
        "Lagoon 4 nest"
    )
    # A cam reached from a page and a card is one candidate: a page that only links
    # it loses to the card's curated name.
    rutland = by_target["https://www.youtube.com/watch?v=aaaaaaaaaaa"]
    assert rutland.title == "Rutland ospreys"
    assert rutland.source_page_url == "https://www.wildlifetrusts.org/webcams"
    assert by_target["https://www.youtube.com/watch?v=ccccccccccc"].predisc_key == (
        "yt:ccccccccccc"
    )

    for c in cands:
        assert c.source == "wildlife-trusts"
        assert c.category == "Animals"


def test_wildlife_trusts_finds_a_sitemap_named_only_in_robots_txt():
    pages = {
        "https://wp.example/robots.txt": (
            "User-agent: *\nSitemap: https://wp.example/sitemap_index.xml\n"
        ),
        "https://wp.example/sitemap_index.xml": (
            "<urlset><url><loc>https://wp.example/badger-cam/</loc></url></urlset>"
        ),
    }

    class _Fetch:
        def get(self, url: str, _timeout: float = 20.0) -> str | None:
            return pages.get(url)

    src = WildlifeTrustsSource(_Fetch())
    found = src._cam_pages("https://wp.example")  # pyright: ignore[reportPrivateUsage]
    assert found == ["https://wp.example/badger-cam/"]


def test_wildlife_trusts_sitemap_filter_skips_articles_and_other_hosts():
    src = WildlifeTrustsSource(_FakeFetch())
    trust = "https://www.essexwt.org"
    found = src._cam_pages(trust)  # pyright: ignore[reportPrivateUsage]
    assert found == [
        "https://www.essexwt.org/wildlife/webcams",
        "https://www.essexwt.org/barn-owl-cam-update",
        "https://www.essexwt.org/osprey-blogs",
        "https://www.essexwt.org/rutlandospreys",
    ]
