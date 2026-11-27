"""Deduplication: merge every copy of one stream, within a source or across sources."""

from __future__ import annotations

from dataclasses import replace
from functools import reduce
from typing import TYPE_CHECKING

from webcam_aggregator.categories import is_mapped

if TYPE_CHECKING:
    from webcam_aggregator.models import Candidate

_CAT_RANK = {"cxtvlive": 2, "worldcams": 1, "youtube-api": 0}


def _category(ranked: list[Candidate]) -> str | None:
    # A category we can map beats one we can't, whichever source ranks higher: an
    # unrecognised slug would only land the cam in "Unmapped Category".
    cats = [c.category for c in ranked if c.category]
    return next((cat for cat in cats if is_mapped(cat)), cats[0] if cats else None)


def _merge(a: Candidate, b: Candidate) -> Candidate:
    if (
        a.name_rank is not None
        and b.name_rank is not None
        and a.name_rank != b.name_rank
    ):
        # The source said which copy names the cam best: it is the base (its page
        # fixes the stable id) and its title stands.
        best, other = (a, b) if a.name_rank < b.name_rank else (b, a)
        return replace(best, category=_category([best, other]))
    # base: the highest-ranked source, then a copy with a category, a mapped one first
    ranked = sorted(
        [a, b],
        key=lambda c: (
            _CAT_RANK.get(c.source, -1),
            bool(c.category),
            is_mapped(c.category),
        ),
        reverse=True,
    )
    # title: YouTube API's canonical title for a YT cam, else the longest non-empty
    # title
    yt = next((c for c in (a, b) if c.source == "youtube-api" and c.title), None)
    title = yt.title if yt else max((a.title, b.title), key=len)
    return replace(ranked[0], title=title, category=_category(ranked))


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Merge the candidates that are one stream, keeping the best of each field.

    This is the only place repeats are merged, for a stream listed on several pages
    of one source as much as for one found by several sources, so every source gets
    the same fields for the same situation. Two candidates are one stream when they
    share a `predisc_key`, or, lacking one, carry the identical `target_url`: an
    extractor resolves from the target alone, so one URL is one stream.
    """
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        groups.setdefault(c.predisc_key or f"target:{c.target_url}", []).append(c)
    return [_fold(copies) for copies in groups.values()]


def _fold(copies: list[Candidate]) -> Candidate:
    # The copies a source ranked settle among themselves first, at the first one's
    # place: merged pairwise against another source's copy, each would fall back to
    # the longest title, and the worst-ranked could win.
    ranked = [i for i, c in enumerate(copies) if c.name_rank is not None]
    if len(ranked) > 1:
        best = reduce(_merge, (copies[i] for i in ranked))
        copies = [
            best if i == ranked[0] else c
            for i, c in enumerate(copies)
            if c.name_rank is None or i == ranked[0]
        ]
    return reduce(_merge, copies)
