from __future__ import annotations

from typing import Any

import pytest

from webcam_aggregator.app import origin_of
from webcam_aggregator.models import Candidate

# ---------------------------------------------------------------------------
# origin_of helper — must return the scheme+host with trailing slash
# ---------------------------------------------------------------------------


def test_origin_of_typical_url() -> None:
    assert (
        origin_of("https://balticlivecam.com/wp-admin/admin-ajax.php")
        == "https://balticlivecam.com/"
    )


def test_origin_of_with_path_and_query() -> None:
    assert origin_of("http://example.com/some/path?foo=bar") == "http://example.com/"


def test_origin_of_preserves_scheme() -> None:
    assert origin_of("http://insecure.example.com/x").startswith("http://")
    assert origin_of("https://secure.example.com/x").startswith("https://")


def test_origin_of_no_trailing_slash_duplication() -> None:
    result = origin_of("https://balticlivecam.com/wp-admin/admin-ajax.php")
    assert result.endswith("/")
    assert not result.endswith("//")


# ---------------------------------------------------------------------------
# _baltic_post — Referer must be the SITE ORIGIN, not the ajax URL
# ---------------------------------------------------------------------------


def test_baltic_post_sends_xhr_and_site_origin_referer() -> None:
    import webcam_aggregator.app as _app

    _baltic_post = _app._baltic_post  # pyright: ignore[reportPrivateUsage]

    captured: dict[str, Any] = {}

    class _FakeFetcher:
        def post(
            self,
            _url: str,
            _data: dict[str, str],
            *,
            headers: dict[str, str] | None = None,
            timeout: float = 20.0,
        ) -> str:
            del timeout  # unused; present to satisfy FetcherPostProtocol signature
            captured["headers"] = headers
            return "ok"

    out = _baltic_post(_FakeFetcher())(
        "https://balticlivecam.com/wp-admin/admin-ajax.php", {"action": "auth_token"}
    )
    assert out == "ok"
    headers: dict[str, str] = captured["headers"]
    assert (
        headers["Referer"] == "https://balticlivecam.com/"
    )  # site origin, NOT the ajax URL
    assert headers["X-Requested-With"] == "XMLHttpRequest"


def _probe_candidate(target: str = "https://x/p.m3u8") -> Candidate:
    return Candidate(
        title="x",
        angle_key=None,
        category=None,
        source="s",
        source_page_url="https://x/p",
        target_url=target,
        predisc_key=None,
    )


def test_liveness_check_fetch_verifies_hls() -> None:
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(url="https://cdn.x/p.m3u8", stream_type="hls", ttl_seconds=None)

    cand = _probe_candidate()
    live = "#EXTM3U\n#EXTINF:3.000,\nseg.ts\n"
    assert make_liveness_check(resolve, lambda u: live)(cand) is None
    assert make_liveness_check(resolve, lambda u: None)(cand) == "dead-manifest"
    assert (
        make_liveness_check(resolve, lambda u: "<?xml?><MPD/>")(cand) == "dead-manifest"
    )  # DASH


def test_liveness_rejects_an_empty_playlist() -> None:
    """A CDN handed an expired token answers 200 with a well-formed but empty
    playlist rather than a 404 — listing that cam ships a stream no player can
    start, so liveness must judge on content, not the #EXTM3U header."""
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(
            url="https://hd-auth.x/live.m3u8?a=x", stream_type="hls", ttl_seconds=60
        )

    stub = "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST\n"
    check = make_liveness_check(resolve, lambda _u: stub)
    assert check(_probe_candidate()) == "dead-manifest"


def test_liveness_accepts_a_master_playlist() -> None:
    """Requiring segments alone would drop every master playlist — variants, not
    #EXTINF — which is what most CDN top-level URLs return."""
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(
            url="https://cdn.x/master.m3u8", stream_type="hls", ttl_seconds=60
        )

    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow.m3u8\n"
    assert make_liveness_check(resolve, lambda _u: master)(_probe_candidate()) is None


