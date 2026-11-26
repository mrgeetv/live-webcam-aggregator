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
    clock.now += 11 * 3600  # inside the token's in-process life
    resolver.resolve(_BARE.replace("carcavelos2", "alvor"))
    assert len(calls) == 1

    clock.now += 2 * 3600  # past it: a fresh token, well before the real 24h
    out = resolver.resolve(_BARE)
    assert len(calls) == 2
    assert out.url.endswith(f"wmsAuthSign={newer}")


@pytest.mark.parametrize(
    "body", ["<html>Forbidden</html>", "", ValueError("resolver fetch failed: x")]
)
def test_beachcam_without_a_token_fails_with_no_url(body: str | Exception) -> None:
    resolver = BeachcamResolver(_fetch([body], []), clock=_Clock())
    with pytest.raises(ValueError, match="beachcam") as exc:
        resolver.resolve(_BARE)
    # the message feeds the aggregated resolve-failed detail: no URL, no cam
    assert "http" not in str(exc.value)
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
