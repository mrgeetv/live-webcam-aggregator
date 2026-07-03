from __future__ import annotations

import re
from collections.abc import Callable

from .base import Resolved

# ipcamlive embeds: g*.ipcamlive.com/player/player.php?alias=<id>. The player page exposes
# `var address` + `var streamid`, which build <address>streams/<streamid>/stream.m3u8.
# streamid is EMPTY for an offline cam -> unresolvable (raise; liveness validation drops it).
#
# Route ONLY player/player.php URLs here. Direct s*.ipcamlive.com/streams/<id>/stream.m3u8
# URLs (the majority) must fall through to DirectHls, so the predicate matches the
# player/player.php path, NOT a blanket *.ipcamlive.com host.
_ADDRESS = re.compile(r'var\s+address\s*=\s*["\']([^"\']+)["\']')
_STREAMID = re.compile(r'var\s+streamid\s*=\s*["\']([^"\']+)["\']')


class IpcamliveResolver:
    _fetch: Callable[[str], str]

    def __init__(self, fetch: Callable[[str], str]) -> None:
        self._fetch = fetch

    def resolve(self, target_url: str) -> Resolved:
        body = self._fetch(target_url)
        addr = _ADDRESS.search(body)
        sid = _STREAMID.search(body)
        if not (addr and sid):
            raise ValueError(
                f"ipcamlive: no address/streamid (offline?) in {target_url}"
            )
        base = addr.group(1).rstrip("/")
        return Resolved(
            url=f"{base}/streams/{sid.group(1)}/stream.m3u8",
            stream_type="hls",
            ttl_seconds=None,
        )
