from webcam_aggregator.sources.camsecure import CamSecureSource

_BASE = "https://www.camsecure.co.uk"
_SITEMAP = f"{_BASE}/sitemap.xml"

# the source enumerates cam pages from the sitemap (far more than the demo index)
_SITEMAP_XML = """<?xml version="1.0"?><urlset>
<url><loc>https://www.camsecure.co.uk/Camsecure3/Brixham_Harbour.html</loc></url>
<url><loc>https://www.camsecure.co.uk/CoastwatchRedcarWebcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/oban%20bay%20webcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/oban bay webcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/Falmouth_Yacht_Webcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/dead_webcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/rtsp_demo_webcam.html</loc></url>
<url><loc>https://www.camsecure.co.uk/webcam_live_clock_overlay.html</loc></url>
<url><loc>https://www.camsecure.co.uk/WebcamHosting.html</loc></url>
<url><loc>https://www.othercam.com/some_webcam.html</loc></url>
</urlset>"""


def _cam(player: str, title: str | None) -> str:
    head = f"<title>{title}</title>" if title is not None else ""
    return f'{head}<iframe src="{player}"></iframe>'


_PAGES = {
    _SITEMAP: _SITEMAP_XML,
    f"{_BASE}/Camsecure3/Brixham_Harbour.html": _cam(
        "https://camsecure.co/httpswebcam/camsecure/brixham1.html",
        "Brixham Harbour Live Streaming Webcam",
    ),
    "https://camsecure.co/httpswebcam/camsecure/brixham1.html": (
        '<video><source src="/HLS/brixham.m3u8" type="application/x-mpegURL"></video>'
    ),
    # title LEADS with boilerplate -> fall back to the "from <place>" tail
    f"{_BASE}/CoastwatchRedcarWebcam.html": _cam(
        "https://camsecure.co/httpswebcam/camsecure/redcar1.html",
        "Live Coastal Shipping Webcam from Coastwatch Redcar",
    ),
    "https://camsecure.co/httpswebcam/camsecure/redcar1.html": (
        '<source src="//camsecure.uk/HLS/redcar1.m3u8">'
    ),
    f"{_BASE}/oban%20bay%20webcam.html": _cam(
        "https://camsecure.uk/httpswebcam/camsecure/greystones.html",
        "Oban Bay Live Webcam",
    ),
    "https://camsecure.uk/httpswebcam/camsecure/greystones.html": (
        '<source src="//camsecure.uk/HLS/greystonesoban.m3u8">'
    ),
    # no <title> at all -> URL-filename fallback ("Falmouth Yacht")
    f"{_BASE}/Falmouth_Yacht_Webcam.html": _cam(
        "https://camsecure.co/httpswebcam/camsecure/falmouth.html", None
    ),
    "https://camsecure.co/httpswebcam/camsecure/falmouth.html": (
        '<source src="/HLS/la-mouette.m3u8">'
    ),
    # player has no stream -> dropped
    f"{_BASE}/dead_webcam.html": _cam(
        "https://camsecure.co/httpswebcam/camsecure/dead.html", "Dead Cam Webcam"
    ),
    "https://camsecure.co/httpswebcam/camsecure/dead.html": "<p>offline</p>",
    # embeds an rtsp.me stream -> skipped (stub manifest passes liveness but 404s)
    f"{_BASE}/rtsp_demo_webcam.html": _cam(
        "https://camsecure.co/httpswebcam/camsecure/rtspdemo.html", "Rtsp Demo Webcam"
    ),
    "https://camsecure.co/httpswebcam/camsecure/rtspdemo.html": (
        '<source src="//lon.rtsp.me/abc/123/hls/x.m3u8">'
    ),
}


class _FakeFetch:
    def get(self, url: str, _timeout: float = 20.0) -> str | None:
        return _PAGES.get(url)


def test_camsecure_discover_sitemap_titles_dedup_and_filtering():
    cands = list(CamSecureSource(_FakeFetch()).discover())
    by_title = {c.title: c for c in cands}

    # the live cams only: dead (no stream) + rtsp.me (skipped) drop; WebcamHosting +
    # webcam_live_clock_overlay are denylisted; othercam.com isn't camsecure.co.uk
    assert set(by_title) == {
        "Brixham Harbour",
        "Coastwatch Redcar",  # "from <place>" fallback
        "Oban Bay",
        "Falmouth Yacht",  # URL-filename fallback (no <title>)
    }

    # direct HLS, protocol-relative source resolved against the player host
    assert (
        by_title["Brixham Harbour"].target_url
        == "https://camsecure.co/HLS/brixham.m3u8"
    )
    assert (
        by_title["Oban Bay"].target_url
        == "https://camsecure.uk/HLS/greystonesoban.m3u8"
    )

    # the literal-space + %20 Oban links collapse to a single cam
    assert sum(1 for c in cands if c.title == "Oban Bay") == 1
    # no rtsp.me stream leaks through
    assert not any("rtsp.me" in c.target_url for c in cands)

    for c in cands:
        assert c.source == "camsecure"
        assert c.category is None  # no category -> "Other"
        assert (c.predisc_key or "").startswith("hls:")
