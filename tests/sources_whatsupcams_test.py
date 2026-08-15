import json

from webcam_aggregator.sources.whatsupcams import WhatsupcamsSource

_SITE = "https://www.whatsupcams.com"

# the town-sitemap must be ignored; only webcams-sitemap*.xml are cam pages
_SITEMAP_INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex>
  <sitemap><loc>{_SITE}/en/post-sitemap.xml</loc></sitemap>
  <sitemap><loc>{_SITE}/en/webcams-sitemap.xml</loc></sitemap>
  <sitemap><loc>{_SITE}/en/webcams-sitemap2.xml</loc></sitemap>
  <sitemap><loc>{_SITE}/en/town-sitemap.xml</loc></sitemap>
</sitemapindex>
"""

_PODSTRANA = (
    f"{_SITE}/en/webcams/croatia/split-dalmatia/podstrana/podstrana-beach-webcam/"
)
_ZOO = f"{_SITE}/en/webcams/croatia/zagreb-county/zagreb-center/gyps-webcam-zagreb-zoo/"
_NOCAM = f"{_SITE}/en/webcams/croatia/istria/pula/retired-cam-page/"
_DEADAPI = f"{_SITE}/en/webcams/slovenia/goriska/nova-gorica/bevk-square-nova-gorica/"

# the bare /en/webcams/ archive entry must be skipped
_SITEMAP1 = f"""<urlset>
  <url><loc>{_SITE}/en/webcams/</loc></url>
  <url><loc>{_PODSTRANA}</loc></url>
  <url><loc>{_NOCAM}</loc></url>
</urlset>
"""
# a cross-sitemap repeat of podstrana must still yield one candidate
_SITEMAP2 = f"""<urlset>
  <url><loc>{_ZOO}</loc></url>
  <url><loc>{_DEADAPI}</loc></url>
  <url><loc>{_PODSTRANA}</loc></url>
</urlset>
"""

# trimmed from the live page: the wgt embedUrl is the page's own cam id; the
# snapshot thumbnails include the "nearby cams" strip's ids and must not be used
_PODSTRANA_HTML = """
<meta property="og:title" content="Podstrana Beach Webcam &#8211; Live View from Hotel Split" />
<script>{"embedUrl":"https://services.whatsupcams.com/wgt/hr_podstrana01/",
"thumbnailUrl":"https://cdn.whatsupcams.com/snapshot/hr_podstrana01.jpg"}</script>
<img src="https://cdn.whatsupcams.com/snapshot/hr_bol02.jpg">
"""
_ZOO_HTML = """
<meta property="og:title" content="Gyps Webcam – Zagreb Zoo Live" />
<script>{"embedUrl":"https://services.whatsupcams.com/wgt/hr_zgzoo05/"}</script>
"""
_NOCAM_HTML = "<html><body>No player on this page.</body></html>"
_DEADAPI_HTML = """
<script>{"embedUrl":"https://services.whatsupcams.com/wgt/si_ngbevkovtrg/"}</script>
"""

_API = "https://www.whatsupcams.com/cdn-api/streams/"
# the cdn-0NN host varies per cam — the API is the only id -> URL mapping
_API_JSON = {
    "hr_podstrana01": {
        "name": "hr_podstrana01",
        "hls": {"url": "https://cdn-008.whatsupcams.com/hls/hr_podstrana01.m3u8"},
    },
    "hr_zgzoo05": {
        "name": "hr_zgzoo05",
        "hls": {"url": "https://cdn-007.whatsupcams.com/hls/hr_zgzoo05.m3u8"},
    },
}

_PAGES = {
    "https://www.whatsupcams.com/en/sitemap_index.xml": _SITEMAP_INDEX,
    "https://www.whatsupcams.com/en/webcams-sitemap.xml": _SITEMAP1,
    "https://www.whatsupcams.com/en/webcams-sitemap2.xml": _SITEMAP2,
    _PODSTRANA: _PODSTRANA_HTML,
    _ZOO: _ZOO_HTML,
    _NOCAM: _NOCAM_HTML,
    _DEADAPI: _DEADAPI_HTML,
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        if url.startswith(_API):
            info = _API_JSON.get(url.removeprefix(_API))
            return json.dumps(info) if info else None
        return _PAGES.get(url)


def test_whatsupcams_sitemap_page_api_discovery():
    cands = list(WhatsupcamsSource(_FakeFetch()).discover())
    by_url = {c.target_url: c for c in cands}

    # podstrana + zoo survive; the no-player page and the failed-API id drop,
    # and the cross-sitemap repeat of podstrana collapses to one
    assert set(by_url) == {
        "https://cdn-008.whatsupcams.com/hls/hr_podstrana01.m3u8",
        "https://cdn-007.whatsupcams.com/hls/hr_zgzoo05.m3u8",
    }

    # entity-decoded title, with the location parts it doesn't already name
    # appended town-first (the title names Podstrana itself)
    pod = by_url["https://cdn-008.whatsupcams.com/hls/hr_podstrana01.m3u8"]
    assert pod.title == (
        "Podstrana Beach Webcam – Live View from Hotel Split"
        " — Split Dalmatia, Croatia"
    )
    assert pod.source_page_url == _PODSTRANA
    zoo = by_url["https://cdn-007.whatsupcams.com/hls/hr_zgzoo05.m3u8"]
    assert (
        zoo.title
        == "Gyps Webcam – Zagreb Zoo Live — Zagreb Center, Zagreb County, Croatia"
    )

    for c in cands:
        assert c.source == "whatsupcams"
        assert c.category is None  # no category in the page -> "Other"
        assert (c.predisc_key or "").startswith("hls:")  # direct HLS, mergeable


def test_whatsupcams_survives_all_fetches_failing():
    class _Dead:
        def get(self, _url: str, _timeout: float = 20.0) -> str | None:
            return None

    assert list(WhatsupcamsSource(_Dead()).discover()) == []
