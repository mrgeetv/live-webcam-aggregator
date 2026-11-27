import threading
from typing import TYPE_CHECKING

import pytest

from webcam_aggregator.extractors.beachcam import BeachcamResolver

if TYPE_CHECKING:
    from collections.abc import Callable

_BARE = "https://video-auth1.iol.pt/auth-beachcam/carcavelos2/playlist.m3u8"
_MATRIX = "https://services.iol.pt/matrix?userId="
# same base64 shape as the real IOL auth token, deliberately not a real one; it
# carries the base64 '+' and '/', which a bare query would corrupt
_TOK = "aGVsbG8+d29ybGQ/dGVzdC1vbmx5LXRva2Vu=="
_TOK_QUOTED = "aGVsbG8%2Bd29ybGQ%2FdGVzdC1vbmx5LXRva2Vu=="


class _Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def _fetch(bodies: list[str | Exception], calls: list[str]) -> Callable[[str], str]:
    """Answer each fetch with the next of `bodies`, the last one from then on."""

    def fetch(url: str) -> str:
        calls.append(url)
        body = bodies.pop(0) if len(bodies) > 1 else bodies[0]
        if isinstance(body, Exception):
            raise body
        return body

    return fetch


def test_beachcam_appends_a_url_encoded_token_to_the_bare_target() -> None:
    calls: list[str] = []
    out = BeachcamResolver(_fetch([_TOK], calls), clock=_Clock()).resolve(_BARE)

    assert calls == [_MATRIX]
    assert out.url == f"{_BARE}?wmsAuthSign={_TOK_QUOTED}"
    assert out.stream_type == "hls"
    # the cached URL must lapse long before the 24h token it carries does
    assert out.ttl_seconds is not None
    assert out.ttl_seconds <= 3600


def test_beachcam_replaces_a_token_a_target_already_carries() -> None:
    # another source's copy of the same cam can carry its own, older token
    calls: list[str] = []
    out = BeachcamResolver(_fetch([_TOK], calls), clock=_Clock()).resolve(
        f"{_BARE}?wmsAuthSign=c3RhbGUtdG9rZW4tZnJvbS1lbHNld2hlcmU="
    )
    assert out.url == f"{_BARE}?wmsAuthSign={_TOK_QUOTED}"


def test_beachcam_one_token_serves_resolves_until_its_ttl_lapses() -> None:
    calls: list[str] = []
    clock = _Clock()
    newer = "bmV3ZXItdGVzdC1vbmx5LXRva2VuLXZhbHVl"
    resolver = BeachcamResolver(_fetch([_TOK, newer], calls), clock=clock)

    resolver.resolve(_BARE)
    clock.now += 50 * 60  # inside the token's in-process hold
    resolver.resolve(_BARE.replace("carcavelos2", "alvor"))
    assert len(calls) == 1

    clock.now += 20 * 60  # past it: a fresh token, long before the real 24h
    out = resolver.resolve(_BARE)
    assert len(calls) == 2
    assert out.url.endswith(f"wmsAuthSign={newer}")


@pytest.mark.parametrize(
    "body",
    ["<html>Forbidden</html>", "", ValueError("resolver fetch failed: http-503")],
)
def test_beachcam_without_a_token_fails_with_no_url(body: str | Exception) -> None:
    resolver = BeachcamResolver(_fetch([body], []), clock=_Clock())
    with pytest.raises(ValueError, match="beachcam") as exc:
        resolver.resolve(_BARE)
    # the message feeds the aggregated resolve-failed detail: no URL, no cam
    assert "://" not in str(exc.value)
    assert "carcavelos" not in str(exc.value)


def test_beachcam_failed_token_fetch_is_not_retried_for_a_minute() -> None:
    # a build resolves ~190 cams at once: with the endpoint down, each must fail at
    # once rather than queue behind its own matrix fetch
    calls: list[str] = []
    clock = _Clock()
    resolver = BeachcamResolver(_fetch(["nope", _TOK], calls), clock=clock)

    for _ in range(3):
        with pytest.raises(ValueError, match="beachcam"):
            resolver.resolve(_BARE)
    assert len(calls) == 1

    clock.now += 61
    assert resolver.resolve(_BARE).url.endswith(_TOK_QUOTED)
    assert len(calls) == 2


def test_beachcam_keeps_its_token_through_a_failed_refresh() -> None:
    # the hold lapses long before the token does: an endpoint down at that moment
    # must not take every cam down while the token in hand still plays
    calls: list[str] = []
    clock = _Clock()
    down = ValueError("resolver fetch failed: http-503")
    newer = "bmV3ZXItdGVzdC1vbmx5LXRva2VuLXZhbHVl"
    resolver = BeachcamResolver(_fetch([_TOK, down, down, newer], calls), clock=clock)

    resolver.resolve(_BARE)
    clock.now += 2 * 3600
    assert resolver.resolve(_BARE).url.endswith(_TOK_QUOTED)
    assert len(calls) == 2
    resolver.resolve(_BARE)  # the failed refresh isn't retried at once
    assert len(calls) == 2

    clock.now += 61
    assert resolver.resolve(_BARE).url.endswith(_TOK_QUOTED)  # still down
    clock.now += 61
    assert resolver.resolve(_BARE).url.endswith(f"wmsAuthSign={newer}")


def test_beachcam_fails_once_the_token_in_hand_nears_its_end() -> None:
    clock = _Clock()
    resolver = BeachcamResolver(
        _fetch([_TOK, ValueError("resolver fetch failed: http-503")], []), clock=clock
    )
    resolver.resolve(_BARE)
    clock.now += 23 * 3600 + 1
    with pytest.raises(ValueError, match="beachcam"):
        resolver.resolve(_BARE)


def test_beachcam_concurrent_resolves_share_one_token_fetch() -> None:
    # a build resolves the whole source at once: one matrix fetch must serve them all
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def slow_fetch(url: str) -> str:
        calls.append(url)
        entered.set()
        release.wait(5)
        return _TOK

    resolver = BeachcamResolver(slow_fetch, clock=_Clock())
    threads = [
        threading.Thread(target=resolver.resolve, args=(_BARE,)) for _ in range(4)
    ]
    for t in threads:
        t.start()
    entered.wait(5)
    release.set()
    for t in threads:
        t.join(5)
    assert calls == [_MATRIX]
