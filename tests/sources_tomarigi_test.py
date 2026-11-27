from __future__ import annotations

import json
from typing import TYPE_CHECKING, override

from webcam_aggregator.categories import _MAP  # pyright: ignore[reportPrivateUsage]
from webcam_aggregator.dedup import dedupe
from webcam_aggregator.sources.tomarigi import (
    _CATEGORY_MAP,  # pyright: ignore[reportPrivateUsage]
    _UNCATEGORISED,  # pyright: ignore[reportPrivateUsage]
    TomarigiSource,
)

if TYPE_CHECKING:
    from webcam_aggregator.fetch import FetcherProtocol

_SITEMAP_URL = "https://tomarigi.me/sitemap.xml"
_SPOT = "https://tomarigi.me/spot/1b6ea960-f566-44f9-a63d-94de856b206d"
_SPOT2 = "https://tomarigi.me/spot/bd262da8-d5e7-48cd-84f4-e9a1b0e6147d"
_ASIA = "https://tomarigi.me/world/spots/asia"
_ASIA2 = "https://tomarigi.me/world/spots/asia/2"
_EUROPE = "https://tomarigi.me/world/spots/europe"


class _FakeFetcher:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0, /) -> str | None:
        return self._pages.get(url)


def _sitemap(*locs: str) -> str:
    urls = "".join(
        f"<url><loc>{u}</loc><changefreq>hourly</changefreq></url>" for u in locs
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset>{urls}</urlset>'


def _listing(*hrefs: str) -> str:
    """Build a region listing page: its cams plus any region/pagination links.

    The links are written as the site-relative hrefs the real pages use.
    """
    links = "".join(
        f'<li><a href="{h.removeprefix("https://tomarigi.me")}">x</a></li>'
        for h in hrefs
    )
    return f"<html><body><ul>{links}</ul></body></html>"


def _page(
    name: str, video_id: str, category: str = "海", *, extra_ld: bool = True
) -> str:
    ld = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": name,
        "description": (
            f"「{name}」のライブカメラ映像(提供: someone)。"
            f"{category}のライブカメラを地図から探せる「とまり木」で配信中。"
        ),
        "embedUrl": f"https://www.youtube.com/embed/{video_id}",
    }
    site = (
        '<script type="application/ld+json">'
        '{"@type":"WebSite","name":"とまり木"}</script>'
        if extra_ld
        else ""
    )
    return (
        "<html><body>"
        + site
        + '<script type="application/ld+json">'
        + json.dumps(ld, ensure_ascii=False)
        + "</script></body></html>"
    )


def _source(pages: dict[str, str]) -> TomarigiSource:
    f: FetcherProtocol = _FakeFetcher(pages)
    return TomarigiSource(fetch=f)


def test_spot_page_becomes_watch_candidate_with_mapped_category() -> None:
    src = _source(
        {
            _SITEMAP_URL: _sitemap("https://tomarigi.me", _ASIA),
            _ASIA: _listing("https://tomarigi.me/world/spots", _SPOT),
            _SPOT: _page("Hart Beach Scheveningen SurfCam", "Cbp3Wg44fPo", "海"),
        }
    )
    (c,) = list(src.discover())
    assert c.source == "tomarigi"
    assert c.target_url == "https://www.youtube.com/watch?v=Cbp3Wg44fPo"
    assert c.predisc_key == "yt:Cbp3Wg44fPo"
    assert c.category == "Beaches"
    assert c.title == "Hart Beach Scheveningen SurfCam"
    assert c.source_page_url == _SPOT


def test_only_region_listings_and_their_spots_are_fetched() -> None:
    # the sitemap also lists /rail, the /spots/<slug> and /world/country/<cc> subsets
    # and the zh-tw/th mirrors — fetching those costs a request each and adds no cam
    pages = {
        _SITEMAP_URL: _sitemap(
            "https://tomarigi.me",
            "https://tomarigi.me/world/spots",
            "https://tomarigi.me/world/country/jp",
            "https://tomarigi.me/spots/coast",
            "https://tomarigi.me/zh-tw/country/jp",
            "https://tomarigi.me/rail/routes",
            _ASIA,
        ),
        _ASIA: _listing(_SPOT),
        _SPOT: _page("cam", "Cbp3Wg44fPo"),
    }
    fetched: list[str] = []

    class _Recording(_FakeFetcher):
        @override
        def get(self, url: str, _timeout: float = 20.0, /) -> str | None:
            fetched.append(url)
            return super().get(url)

    f: FetcherProtocol = _Recording(pages)
    assert [c.source_page_url for c in TomarigiSource(fetch=f).discover()] == [_SPOT]
    assert sorted(fetched) == sorted([_SITEMAP_URL, _ASIA, _SPOT])


