"""camscape.com source: sitemap -> cam pages -> embeds, camsecure resolved early."""

from __future__ import annotations

import json
import logging
import re
from typing import override
from urllib.parse import urlsplit

from webcam_aggregator.models import Candidate
from webcam_aggregator.sources.base import (
    HtmlScraperSource,
    predisc_key,
    with_location_parts,
)
from webcam_aggregator.sources.camsecure import hls_from_player, player_iframe

log = logging.getLogger("webcam-aggregator.camscape")

_BASE = "https://www.camscape.com"

# camscape category slug -> a categories._MAP key (mapped to the unified taxonomy at
# build). The granular animal sub-cats all collapse to Animals; None -> "Other".
_CATEGORY: dict[str, str | None] = {
    "alpacas": "Animals",
    "animals": "Animals",
    "bats": "Animals",
    "beaches": "Beaches",
    "bears": "Animals",
    "beavers": "Animals",
    "bees": "Animals",
    "big-cats": "Animals",
    "big-dogs": "Animals",
    "birds": "Birds",
    "boats": "Ships",
    "bovines": "Animals",
    "buildings": "Buildings",
    "business-miscellaneous": None,
    "christmas": "Christmas",
    "cityscapes": "Cities",
    "critters": "Animals",
    "culture": None,
    "deer": "Animals",
    "domestic-animals": "Animals",
    "donkeys": "Animals",
    "elephants": "Animals",
    "entertainment": "Entertainment",
    "giraffes": "Animals",
    "goats": "Animals",
    "horses": "Animals",
    "landscapes": "Parks",
    "marsupials": "Animals",
    "monsters": None,
    "nature": "Parks",
    "nightlife": "Bars",
    # people/scenery/transport are umbrella categories spanning several of ours; left
    # uncategorised so the title fallback decides per cam.
    "people": None,
    "pigs": "Animals",
    "planes-airports": "Airports",
    "primates": "Animals",
    "religion": "Religion",
    "rivers-seas-lakes": "Rivers Lakes",
    "roads": "Traffic",
    "scenery": None,
    "sealife": "Animals",
    "shopping-district": None,
    "ski-resorts": "Ski Resorts",
    "space-astronomy": "Space",
    "squirrels": "Animals",
    "tortoises": "Animals",
    "tourist-attractions": "Sights",
    "trains-railways": "Trains",
    "transport": None,
    "urban-spaces": "Cities",
    "zebras": "Animals",
    "zoos": "Animals",
}

_CAT_SLUG = re.compile(r'href="/showing/([a-z0-9-]+)/"')
_WEBCAM_SITEMAP = re.compile(
    r"<loc>(https://www\.camscape\.com/webcam-sitemap\d*\.xml)</loc>"
)
_CAM = re.compile(r"<loc>(https://www\.camscape\.com/webcam/[a-z0-9-]+/)</loc>")
# The cam's own tags are its `where-link`s. Every page's nav also links a seasonal
# category (/showing/christmas/), so an unanchored /showing/ match tags every cam with
# it.
_TAG = re.compile(r'class="where-link" href="/showing/([a-z0-9-]+)/"')
_LOC = re.compile(r'class="where-link" href="/location/([a-z0-9-]+)/"')
_STREAMS = '<script id="streams-data" type="application/json">'
_CHANNEL = re.compile(r"[?&]channel=([A-Za-z0-9_]+)")


def _is_camsecure(url: str) -> bool:
    """Match a camsecure embed (camsecure.co / .uk / .co.uk, any subdomain).

    The embed is resolved to its HLS below. It is read from the hostname's labels: as a
    regex, a run of label repeats and the `camsecure.` literal compete for the same
    characters.
    """
    parts = urlsplit(url)
    labels = (parts.hostname or "").split(".")
    return "camsecure" in labels[:-1] and parts.path.startswith("/")


# ctx = (location parts, {stream index: name})
_Ctx = tuple[list[str], dict[str, str]]


def _streams(html: str) -> list[dict[str, object]]:
    # The cam's streams (every angle's embed url + name) live in a JSON script block;
    # the rendered player only shows the active one, so parse the JSON.
    i = html.find(_STREAMS)
    if i < 0:
        return []
    try:
        arr, _ = json.JSONDecoder().raw_decode(html, html.index("[", i))
    except ValueError:
        return []
    return [s for s in arr if isinstance(s, dict)] if isinstance(arr, list) else []


