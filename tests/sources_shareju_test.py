from __future__ import annotations

import json

from webcam_aggregator.categories import _MAP  # pyright: ignore[reportPrivateUsage]
from webcam_aggregator.fetch import FetcherProtocol
from webcam_aggregator.sources.shareju import (
    _TAG_CATEGORY,  # pyright: ignore[reportPrivateUsage]
    SharejuSource,
)


class _FakeFetcher:
    _pages: dict[str, str]

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, _timeout: float = 20.0, /) -> str | None:
        return self._pages.get(url)


def _item_list(items: list[dict[str, object]]) -> str:
    ld = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": n, "item": i}
            for n, i in enumerate(items, 1)
        ],
    }
    return (
        '<script type="application/ld+json">'
        + json.dumps(ld, ensure_ascii=False)
        + "</script>"
    )


def _cam(
    name: str,
    embed: str,
    *,
    url: str = "https://its-i.com/camera/camera8",
    tags: str = "",
    locality: str = "港区…",
    region: str = "東京都",
) -> dict[str, object]:
    return {
        "@type": "VideoObject",
        "name": f"【ライブカメラ映像再生】{name}【Share-Ju】公開カメラ",
        "description": f"説明文。このカメラに付されているタグ（{tags}）。",
        "embedUrl": embed,
        "url": url,
        "locationCreated": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": locality,
                "addressRegion": region,
            },
        },
    }


def _source(index_html: str) -> SharejuSource:
    f: FetcherProtocol = _FakeFetcher({"https://its-i.com/camera": index_html})
    return SharejuSource(fetch=f)


def test_youtube_embed_becomes_watch_candidate_with_category() -> None:
    html = _item_list(
        [
            _cam(
                "アクティ汐留",
                "https://www.youtube-nocookie.com/embed/I4vPwjdTNYQ?si=abc",
                tags="鉄道 新幹線 交通 東京都 港区 公開 ライブカメラ",
            )
        ]
    )
    (c,) = list(_source(html).discover())
    assert c.source == "shareju"
    assert c.target_url == "https://www.youtube.com/watch?v=I4vPwjdTNYQ"
    assert c.predisc_key == "yt:I4vPwjdTNYQ"
    # first mapped tag wins (site order is specific -> generic): 鉄道 beats 交通
    assert c.category == "Trains"
    # boilerplate stripped, locality ellipsis stripped, geo appended
    assert c.title == "アクティ汐留 — 港区, 東京都"
    assert c.source_page_url == "https://its-i.com/camera/camera8"


def test_non_youtube_iframe_kept_as_visible_gap() -> None:
    html = _item_list(
        [
            _cam(
                "一宮市 銀座通り",
                "https://livecam.icc-media.co.jp/2.html\n",
                url="https://its-i.com/camera/camera550",
                tags="街並み",
            )
        ]
    )
    (c,) = list(_source(html).discover())
    assert c.target_url == "https://livecam.icc-media.co.jp/2.html"
    assert c.predisc_key is None
    # town/scenic tags deliberately unmapped -> None (title fallback takes over)
    assert c.category is None


def test_duplicate_embeds_and_geo_already_in_name() -> None:
    html = _item_list(
        [
            _cam(
                "川越駅西口",
                "https://www.youtube.com/embed/aaaaaaaaaaa",
                tags="河川",
                locality="川越市…",
                region="埼玉県",
            ),
            _cam("重複", "https://www.youtube.com/embed/aaaaaaaaaaa?si=x"),
        ]
    )
    cands = list(_source(html).discover())
    assert len(cands) == 1
    assert cands[0].category == "Rivers Lakes"
    # 川越市 is not literally in the name, 埼玉県 is appended too
    assert cands[0].title == "川越駅西口 — 川越市, 埼玉県"


def test_empty_or_malformed_index_yields_nothing() -> None:
    assert list(_source("").discover()) == []
    assert (
        list(_source('<script type="application/ld+json">{oops</script>').discover())
        == []
    )
    assert (
        list(
            _source(
                '<script type="application/ld+json">{"@type": "BreadcrumbList"}</script>'
            ).discover()
        )
        == []
    )


def test_tag_map_targets_are_real_map_keys() -> None:
    # every value must be a categories._MAP key, or those cams silently land in
    # "Unmapped Category" at build
    assert set(_TAG_CATEGORY.values()) <= set(_MAP)
