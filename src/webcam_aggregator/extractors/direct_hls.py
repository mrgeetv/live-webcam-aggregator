from __future__ import annotations

import re
from urllib.parse import unquote

from .base import Resolved


def unwrap(url: str) -> str:
    m = re.search(r"[?&]url=(.+)$", url)
    return unquote(m.group(1)) if m else url


class DirectHls:
    def resolve(self, target_url: str) -> Resolved:
        return Resolved(url=unwrap(target_url), stream_type="hls", ttl_seconds=None)
