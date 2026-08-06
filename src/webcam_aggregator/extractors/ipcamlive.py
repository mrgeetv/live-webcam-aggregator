from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# ipcamlive embeds: g*.ipcamlive.com/player/player.php?alias=<id>. The player page exposes
# `var address` + `var streamid`, which build <address>streams/<streamid>/stream.m3u8.
# streamid is EMPTY for an offline cam -> unresolvable (raise; liveness validation drops it).
#
# Route ONLY player/player.php URLs and bare alias landing pages here. Direct
# s*.ipcamlive.com/streams/<id>/stream.m3u8 URLs (the majority) must fall through to
# DirectHls, so the predicates below match those two shapes, NOT a blanket
# *.ipcamlive.com host.
_ADDRESS = re.compile(r'var\s+address\s*=\s*["\']([^"\']+)["\']')
_STREAMID = re.compile(r'var\s+streamid\s*=\s*["\']([^"\']+)["\']')

# Some embeds point at the share/landing page, https://www.ipcamlive.com/<alias>, which
# carries neither `address`/`streamid` nor a link to the player — it builds the player
# client-side. The alias in the path is all the player URL needs, so rewrite to that
# rather than trying to scrape the landing page. Anchored to the apex/www host so the
# s*/g* subdomains can't match.
# Any single-segment path on the apex/www host matches, so a non-cam page (/faq, say)
# would route here and surface as resolve-failed rather than no-extractor. Accepted:
# candidates only arrive from scraped embeds, and a denylist of the site's own pages
# would be guesswork that rots.
_ALIAS_URL = re.compile(r"^https?://(?:www\.)?ipcamlive\.com/([A-Za-z0-9_-]+)/?$", re.I)
_PLAYER = "https://g0.ipcamlive.com/player/player.php?alias="


def is_alias_page(url: str) -> bool:
    """True for a bare https://www.ipcamlive.com/<alias> landing page."""
    return _ALIAS_URL.match(url) is not None


def _player_url(target_url: str) -> str:
    m = _ALIAS_URL.match(target_url)
    return f"{_PLAYER}{m.group(1)}" if m else target_url


class IpcamliveResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        player_url = _player_url(target_url)
        body = self._fetch(player_url)
        addr = _ADDRESS.search(body)
        sid = _STREAMID.search(body)
        if not (addr and sid):
            raise ValueError(
                f"ipcamlive: no address/streamid (offline?) in {player_url}"
            )
        base = addr.group(1).rstrip("/")
        return Resolved(
            url=f"{base}/streams/{sid.group(1)}/stream.m3u8",
            stream_type="hls",
            ttl_seconds=None,
        )
