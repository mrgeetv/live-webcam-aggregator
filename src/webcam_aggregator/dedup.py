from __future__ import annotations

from dataclasses import replace

from .models import Candidate

_CAT_RANK = {"cxtvlive": 2, "worldcams": 1, "youtube-api": 0}


def _merge(a: Candidate, b: Candidate) -> Candidate:
    # category: highest-ranked source that actually has a non-empty category
    ranked = sorted(
        [a, b],
        key=lambda c: (_CAT_RANK.get(c.source, -1), bool(c.category)),
        reverse=True,
    )
    category = next((c.category for c in ranked if c.category), None)
    # title: YouTube API's canonical title for a YT cam, else the longest non-empty title
    yt = next((c for c in (a, b) if c.source == "youtube-api" and c.title), None)
    title = yt.title if yt else max((a.title, b.title), key=len)
    return replace(ranked[0], title=title, category=category)


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    by_key: dict[str, Candidate] = {}
    no_key: list[Candidate] = []
    for c in candidates:
        if c.predisc_key:
            existing = by_key.get(c.predisc_key)
            by_key[c.predisc_key] = _merge(existing, c) if existing else c
        else:
            no_key.append(c)  # channel/playlist/iframe — never collapsed
    return list(by_key.values()) + no_key
