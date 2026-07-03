from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from .categories import unknown_categories

_DEFAULT_SEARCH_QUERY = (
    "cam|webcam|live|beach|wildlife|aquarium|space|harbor|park|mountain|coast|city"
    "|traffic|nature|zoo -gameplay -playing -subscriber -donation -follower -facecam"
    " -reaction -chatting -gaming -fortnite -troll -asmr -twitch"
)

log = logging.getLogger("webcam-aggregator.config")
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# v1 env vars that v2 ignores — warn so migrators aren't caught out by silent no-ops.
_LEGACY_VARS: dict[str, str | None] = {
    "UPDATE_INTERVAL_HOURS": "CATALOGUE_INTERVAL_HOURS",
    "EXCLUDED_CATEGORIES": "EXCLUDE_CATEGORIES",
    "MAX_VIDEOS_PER_CYCLE": None,
    "CONCURRENT_EXTRACTIONS": None,
}


@dataclass(frozen=True)
class Config:
    youtube_api_key: str
    public_base_url: str
    catalogue_interval_hours: int
    search_query: str
    log_level: str
    exclude_categories: frozenset[str]
    proxy_youtube: bool
    max_parallel_sources: int


def _int_env(env: dict[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        log.warning("invalid %s=%r — using default %d", key, raw, default)
        return default


def _csv_set(raw: str) -> frozenset[str]:
    # Comma-separated, stored casefolded so category matching is case-insensitive.
    return frozenset(p.strip().casefold() for p in raw.split(",") if p.strip())


def _bool_env(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0", ""):
        return False
    log.warning(
        "invalid %s=%r — expected true/false; using default %s", key, raw, default
    )
    return default


def _warn_on_suspect_config(cfg: Config) -> None:
    if cfg.log_level not in _VALID_LOG_LEVELS:
        log.warning("unknown LOG_LEVEL=%r — falling back to INFO", cfg.log_level)
    host = urlsplit(cfg.public_base_url).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        log.warning(
            "PUBLIC_BASE_URL is %s — fine for local use, but remote players won't "
            "reach the /stream URLs; set it to an address clients can actually reach",
            cfg.public_base_url,
        )
    unknown = unknown_categories(cfg.exclude_categories)
    if unknown:
        log.warning(
            "EXCLUDE_CATEGORIES: ignoring unknown categories %s (see the README list)",
            ", ".join(sorted(unknown)),
        )


def _warn_legacy_env(env: dict[str, str]) -> None:
    for old, new in _LEGACY_VARS.items():
        if not env.get(old):
            continue
        if new:
            log.warning(
                "%s is a v1 setting, ignored in v2 — did you mean %s?", old, new
            )
        else:
            log.warning("%s is a v1 setting, ignored in v2 (removed)", old)


def load(env: dict[str, str] | None = None) -> Config:
    e = env if env is not None else dict(os.environ)
    key = e.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise ValueError("YOUTUBE_API_KEY is required")
    cfg = Config(
        youtube_api_key=key,
        public_base_url=e.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        catalogue_interval_hours=_int_env(e, "CATALOGUE_INTERVAL_HOURS", 6, 1),
        search_query=e.get("SEARCH_QUERY", "").strip() or _DEFAULT_SEARCH_QUERY,
        log_level=e.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        exclude_categories=_csv_set(e.get("EXCLUDE_CATEGORIES", "")),
        proxy_youtube=_bool_env(e, "PROXY_YOUTUBE", False),
        max_parallel_sources=_int_env(e, "MAX_PARALLEL_SOURCES", 4, 1),
    )
    _warn_on_suspect_config(cfg)
    _warn_legacy_env(e)
    return cfg
