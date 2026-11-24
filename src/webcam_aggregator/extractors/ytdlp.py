from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable

from .base import Resolved

# We hand the player ONE url, so we need a *muxed* rendition (youtube's live
# itags 91-96, video and audio in a single HLS ladder). Only some player clients
# offer those: yt-dlp's default clients rotate, and can serve only video-only +
# audio-only formats, which this extractor cannot use.
# mweb and web_embedded both carry the muxed ladder at full quality; the
# second is a fallback for mweb being withdrawn, and costs ~1s per resolve.
# Revisit whenever yt-dlp changes its default clients — this pin exists to buy a
# muxed format, not because mweb is special.
_MUXED_CLIENTS = "youtube:player_client=mweb,web_embedded"


_TIMEOUT = 60

# yt-dlp names the video or channel in every error (`ERROR: [youtube] <id>: …`) and
# tacks a bug-report plea onto many. The error is the bucket key of the build's
# resolve-failure aggregate, so both go: with the id left in, one failure mode
# across every cam reads as a handful of single-cam buckets. The serve-path warning
# names the cam through its target URL.
_ERROR_SUBJECT = re.compile(r"^(ERROR: \[[\w:]+\]) [^\s:]+: ", re.MULTILINE)
_REPORT_PLEA = re.compile(r";? please report this issue on [^\n]*")


def _default_run(argv: list[str]) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        # its own message is the whole argv, which buries the reason in the
        # truncated resolve-failure detail
        raise ValueError(f"yt-dlp timed out after {_TIMEOUT}s") from None
    if r.returncode != 0 or not r.stdout.strip():
        reason = _REPORT_PLEA.sub("", _ERROR_SUBJECT.sub(r"\1 ", r.stderr)).strip()
        raise ValueError(f"yt-dlp failed: {reason[:200]}")
    return r.stdout.strip()


class YtDlpExtractor:
    _run: Callable[[list[str]], str]

    def __init__(self, run: Callable[[list[str]], str] = _default_run) -> None:
        self._run = run

    def resolve(self, target_url: str) -> Resolved:
        # Prefer an HLS (m3u8) format. Some live streams default to a DASH .mpd,
        # which our HLS manifest proxy can't serve; fall back to best if no HLS.
        out = self._run(
            [
                "yt-dlp",
                "-q",
                "--no-warnings",
                "--extractor-args",
                _MUXED_CLIENTS,
                "-f",
                "b[protocol*=m3u8]/b",
                "-g",
                "--",
                target_url,
            ]
        )
        # `-g` prints one url per selected format, so two lines means a split
        # (video-only + audio-only) format slipped through. Fail loudly: picking
        # a line would silently serve an audio-only cam that looks healthy.
        lines = out.splitlines()
        if len(lines) != 1:
            raise ValueError("yt-dlp returned a split format, not a muxed stream")
        url = lines[0]
        m = re.search(r"expire[/=](\d+)", url)
        ttl = int(m.group(1)) - int(time.time()) if m else None
        return Resolved(url=url, stream_type="hls", ttl_seconds=ttl)
