from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


@dataclass(frozen=True)
class Candidate:
    title: str
    angle_key: str | None
    category: str | None
    source: str
    source_page_url: str
    target_url: str
    predisc_key: str | None


@dataclass(frozen=True)
class CatalogueEntry:
    id: str
    title: str
    category: str
    source: str
    source_page_url: str
    target_url: str


def _canonical_url(url: str) -> str:
    s = urlsplit(url)
    host = s.hostname.lower() if s.hostname else ""
    path = s.path.rstrip("/") or "/"
    kept = [(k, v) for k, v in parse_qsl(s.query) if k.lower() not in _TRACKING]
    return urlunsplit(("https", host, path, urlencode(kept), ""))


def stable_id(c: Candidate) -> str:
    basis = f"{c.source}|{_canonical_url(c.source_page_url)}|{c.angle_key or '0'}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]
