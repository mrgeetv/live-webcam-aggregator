from __future__ import annotations

import re
import time
from collections.abc import Callable

from .base import Resolved

# balticlivecam is a WordPress site. The embed page carries the numeric camera id in
# `var data = { action: 'auth_token', id: <N>, embed: 1 }`. POSTing
# action=auth_token&id=<N>&embed=1 to admin-ajax.php (with an XHR Referer header) returns an
# HTML player fragment containing the tokenised m3u8 (edge*.balticlivecam.com/...?token=h:<epoch_ms>).
_AJAX = "https://balticlivecam.com/wp-admin/admin-ajax.php"
_ID = re.compile(r"auth_token['\"][^}]*?\bid:\s*(\d+)", re.S)
_M3U8 = re.compile(r"https?://[^\"'\s\\]+\.m3u8[^\"'\s\\]*")
_EPOCH_MS = re.compile(r":(\d{13})")


class BalticResolver:
    _get: Callable[[str], str]
    _post: Callable[[str, dict[str, str]], str]

    def __init__(
        self,
        get: Callable[[str], str],
        post: Callable[[str, dict[str, str]], str],
    ) -> None:
        self._get = get
        self._post = post

    def resolve(self, target_url: str) -> Resolved:
        page = self._get(target_url)
        cam = _ID.search(page)
        if not cam:
            raise ValueError(f"baltic: no camera id in {target_url}")
        fragment = self._post(
            _AJAX,
            {
                "action": "auth_token",
                "id": cam.group(1),
                "embed": "1",
                "main_referer": "",
            },
        )
        found = _M3U8.search(fragment)
        if not found:
            raise ValueError(
                f"baltic: no m3u8 in auth_token response for id {cam.group(1)}"
            )
        url = found.group(0)
        epoch = _EPOCH_MS.search(url)
        ttl = int(epoch.group(1)) // 1000 - int(time.time()) if epoch else None
        return Resolved(url=url, stream_type="hls", ttl_seconds=ttl)
