"""Deduplication: merge every copy of one stream, within a source or across sources."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webcam_aggregator.models import Candidate

_CAT_RANK = {"cxtvlive": 2, "worldcams": 1, "youtube-api": 0}


def _merge(a: Candidate, b: Candidate) -> Candidate:
    # category: highest-ranked source that actually has a non-empty category
    ranked = sorted(
        [a, b],
        key=lambda c: (_CAT_RANK.get(c.source, -1), bool(c.category)),
        reverse=True,
    )
    category = next((c.category for c in ranked if c.category), None)
    # title: YouTube API's canonical title for a YT cam, else the longest non-empty
    # title
    yt = next((c for c in (a, b) if c.source == "youtube-api" and c.title), None)
    title = yt.title if yt else max((a.title, b.title), key=len)
    return replace(ranked[0], title=title, category=category)


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Merge the candidates that are one stream, keeping the best of each field.

    This is the only place repeats are merged, for a stream listed on several pages
    of one source as much as for one found by several sources, so every source gets
    the same fields for the same situation. Two candidates are one stream when they
    share a `predisc_key`, or, lacking one, carry the identical `target_url`: an
    extractor resolves from the target alone, so one URL is one stream.
    """
    by_key: dict[str, Candidate] = {}
    for c in candidates:
        key = c.predisc_key or f"target:{c.target_url}"
        existing = by_key.get(key)
        by_key[key] = _merge(existing, c) if existing else c
    return list(by_key.values())