class CamscapeSource(HtmlScraperSource[_Ctx]):
    """camscape.com cams, listed by its sitemaps; each page's JSON holds the embeds."""

    name: str = "camscape"

    @override
    def _page_urls(self) -> list[str]:
        # Crawl-first coverage: flag category slugs we have no mapping for, so a new
        # camscape category surfaces (its cams also land in "Unmapped Category") instead
        # of silently dropping to "Other".
        slugs = set(_CAT_SLUG.findall(self._fetch.get(f"{_BASE}/showing/") or ""))
        unmapped = sorted(s for s in slugs if s not in _CATEGORY)
        if unmapped:
            log.warning(
                "camscape: %d category page(s) not in _CATEGORY "
                "(-> Unmapped Category): %s",
                len(unmapped),
                ", ".join(unmapped),
            )
        # The webcam sitemaps list exactly the cams the category pages show as live,
        # without the stale ones in each category page's "Gone quiet" box.
        index = self._fetch.get(f"{_BASE}/sitemap_index.xml") or ""
        cams: set[str] = set()
        for sitemap in _WEBCAM_SITEMAP.findall(index):
            cams.update(_CAM.findall(self._fetch.get(sitemap) or ""))
        return sorted(cams)

    @override
    def _page_meta(self, html: str, url: str) -> tuple[str | None, _Ctx]:
        tags = _TAG.findall(html)
        category = next((_CATEGORY[t] for t in tags if _CATEGORY.get(t)), None)
        if category is None:
            # No mapped tag: surface an unknown tag so it lands in "Unmapped Category"
            # (+ gets logged); else None -> "Other" (untagged, or only None-mapped
            # tags).
            category = next((t for t in tags if t not in _CATEGORY), None)
        locs = dict.fromkeys(_LOC.findall(html))
        location = [loc.replace("-", " ").title() for loc in locs]
        names = {str(i): str(s.get("name") or "") for i, s in enumerate(_streams(html))}
        return category, (location, names)

    @override
    def _title_for(
        self, cand: Candidate, url: str, category: str | None, ctx: _Ctx
    ) -> str:
        location, names = ctx
        return with_location_parts(
            names.get(cand.angle_key or "", ""), location, drop=category or ""
        )

    def _camsecure_hls(self, embed: str) -> str | None:
        """Resolve a camsecure embed to its direct HLS, or None if it doesn't resolve.

        camscape embeds camsecure players (including white-label ones for third
        parties, which are absent from camsecure's own sitemap and so unreachable
        by that source). Resolving here rather than at serve-time is what lets
        dedup work: the m3u8 is the key both sources share, and it cannot be
        derived from the embed URL — `cityair1.html` serves `cityair.m3u8`, and
        `portland_harbour_webcam.html` serves `weymouthsailing.m3u8`. The URL is a
        static open-CDN path (no token), which is why the camsecure source stores
        it the same way. The hops cost a fetch or two per camsecure embed, far
        cheaper than the duplicate channels we would otherwise ship.
        """
        page = self._fetch.get(embed) or ""
        direct = hls_from_player(page, embed)
        if direct:
            return direct
        # Not a player page but a cam page: follow its player iframe (one more hop).
        player = player_iframe(page)
        return (
            hls_from_player(self._fetch.get(player) or "", player) if player else None
        )

    @override
    def _candidates(self, html: str, url: str) -> list[Candidate]:
        out: list[Candidate] = []
        for i, s in enumerate(_streams(html)):
            embed = str(s.get("url") or "")
            if not embed:
                continue
            # Twitch player embeds need normalising to twitch.tv/<channel> for yt-dlp.
            m = _CHANNEL.search(embed) if "player.twitch.tv" in embed else None
            target = f"https://www.twitch.tv/{m.group(1)}" if m else embed
            if _is_camsecure(target) and ".m3u8" not in target:
                target = self._camsecure_hls(target) or target
            out.append(
                Candidate(
                    title="",
                    angle_key=str(i),
                    category=None,
                    source=self.name,
                    source_page_url=url,
                    target_url=target,
                    predisc_key=predisc_key(target),
                )
            )
        return out
