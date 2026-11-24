from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

import googleapiclient.discovery
import pytest

import webcam_aggregator.app as _app
from webcam_aggregator.app import (
    NoExtractorError,
    _source_status_for,  # pyright: ignore[reportPrivateUsage]
    make_liveness_check,
    origin_of,
)
from webcam_aggregator.catalogue import Hist
from webcam_aggregator.config import Config
from webcam_aggregator.extractors.base import Resolved
from webcam_aggregator.fetch import (
    Fetcher,
    _ImpersonatingSession,  # pyright: ignore[reportPrivateUsage]
)
from webcam_aggregator.models import Candidate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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


def test_resolver_fetch_failures_carry_the_outcome_but_no_url() -> None:
    """The resolver error message carries the outcome but no URL.

    The message feeds the aggregated resolve-failure detail and the serve-time
    resolve-failed line: the outcome says why, and a URL would leak its query.
    """
    resolver_get = _app._resolver_get  # pyright: ignore[reportPrivateUsage]
    baltic_post = _app._baltic_post  # pyright: ignore[reportPrivateUsage]
    f = Fetcher(delay=0.0, retries=1)  # loopback: refused before any request
    with pytest.raises(ValueError, match="resolver fetch failed") as got:
        resolver_get(f)("http://127.0.0.1/cam?tok=SECRET")
    assert str(got.value) == "resolver fetch failed: blocked-ip"
    with pytest.raises(ValueError, match="resolver post failed") as posted:
        baltic_post(f)("http://127.0.0.1/ajax", {"a": "b"})
    assert str(posted.value) == "resolver post failed: blocked-ip"


def test_baltic_post_sends_xhr_and_site_origin_referer() -> None:
    _baltic_post = _app._baltic_post  # pyright: ignore[reportPrivateUsage]

    captured: dict[str, Any] = {}

    class _FakeFetcher:
        def post_with_outcome(
            self,
            _url: str,
            _data: dict[str, str],
            *,
            headers: dict[str, str] | None = None,
            timeout: float = 20.0,
        ) -> tuple[str, str]:
            del timeout  # unused; present to satisfy FetcherPostProtocol signature
            captured["headers"] = headers
            return "ok", "ok"

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


def _upstream(
    pages: dict[str, str | tuple[None, str]],
) -> Callable[[str], tuple[str | None, str]]:
    """Fake the probe fetch.

    A str is a 200 body, a tuple a failed fetch and its outcome, and an unlisted URL a
    404.
    """

    def fetch(url: str) -> tuple[str | None, str]:
        page = pages.get(url, (None, "http-404"))
        return (page, "ok") if isinstance(page, str) else page

    return fetch


def test_liveness_check_fetch_verifies_hls() -> None:
    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(url="https://cdn.x/p.m3u8", stream_type="hls", ttl_seconds=None)

    cand = _probe_candidate()
    live = "#EXTM3U\n#EXTINF:3.000,\nseg.ts\n"
    check = make_liveness_check(resolve, _upstream({"https://cdn.x/p.m3u8": live}))
    assert check(cand) is None
    assert make_liveness_check(resolve, _upstream({}))(cand) == "dead-manifest"
    dash = _upstream({"https://cdn.x/p.m3u8": "<?xml?><MPD/>"})
    assert make_liveness_check(resolve, dash)(cand) == "dead-manifest"


def test_liveness_rejects_an_empty_playlist() -> None:
    """An empty 200 playlist is dead, whatever its header says.

    A CDN handed an expired token answers 200 with a well-formed but empty playlist
    rather than a 404. Listing that cam ships a stream no player can start, so liveness
    must judge on content, not the #EXTM3U header.
    """
    url = "https://hd-auth.x/live.m3u8?a=x"

    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(url=url, stream_type="hls", ttl_seconds=60)

    stub = "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST\n"
    check = make_liveness_check(resolve, _upstream({url: stub}))
    assert check(_probe_candidate()) == "dead-manifest"


_MASTER_URL = "https://cdn.x/cam/master.m3u8"
_VARIANT_URL = "https://cdn.x/cam/media_0.m3u8"
_MASTER = (
    "#EXTM3U\n#EXT-X-VERSION:7\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=446743,CODECS="avc1.4d4028"\nmedia_0.m3u8\n'
)


def _master_resolve(_id: str, _url: str) -> Resolved:
    return Resolved(url=_MASTER_URL, stream_type="hls", ttl_seconds=60)


def test_liveness_accepts_a_master_playlist() -> None:
    """Requiring segments alone would drop every master playlist.

    Variants, not #EXTINF — which is what most CDN top-level URLs return.
    """
    variant = "#EXTM3U\n#EXTINF:2.0,\nseg.ts\n"
    fetch = _upstream({_MASTER_URL: _MASTER, _VARIANT_URL: variant})
    assert make_liveness_check(_master_resolve, fetch)(_probe_candidate()) is None


