from webcam_aggregator.sources.ozolio import OzolioSource

_SITEMAP = "https://www.ozolio.com/cameras-sitemap.xml"

# the source enumerates /explore/<CID> pages only; the homepage and any other
# sitemap entries are ignored
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://www.ozolio.com/</loc></url>
<url><loc>https://www.ozolio.com/explore/BFWA000002E5</loc></url>
<url><loc>https://www.ozolio.com/explore/YRCN0000001F</loc></url>
<url><loc>https://www.ozolio.com/explore/DEAD00000001</loc></url>
<url><loc>https://www.ozolio.com/explore/NOTITLE00002</loc></url>
<url><loc>https://www.ozolio.com/about-us/</loc></url>
</urlset>"""


def _page(title: str) -> str:
    return f"<html><head><title>{title} - Webcam - Ozolio</title></head></html>"


_PAGES: dict[str, str | None] = {
    _SITEMAP: _SITEMAP_XML,
    "https://www.ozolio.com/explore/BFWA000002E5": _page("Keauhou Bay, Hawaii"),
    "https://www.ozolio.com/explore/YRCN0000001F": _page("Main Roof - Maui Eldorado"),
    # fetch failed -> skipped
    "https://www.ozolio.com/explore/DEAD00000001": None,
    # fetched but no Ozolio <title> (an error/consent page) -> skipped
    "https://www.ozolio.com/explore/NOTITLE00002": "<html><body>oops</body></html>",
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_ozolio_discover_sitemap_titles_and_filtering():
    cands = list(OzolioSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    assert set(by_title) == {"Keauhou Bay, Hawaii", "Main Roof - Maui Eldorado"}

    keauhou = by_title["Keauhou Bay, Hawaii"]
    # the explore page itself is the target: the stream URL only exists inside a
    # per-session relay exchange, so there is nothing stable to point at instead
    assert keauhou.target_url == "https://www.ozolio.com/explore/BFWA000002E5"
    assert keauhou.source_page_url == keauhou.target_url

    for c in cands:
        assert c.source == "ozolio"
        assert c.category is None  # no category -> "Other"
        assert c.predisc_key is None  # no stable stream URL to merge on
        assert c.angle_key is None


def test_ozolio_empty_sitemap_yields_nothing():
    class _Empty:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(OzolioSource(_Empty()).discover()) == []
