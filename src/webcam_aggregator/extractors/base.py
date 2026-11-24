"""Extractor contract: `Resolved` and the `Extractor` protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Resolved:
    """A resolved stream: its URL, its type (`hls` or `mp4`) and a TTL."""

    url: str  # manifest or media URL
    stream_type: str  # "hls" | "mp4"
    ttl_seconds: int | None  # None = unknown/stable


class Extractor(Protocol):
    """Turns a cam's target URL into a playable stream."""

    def resolve(self, target_url: str) -> Resolved:
        """Resolve `target_url` to a `Resolved`, raising `ValueError` if it can't."""
        ...