def test_liveness_rejects_a_master_whose_variant_is_dead() -> None:
    """A master playlist can answer 200 while the variant behind it is gone.

    A 15-byte `#EXT-X-ENDLIST` body, a header-only stub, a 404 chunklist, an origin 500
    — so the master alone says nothing about whether a player can start it.
    """
    dead: list[str | tuple[None, str]] = [
        "#EXT-X-ENDLIST\n",
        "#EXTM3U\n#EXT-X-VERSION:3\n",
        (None, "http-404"),
        (None, "http-410"),
        (None, "http-500"),
    ]
    for variant in dead:
        fetch = _upstream({_MASTER_URL: _MASTER, _VARIANT_URL: variant})
        check = make_liveness_check(_master_resolve, fetch)
        assert check(_probe_candidate()) == "dead-manifest", variant
    # a variant tag with no URI after it: nothing for a player to start on
    nameless = _upstream({_MASTER_URL: "#EXTM3U\n#EXT-X-STREAM-INF:BW=1\n"})
    check = make_liveness_check(_master_resolve, nameless)
    assert check(_probe_candidate()) == "dead-manifest"


def test_liveness_keeps_a_master_whose_variant_was_refused() -> None:
    """A refused or failed variant fetch is not a dead cam.

    MediaMTX behaves as if it keeps one HLS session per client IP, 401ing the earlier
    one when a build's concurrent probes open another; a shedding or flaky host fails
    for reasons of its own.
    """
    for outcome in ("http-401", "http-403", "http-503", "timeout", "conn-error"):
        fetch = _upstream({_MASTER_URL: _MASTER, _VARIANT_URL: (None, outcome)})
        check = make_liveness_check(_master_resolve, fetch)
        assert check(_probe_candidate()) is None, outcome


def test_liveness_check_distinguishes_missing_extractor() -> None:
    """A gap in our coverage must not read the same as a cam being off air."""

    def resolve(_id: str, url: str) -> Resolved:
        msg = f"no extractor for {url}"
        raise NoExtractorError(msg)

    check = make_liveness_check(resolve, _upstream({}))
    assert check(_probe_candidate("https://rtsp.me/abc")) == "no-extractor"


def test_liveness_check_reports_resolver_detail() -> None:
    """The detail is what separates "yt-dlp is broken" from "this cam is off air"."""

    def resolve(_id: str, _url: str) -> Resolved:
        msg = "yt-dlp failed: Sign in to confirm you are not a bot"
        raise ValueError(msg)

    reason = make_liveness_check(resolve, _upstream({}))(_probe_candidate())
    assert reason is not None
    assert reason.startswith("resolve-failed:")
    assert "Sign in to confirm" in reason


def test_liveness_check_detail_strips_url_query_strings() -> None:
    """An extractor error can embed its target URL, token-carrying query and all.

    The detail is aggregated at INFO, where the rule is hostnames/paths only.
    """

    def resolve(_id: str, _url: str) -> Resolved:
        msg = "no address/streamid in https://g0.ipcamlive.com/player/player.php?alias=TOKEN"
        raise ValueError(msg)

    reason = make_liveness_check(resolve, _upstream({}))(_probe_candidate())
    assert reason is not None
    assert "TOKEN" not in reason
    assert "g0.ipcamlive.com/player/player.php" in reason


def test_liveness_check_trusts_non_hls_resolve() -> None:
    def resolve(_id: str, _url: str) -> Resolved:
        return Resolved(url="https://cdn.x/v.mp4", stream_type="mp4", ttl_seconds=None)

    assert make_liveness_check(resolve, _upstream({}))(_probe_candidate()) is None


def test_build_app_starts_without_youtube_when_client_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed YouTube client init degrades gracefully.

    The scrapers still build, and startup does not crash.
    """

    def _boom(*_a: object, **_k: object) -> NoReturn:
        msg = "no creds"
        raise RuntimeError(msg)

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
    # Cold start: no rebuild has run, so nothing is unhealthy yet (the top-level
    # `ready` gates that window) and every expected source is listed at zero — never
    # an empty {}.
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


def test_only_fingerprint_gated_sources_get_the_impersonating_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wildlife-trusts' index 403s the requests client on its TLS fingerprint.

    The `impersonate=True` it is built with is the whole fix, so it must survive wiring
    changes — and must not leak onto sources that don't need it.
    """
    seen: dict[str, Any] = {}

    def recorder(name: str) -> type:
        class _Rec:
            def __init__(self, fetch: object) -> None:
                seen[name] = fetch

            def discover(self) -> Iterator[Candidate]:
                return iter(())

        _Rec.name = name  # pyright: ignore[reportAttributeAccessIssue]
        return _Rec

    monkeypatch.setattr(_app, "WildlifeTrustsSource", recorder("wildlife-trusts"))
    monkeypatch.setattr(_app, "ExploreOrgSource", recorder("explore"))
    cfg = Config(
        youtube_api_key="",
        public_base_url="http://localhost",
        catalogue_interval_hours=6,
        search_query="q",
        log_level="INFO",
        exclude_categories=frozenset(),
        proxy_youtube=False,
        max_parallel_sources=4,
    )
    _app.build_app(cfg)
    assert isinstance(seen["wildlife-trusts"]._session, _ImpersonatingSession)
    assert not isinstance(seen["explore"]._session, _ImpersonatingSession)


def test_health_sources_stay_lean() -> None:
    """Per-source payload is kept/discovered/crashed/status only.

    The diagnostic detail lives on the per-cycle log lines.
    """
    history = {"skyline": Hist(last_raw_kept=0, last_discovered=11, last_crashed=False)}
    st = _source_status_for(["skyline"], history)
    assert st["sources"]["skyline"] == {
        "kept": 0,
        "discovered": 11,
        "crashed": False,
        "status": "ok",
    }
    assert st["unhealthy"] == ["skyline"]