def test_liveness_check_distinguishes_missing_extractor() -> None:
    """A gap in our coverage must not read the same as a cam being off air."""
    from webcam_aggregator.app import NoExtractorError, make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, url: str) -> Resolved:
        raise NoExtractorError(f"no extractor for {url}")

    check = make_liveness_check(resolve, lambda u: None)
    assert check(_probe_candidate("https://rtsp.me/abc")) == "no-extractor"


def test_liveness_check_reports_resolver_detail() -> None:
    """The detail is what separates "yt-dlp is broken" from "this cam is off air"."""
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        raise ValueError("yt-dlp failed: Sign in to confirm you are not a bot")

    reason = make_liveness_check(resolve, lambda u: None)(_probe_candidate())
    assert reason is not None
    assert reason.startswith("resolve-failed:")
    assert "Sign in to confirm" in reason


def test_liveness_check_detail_strips_url_query_strings() -> None:
    """An extractor error can embed its target URL, token-carrying query and all; the
    detail is aggregated at INFO, where the rule is hostnames/paths only."""
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        raise ValueError(
            "no address/streamid in https://g0.ipcamlive.com/player/player.php?alias=TOKEN"
        )

    reason = make_liveness_check(resolve, lambda u: None)(_probe_candidate())
    assert reason is not None
    assert "TOKEN" not in reason
    assert "g0.ipcamlive.com/player/player.php" in reason


def test_liveness_check_trusts_non_hls_resolve() -> None:
    from webcam_aggregator.app import make_liveness_check
    from webcam_aggregator.extractors.base import Resolved

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(url="https://cdn.x/v.mp4", stream_type="mp4", ttl_seconds=None)

    assert make_liveness_check(resolve, lambda u: None)(_probe_candidate()) is None


def test_build_app_starts_without_youtube_when_client_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed YouTube client init must degrade gracefully (scrapers still build),
    not crash startup."""
    import googleapiclient.discovery

    import webcam_aggregator.app as _app
    from webcam_aggregator.config import Config

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no creds")

    monkeypatch.setattr(googleapiclient.discovery, "build", _boom)

    cfg = Config(
        youtube_api_key="dummy",
        public_base_url="http://localhost",
        catalogue_interval_hours=6,
        search_query="q",
        log_level="INFO",
        exclude_categories=frozenset(),
        proxy_youtube=False,
        max_parallel_sources=4,
    )
    store, _cache, rebuild, source_status = _app.build_app(cfg)
    assert store is not None
    assert callable(rebuild)
    assert callable(source_status)
    # Cold start: no rebuild has run, so nothing is unhealthy yet (the top-level `ready`
    # gates that window) and every expected source is listed at zero — never an empty {}.
    st = source_status()
    assert st["unhealthy"] == []
    assert st["sources"], "expected the source roster to be listed at cold start"
    # The /health handler indexes this unguarded — losing it from the real
    # source_status() would be a 500 on /health with an otherwise green suite.
    assert st["last_build_seconds"] is None
    assert all(
        s
        == {
            "kept": 0,
            "discovered": 0,
            "crashed": False,
            "status": "unknown",
        }
        for s in st["sources"].values()
    )


def test_health_sources_stay_lean() -> None:
    """Per-source payload is kept/discovered/crashed/status only; the diagnostic
    detail lives on the per-cycle log lines."""
    from webcam_aggregator.app import (
        _source_status_for,  # pyright: ignore[reportPrivateUsage]
    )
    from webcam_aggregator.catalogue import Hist

    history = {"skyline": Hist(last_raw_kept=0, last_discovered=11, last_crashed=False)}
    st = _source_status_for(["skyline"], history)
    assert st["sources"]["skyline"] == {
        "kept": 0,
        "discovered": 11,
        "crashed": False,
        "status": "ok",
    }
    assert st["unhealthy"] == ["skyline"]