def test_listing_pages_the_sitemap_misses_are_followed_from_their_links() -> None:
    # a cam on a page the sitemap doesn't name yet (a new page of a growing region,
    # a new region) is still a cam; listings link their pages and the other regions
    spot3 = "https://tomarigi.me/spot/0e4bd8a9-1c3e-4e1d-9f4a-6b2f3c1d7a11"
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing("https://tomarigi.me/world/spots", _ASIA2, _EUROPE, _SPOT),
            _ASIA2: _listing(_ASIA, _SPOT2),
            _EUROPE: _listing(_ASIA, spot3),
            _SPOT: _page("one", "Cbp3Wg44fPo"),
            _SPOT2: _page("two", "aaaaaaaaaaa"),
            spot3: _page("three", "bbbbbbbbbbb"),
        }
    )
    assert sorted(c.source_page_url for c in src.discover()) == sorted(
        [_SPOT, _SPOT2, spot3]
    )


def test_rivers_and_roads_slug_stays_uncategorised_for_the_title_fallback() -> None:
    # 河川・道路 is a near-even mix of river gauges and road junctions (and a long
    # tail of neither), so it must NOT be mapped to one category
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing(_SPOT),
            _SPOT: _page("【神田川】柏橋映像監視局", "Cbp3Wg44fPo", "河川・道路"),
        }
    )
    (c,) = list(src.discover())
    assert c.category is None


def test_unknown_slug_passes_through_raw_to_surface_as_unmapped() -> None:
    # a tenth site category must reach map_category's "Unmapped Category" + WARNING,
    # not hide in "Other"
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing(_SPOT),
            _SPOT: _page("cam", "Cbp3Wg44fPo", "宇宙"),
        }
    )
    (c,) = list(src.discover())
    assert c.category == "宇宙"


def test_duplicate_video_ids_merge_into_one_cam() -> None:
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing(_SPOT, _SPOT2),
            _SPOT: _page("cam", "Cbp3Wg44fPo"),
            _SPOT2: _page("same cam, second spot", "Cbp3Wg44fPo"),
        }
    )
    (cam,) = dedupe(list(src.discover()))
    assert cam.title == "same cam, second spot"  # the merge keeps the longest


def test_pages_without_a_video_object_are_dropped() -> None:
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing(_SPOT, _SPOT2),
            _SPOT: "<html><body>withdrawn</body></html>",
            _SPOT2: '<script type="application/ld+json">{oops</script>',
        }
    )
    assert list(src.discover()) == []


def test_name_holding_an_escaped_quote_is_still_read() -> None:
    # the site stores YouTube's HTML-escaped titles inside the JSON; decoding the
    # block before parsing it turns `&quot;` into a bare quote that ends the string
    src = _source(
        {
            _SITEMAP_URL: _sitemap(_ASIA),
            _ASIA: _listing(_SPOT),
            _SPOT: _page("Seoul &quot;Gyeongbokgung&quot; Live", "Cbp3Wg44fPo"),
        }
    )
    (c,) = list(src.discover())
    assert c.predisc_key == "yt:Cbp3Wg44fPo"


def test_non_youtube_embed_url_is_dropped() -> None:
    # every cam on the site is a YouTube live; anything else is a page we misread
    page = _page("cam", "Cbp3Wg44fPo").replace(
        "https://www.youtube.com/embed/Cbp3Wg44fPo", "https://example.com/cam"
    )
    src = _source({_SITEMAP_URL: _sitemap(_ASIA), _ASIA: _listing(_SPOT), _SPOT: page})
    assert list(src.discover()) == []


def test_empty_sitemap_yields_nothing() -> None:
    assert list(_source({}).discover()) == []
    assert list(_source({_SITEMAP_URL: "<urlset></urlset>"}).discover()) == []


def test_category_map_targets_are_real_map_keys() -> None:
    # every value must be a categories._MAP key, or those cams silently land in
    # "Unmapped Category" at build
    assert set(_CATEGORY_MAP.values()) <= set(_MAP)
    # the deliberately-uncategorised slugs must not also be mapped
    assert not (_UNCATEGORISED & set(_CATEGORY_MAP))
