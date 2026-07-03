from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Resolved:
    url: str  # manifest or media URL
    stream_type: str  # "hls" | "mp4"
    ttl_seconds: int | None  # None = unknown/stable


class Extractor(Protocol):
    def resolve(self, target_url: str) -> Resolved: ...
