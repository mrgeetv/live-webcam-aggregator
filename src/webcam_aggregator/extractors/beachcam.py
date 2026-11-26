"""MEO Beachcam extractor: bare iol.pt m3u8 + a freshly minted wmsAuthSign token."""

from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# The bare video-auth1.iol.pt playlist answers 403; the site's JW player appends
# ?wmsAuthSign=<token> fetched from this IOL endpoint (an anonymous userId works). The
# token is a Wowza SecureToken that decodes to `server_time=…&validminutes=1440`: one
# token, any cam, 24h. Its segments are relayed (serving._PROXY_SEGMENT_HOSTS).
_TOKEN_API = "https://services.iol.pt/matrix?userId="  # noqa: S105 - a URL, no secret
# a real token is a base64 blob; anything else (an error page) is not a token
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")
# Held for half the token's 24h life, so a token handed out near the end of its hold
# still outlives the resolve cache's hold on the URL carrying it.
_TOKEN_HOLD = 12 * 3600
# A failed token fetch answers every resolve for this long without a new fetch: a
# build resolves the whole source at once, and each resolve would otherwise wait out
# its own fetch of a dead endpoint, one after another behind the lock.
_FAILURE_TTL = 60


class BeachcamResolver:
    """Resolve a bare beachcam m3u8 to a playable one by appending an IOL token.

    One token serves every cam, so it is fetched once and held in-process.
    """

    def __init__(
        self, fetch: Callable[[str], str], clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Take the `fetch` callable that returns a body as text, and a clock."""
        self._fetch: Callable[[str], str] = fetch
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._token: str | None = None
        self._failure: str | None = None
        self._expires: float = 0.0

    def _get_token(self) -> str:
        with self._lock:
            now = self._clock()
            if now >= self._expires:
                try:
                    self._token, self._failure = self._mint(), None
                    self._expires = now + _TOKEN_HOLD
                except ValueError as exc:
                    self._token, self._failure = None, str(exc)
                    self._expires = now + _FAILURE_TTL
            if self._token is None:
                raise ValueError(self._failure)
            return self._token

    def _mint(self) -> str:
        # Messages carry no URL: they feed the aggregated resolve-failed detail.
        try:
            body = self._fetch(_TOKEN_API).strip()
        except ValueError as exc:
            msg = f"beachcam: token fetch failed ({exc})"
            raise ValueError(msg) from None
        if not _TOKEN_SHAPE.match(body):
            msg = "beachcam: matrix returned no token"
            raise ValueError(msg)
        return body

    def resolve(self, target_url: str) -> Resolved:
        """Resolve `target_url` to its playlist with a current token; raise if none."""
        token = self._get_token()
        p = urlsplit(target_url)
        # Any query the target carries is dropped: the only one the CDN reads is the
        # token, and another source's copy of the cam may carry an older one.
        # The token is base64 and can hold '+', which a bare query decodes to a
        # space (a 403); '=' padding stays literal.
        return Resolved(
            url=f"{p.scheme}://{p.netloc}{p.path}?wmsAuthSign={quote(token, safe='=')}",
            stream_type="hls",
            # Short next to the token's hold, and cheap: a re-resolve costs no fetch
            # while the held token lasts.
            ttl_seconds=1800,
        )
