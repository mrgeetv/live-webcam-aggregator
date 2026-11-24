"""Direct HLS extractor: a target that is already an m3u8 URL is its own stream."""

from __future__ import annotations

import re
from urllib.parse import unquote

from webcam_aggregator.extractors.base import Resolved


def unwrap(url: str) -> str:
    """Return the m3u8 URL a `?url=` wrapper carries, else `url` itself."""
    m = re.search(r"[?&]url=(.+)$", url, re.DOTALL)
    return unquote(m.group(1)) if m else url


class DirectHls:
    """Serve a target that is already a direct HLS URL."""

    def resolve(self, target_url: str) -> Resolved:
        """Return `target_url`, unwrapped, with no TTL."""
        return Resolved(url=unwrap(target_url), stream_type="hls", ttl_seconds=None)
