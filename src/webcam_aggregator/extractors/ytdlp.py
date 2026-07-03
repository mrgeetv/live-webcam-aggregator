from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable

from .base import Resolved


def _default_run(argv: list[str]) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise ValueError(f"yt-dlp failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip().splitlines()[-1]


class YtDlpExtractor:
    _run: Callable[[list[str]], str]

    def __init__(self, run: Callable[[list[str]], str] = _default_run) -> None:
        self._run = run

    def resolve(self, target_url: str) -> Resolved:
        # Prefer an HLS (m3u8) format. Some live streams default to a DASH .mpd,
        # which our HLS manifest proxy can't serve; fall back to best if no HLS.
        url = self._run(
            [
                "yt-dlp",
                "-q",
                "--no-warnings",
                "-f",
                "b[protocol*=m3u8]/b",
                "-g",
                "--",
                target_url,
            ]
        )
        m = re.search(r"expire[/=](\d+)", url)
        ttl = int(m.group(1)) - int(time.time()) if m else None
        return Resolved(url=url, stream_type="hls", ttl_seconds=ttl)
