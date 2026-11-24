from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from webcam_aggregator.extractors.base import Resolved

if TYPE_CHECKING:
    from collections.abc import Callable

# balticlivecam is a WordPress site. The embed page carries the numeric camera id in
# `var data = { action: 'auth_token', id: <N>, embed: 1 }`. POSTing
# action=auth_token&id=<N>&embed=1 to admin-ajax.php (with an XHR Referer header) returns an
# HTML player fragment containing the tokenised m3u8 (edge*.balticlivecam.com/...?token=h:<epoch_ms>).
_AJAX = "https://balticlivecam.com/wp-admin/admin-ajax.php"
# The span to `id:` stops at the next auth_token, so each attempt ends where the next
# one starts, and is possessive, so it keeps no backtracking state per character.
_ID = re.compile(r"auth_token['\"](?:(?!auth_token|\bid:)[^}])*+\bid:\s*(\d+)")
# A whole URL run, kept if it holds `.m3u8` (why: sources.base._URL_RUN).
_URL_RUN = re.compile(r"https?://[^\"'\s\\]+")
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
            # no URL in the message — see the aggregated-detail rule in AGENTS.md
            raise ValueError("baltic: no camera id in target")
        fragment = self._post(
            _AJAX,
            {
                "action": "auth_token",
                "id": cam.group(1),
                "embed": "1",
                "main_referer": "",
            },
        )
        found = next(
            (m for m in _URL_RUN.finditer(fragment) if ".m3u8" in m.group(0)), None
        )
        if not found:
            raise ValueError(
                f"baltic: no m3u8 in auth_token response for id {cam.group(1)}"
            )
        url = found.group(0)
        epoch = _EPOCH_MS.search(url)
        ttl = int(epoch.group(1)) // 1000 - int(time.time()) if epoch else None
        return Resolved(url=url, stream_type="hls", ttl_seconds=ttl)
