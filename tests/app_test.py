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
    assert make_liveness_check(resolve, lambda u: "#EXTM3U\nseg.ts\n")(cand) is None
    assert make_liveness_check(resolve, lambda u: None)(cand) == "dead-manifest"
    assert (
        make_liveness_check(resolve, lambda u: "<?xml?><MPD/>")(cand) == "dead-manifest"
    )  # DASH


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
    assert all(
        s
        == {
            "kept": 0,
            "discovered": 0,
            "crashed": False,
            "status": "unknown",
            "fetches": {},
            "drop_reasons": {},
            "no_extractor_hosts": {},
        }
        for s in st["sources"].values()
    )


def test_health_sources_carry_the_diagnosis() -> None:
    """The uptime check's response body should be enough to diagnose a dead source
    without going to the logs."""
    from webcam_aggregator.app import (
        _source_status_for,  # pyright: ignore[reportPrivateUsage]
    )
    from webcam_aggregator.catalogue import Hist

    history = {
        "skyline": Hist(
            last_raw_kept=0,
            last_discovered=0,
            last_crashed=False,
            last_fetches={"www.skylinewebcams.com": {"http-403": 11}},
            drop_reasons={"dead-manifest": 12},
            no_extractor_hosts={"rtsp.me": 3},
        )
    }
    st = _source_status_for(["skyline"], history)
    src = st["sources"]["skyline"]
    assert src["fetches"] == {"www.skylinewebcams.com": {"http-403": 11}}
    assert src["drop_reasons"] == {"dead-manifest": 12}
    assert src["no_extractor_hosts"] == {"rtsp.me": 3}
    assert st["unhealthy"] == ["skyline"]


def test_health_detail_is_a_copy_not_a_live_reference() -> None:
    """A /health request must not hand out the rebuild thread's mutable state."""
    from webcam_aggregator.app import (
        _source_status_for,  # pyright: ignore[reportPrivateUsage]
    )
    from webcam_aggregator.catalogue import Hist

    h = Hist(last_raw_kept=1, drop_reasons={"dead-manifest": 1})
    st = _source_status_for(["skyline"], {"skyline": h})
    h.drop_reasons["dead-manifest"] = 999
    assert st["sources"]["skyline"]["drop_reasons"] == {"dead-manifest": 1}
