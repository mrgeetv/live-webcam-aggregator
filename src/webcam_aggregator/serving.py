from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from urllib.parse import quote, urljoin, urlsplit

from .cache import ResolveCache
from .logging_redaction import trim_url
from .models import CatalogueEntry
from .signing import sign, verify

log = logging.getLogger("webcam-aggregator.serving")

_HLS_CT = "application/vnd.apple.mpegurl"


class RepeatLimiter:
    """Admits a serve-path log line once per `window` seconds per key. The serve
    path logs per player request, and a failing cam repeats itself: an IP-bound 403
    hits every segment, every few seconds, for every viewer, and a restart turns
    every URL a player holds into a bad signature.

    Keys can carry an id taken from the request path, which a client can make up, so
    past `cap` keys the expired ones go, and if that isn't enough, all of them."""

    _clock: Callable[[], float]
    _window: float
    _cap: int
    _seen: dict[tuple[str, ...], tuple[float, int]]  # key -> (logged at, suppressed)
    _lock: threading.Lock

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        window: float = 60.0,
        cap: int = 4096,
    ) -> None:
        self._clock = clock
        self._window = window
        self._cap = cap
        self._seen = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._seen)

    def admit(self, key: tuple[str, ...]) -> int | None:
        """None to suppress this line, else how many repeats of it were suppressed
        since it last logged."""
        now = self._clock()
        with self._lock:
            seen = self._seen.get(key)
            if seen is not None and now - seen[0] < self._window:
                self._seen[key] = (seen[0], seen[1] + 1)
                return None
            self._seen[key] = (now, 0)
            if len(self._seen) > self._cap:
                self._seen = {
                    k: v for k, v in self._seen.items() if now - v[0] < self._window
                }
                if len(self._seen) > self._cap:
                    self._seen = {key: (now, 0)}
            return 0 if seen is None else seen[1]


repeats = RepeatLimiter()


# The ids we issue (models.stable_id). /m and /s take the id from the request path
# unchecked, since the signature covers only the upstream URL, so a log line echoes
# an id only in this shape: anything else could be 60 KB long or carry terminal
# escapes.
_STREAM_ID = re.compile(r"[0-9a-f]{16}")


def _shown(entry_id: str) -> str:
    return entry_id if _STREAM_ID.fullmatch(entry_id) else "<malformed id>"


def _log(level: int, stream: str, reason: str, msg: str, *args: object) -> None:
    """Log a serve-path line unless the same line for the same stream and reason
    logged within the last minute (`RepeatLimiter`); the next one that logs says
    how many came since. A line about a request that proved nothing (a bad
    signature, an unknown id) passes stream "", so one line a minute covers every
    id a client can make up."""
    suppressed = repeats.admit((msg, stream, reason))
    if suppressed is None:
        return
    if suppressed:
        msg += " (+%d repeat%s since the last line)"
        args = (*args, suppressed, "" if suppressed == 1 else "s")
    log.log(level, msg, *args)


def is_playable_manifest(text: str | None) -> bool:
    """True only for an HLS playlist that actually points at something: a media
    playlist carrying segments, or a master playlist carrying variants.

    The `#EXTM3U` header alone is NOT enough, and testing only for it is how a dead
    cam gets listed. A CDN handed an expired or bogus token typically answers
    **HTTP 200 with a well-formed but empty playlist** rather than a 404 — skyline
    returns exactly that for a lapsed token (`#EXT-X-ENDLIST`, zero segments), so a
    header-only check reads it as live, ships it in the playlist, then serves the
    player something it cannot start. This is the same shape as the rtsp.me stub
    that is currently dodged by skipping that host by URL; deciding on content
    catches the whole class instead of one domain at a time.

    Accepting either tag matters: requiring segments alone would reject every
    master playlist (variants, no `#EXTINF`), which is most CDN top-level URLs."""
    if not text or "#EXTM3U" not in text:
        return False
    return "#EXTINF" in text or "#EXT-X-STREAM-INF" in text


def first_variant(master: str, url: str) -> str | None:
    """The absolute URL of a master playlist's first variant (the URI line after its
    first `#EXT-X-STREAM-INF`), or None if it names none. The first listed variant is
    where a player starts."""
    lines = iter(master.splitlines())
    for ln in lines:
        if ln.startswith("#EXT-X-STREAM-INF"):
            for raw in lines:
                ref = raw.strip()
                if ref and not ref.startswith("#"):
                    return urljoin(url, ref)
            return None
    return None


def loggable(url: str) -> str:
    """An upstream URL trimmed for an INFO log line (`logging_redaction.trim_url`:
    no credentials, but the params that name the cam). These warnings land in a
    log kept for weeks.

    At DEBUG the full URL is kept: when you are actually debugging one stream, the
    token is the point, and DEBUG is opt-in and short-lived."""
    if log.isEnabledFor(logging.DEBUG):
        return url
    return trim_url(url)


_NONCOMMENT = re.compile(r"^(?!#)(\S+)\s*$", re.MULTILINE)
# Tags that carry a reference in an attribute instead of on the next line. These are
# every attribute RFC 8216bis defines as a possibly-relative reference: URI (an fMP4
# init segment on #EXT-X-MAP, a key, an alternate rendition on #EXT-X-MEDIA — Wowza's
# separate audio — an I-frame playlist, session data, and LL-HLS parts, preload hints
# and rendition reports), content steering's SERVER-URI, an interstitial's
# X-ASSET-LIST and a preload date range's X-URI. (An interstitial's X-ASSET-URI is
# absolute by definition.) Left relative, the player resolves them against
# /stream/<id> and 404s.
_URI_ATTRIBUTES = frozenset({"URI", "SERVER-URI", "X-ASSET-LIST", "X-URI"})
# Reads an #EXT-X- tag's attribute list (never an #EXTINF title) one attribute at a
# time, from the start of the list or a comma. A quoted value is consumed whole, so a
# `,URI="` inside an earlier quoted value is never mistaken for an attribute. Never
# past the end of the line.
_TAG_LINE = re.compile(r"^(#EXT-X-[A-Z0-9-]+:)(.*)$", re.MULTILINE)
_ATTRIBUTE = re.compile(r'(?<![^,])([A-Z0-9-]+)=("[^"]*"|[^,]*)')

# Hosts whose HLS sessions are bound to the IP that fetches the manifest. Proxying
# the manifest (our IP) while the player fetches segments direct (its IP) makes the
# upstream 403 the segments. For these we hand the player the original URL and let
# it fetch the whole chain itself, so manifest + segments share one IP/session.
_DIRECT_PLAYBACK_HOSTS = ("pixelcaster.com",)


def _is_direct_playback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in _DIRECT_PLAYBACK_HOSTS)


# Hosts whose segments fail a direct player fetch, so we relay the bytes too:
# balticlivecam's token is IP-bound to OUR fetch; enhd.es 403s when the player
# re-encodes the literal "+" in its stream path to %2B; iol.pt (beachcam) and
# ozolio mint a per-manifest-fetch Nimble/Wowza session that 403s any other
# client (the wetmet shape); hdontap's t/e token may be bound to the fetcher;
# explore's MediaMTX edges mint a `session=` per manifest fetch that is checked
# against the client IP.
# Fetching the segment server-side and handing the player a clean /s URL
# sidesteps all of these.
_PROXY_SEGMENT_HOSTS = (
    "balticlivecam.com",
    "enhd.es",
    "skylinewebcams.com",
    "earthcam.com",
    "wetmet.net",
    "video-auth1.iol.pt",
    # the STREAM hosts, not hdontap.com itself: that apex is the cam-page website whose
    # JSON the resolver reads, so relaying it would let a tampered page make us sign and
    # relay arbitrary hdontap.com bytes. Manifests are on live., edges on *.nginx.
    "live.hdontap.com",
    "nginx.hdontap.com",
    "ozolio.com",
    "webcamera.pl",
    # explore's MediaMTX stream edges only, not explore.org's website/API hosts
    "gcp.prod.explore.org",
)


def _proxy_segments_for(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in _PROXY_SEGMENT_HOSTS)


def _is_youtube(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "googlevideo.com" or host.endswith(".googlevideo.com")


def _redirects_youtube(url: str, proxy_youtube: bool) -> bool:
    return _is_youtube(url) and not proxy_youtube


def _registrable_domain(host: str) -> str:
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _same_site(a: str, b: str) -> bool:
    """True if two hosts share a registrable domain (last two labels). Heuristic —
    treats 2-part TLDs (e.g. co.uk) as the registrable domain, acceptable here as it
    only ever widens 'is this the upstream's own CDN' very slightly."""
    return bool(a) and bool(b) and _registrable_domain(a) == _registrable_domain(b)


# A response: status code, content type (or a redirect's location), body.
Response = tuple[int, str, bytes]

# A segment response: status code, content type, Content-Range if any, body.
SegmentResponse = tuple[int, str, str | None, bytes]


# Titles and categories are scraped text. A line break in one would start a new
# playlist line (a different stream URL, say), and a double quote would end its
# attribute early, so both are neutralised before they reach #EXTINF. Control
# characters go too (str.split() alone keeps NUL and friends).
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")


def _m3u_text(value: str) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", value).replace('"', "'").split())


def render_playlist(entries: list[CatalogueEntry], *, base_url: str) -> str:
    lines = ["#EXTM3U"]
    for e in entries:
        title, category = _m3u_text(e.title), _m3u_text(e.category)
        # tvg-id is the STABLE per-cam id (same value as the /stream/<id> URL). It
        # gives players (TiViMate) a fixed key to keep favourites linked to the right
        # stream across catalogue rebuilds, independent of count, order, or title.
        lines.append(
            f'#EXTINF:-1 tvg-id="{e.id}" tvg-name="{title}" '
            f'group-title="{category}",{title}'
        )
        lines.append(f"{base_url}/stream/{e.id}")
    return "\n".join(lines) + "\n"


def rewrite_manifest(
    text: str,
    *,
    upstream_url: str,
    entry_id: str,
    base_url: str,
    proxy_segments: bool = False,
) -> str:
    up_host = urlsplit(upstream_url).hostname or ""

    def rewrite(ref: str) -> str:
        absolute = urljoin(upstream_url, ref)
        # Open-proxy guard: an upstream manifest is attacker-influenceable, so only
        # sign/relay refs on the upstream's OWN site. An off-site ref is passed
        # through as-is (the player fetches it direct) — never proxied through us.
        if not _same_site(urlsplit(absolute).hostname or "", up_host):
            return absolute
        if absolute.split("?", 1)[0].endswith(".m3u8"):
            return f"{base_url}/stream/{entry_id}/m?u={quote(absolute, safe='')}&sig={sign(absolute)}"
        if proxy_segments:
            return f"{base_url}/stream/{entry_id}/s?u={quote(absolute, safe='')}&sig={sign(absolute)}"
        return absolute

    def rewrite_attribute(m: re.Match[str]) -> str:
        name, value = m.group(1), m.group(2)
        ref = value[1:-1]
        if name not in _URI_ATTRIBUTES or not ref or value != f'"{ref}"':
            return m.group(0)
        return f'{name}="{rewrite(ref)}"'

    def rewrite_tag(m: re.Match[str]) -> str:
        return m.group(1) + _ATTRIBUTE.sub(rewrite_attribute, m.group(2))

    text = _NONCOMMENT.sub(lambda m: rewrite(m.group(1)), text)
    return _TAG_LINE.sub(rewrite_tag, text)


# DVR live streams (YouTube, hdontap's Wowza edges) can hand us the entire rewind
# buffer (thousands of segments, up to several MB, re-fetched every few seconds per
# viewer). We don't support rewind on a live wall, so trim long media playlists to the
# live edge: keep ~the last _LIVE_WINDOW_SECONDS, fixing up the sequence tags. Master
# playlists and normal-length live playlists pass through untouched.
_LIVE_WINDOW_SECONDS = 120.0
_TRUNCATE_ABOVE = 200  # only DVR-sized playlists are trimmed
_MIN_KEEP = 6
# Tags that describe the whole playlist (RFC 8216bis), wherever they sit. Every other
# tag belongs to the segment after it — #EXT-X-KEY and #EXT-X-MAP included, since
# either can change mid-playlist.
_PLAYLIST_TAGS = frozenset(
    {
        "#EXTM3U",
        "#EXT-X-VERSION",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        "#EXT-X-DEFINE",
        "#EXT-X-TARGETDURATION",
        "#EXT-X-MEDIA-SEQUENCE",
        "#EXT-X-DISCONTINUITY-SEQUENCE",
        "#EXT-X-PLAYLIST-TYPE",
        "#EXT-X-ALLOW-CACHE",
        "#EXT-X-START",
        "#EXT-X-SERVER-CONTROL",
        "#EXT-X-PART-INF",
        "#EXT-X-I-FRAMES-ONLY",
    }
)
_SEGMENT_STATE_TAGS = frozenset({"#EXT-X-KEY", "#EXT-X-MAP"})


def _header_int(header: list[str], tag: str, default: int) -> int:
    for ln in header:
        if ln.startswith(tag + ":"):
            try:
                return int(ln.split(":", 1)[1].strip())
            except ValueError:
                return default
    return default


def _extinf_dur(tags: list[str]) -> float:
    for t in tags:
        if t.startswith("#EXTINF:"):
            try:
                return float(t.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                return 0.0
    return 0.0


def truncate_to_live_edge(text: str, window: float = _LIVE_WINDOW_SECONDS) -> str:
    """Trim a long DVR media playlist to ~`window` seconds at the live edge, adjusting
    MEDIA-SEQUENCE / DISCONTINUITY-SEQUENCE and carrying the key and init section in
    force at the cut. Master playlists and normal-length media playlists are returned
    unchanged (verbatim)."""
    if "#EXT-X-STREAM-INF" in text:  # master playlist — no media segments here
        return text
    header: list[str] = []
    segments: list[tuple[list[str], str]] = []  # (preceding tag lines, url line)
    pending: list[str] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if ln.startswith("#"):
            if ln.split(":", 1)[0] in _PLAYLIST_TAGS:
                header.append(ln)
            else:
                pending.append(ln)
        else:
            segments.append((pending, ln))
            pending = []
    if len(segments) <= _TRUNCATE_ABOVE:
        return text  # not a DVR back-catalogue — leave it alone
    kept: list[tuple[list[str], str]] = []
    acc = 0.0
    for seg in reversed(segments):
        kept.append(seg)
        acc += _extinf_dur(seg[0])
        if acc >= window and len(kept) >= _MIN_KEEP:
            break
    kept.reverse()
    if len(kept) >= len(segments):
        return text
    dropped = segments[: len(segments) - len(kept)]
    media_seq = _header_int(header, "#EXT-X-MEDIA-SEQUENCE", 0) + len(dropped)
    disc_dropped = sum(
        1 for tags, _ in dropped for t in tags if t == "#EXT-X-DISCONTINUITY"
    )
    disc_seq = _header_int(header, "#EXT-X-DISCONTINUITY-SEQUENCE", 0) + disc_dropped
    out: list[str] = []
    saw_media = saw_disc = False
    for h in header:
        if h.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            out.append(f"#EXT-X-MEDIA-SEQUENCE:{media_seq}")
            saw_media = True
        elif h.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            out.append(f"#EXT-X-DISCONTINUITY-SEQUENCE:{disc_seq}")
            saw_disc = True
        else:
            out.append(h)
    if not saw_media:
        out.append(f"#EXT-X-MEDIA-SEQUENCE:{media_seq}")
    if not saw_disc and disc_seq:
        out.append(f"#EXT-X-DISCONTINUITY-SEQUENCE:{disc_seq}")
    # A key or init section governs every segment after it until replaced, so the
    # dropped ones are replayed in order: with no segments between them, each lands in
    # the state upstream had at the cut — every KEYFORMAT's key, and the key in force
    # at each MAP (which is the one that encrypted its init section).
    out.extend(
        t
        for tags, _ in dropped
        for t in tags
        if t.split(":", 1)[0] in _SEGMENT_STATE_TAGS
    )
    for tags, url in kept:
        out.extend(tags)
        out.append(url)
    out.extend(pending)  # tags after the last segment (ENDLIST, LL-HLS hints)
    return "\n".join(out) + "\n"


def _safe_redirect_target(url: str) -> bool:
    """A resolved URL is emitted verbatim in a 302 `Location` header, so it must be a
    plain http(s) URL with no control characters. An extractor reads its URL from a
    scraped page (e.g. a feratel/og:video meta), and `send_header` does NO CRLF
    sanitisation — a newline in that value would split the response (header injection
    on our own origin), and a non-http scheme would be an open redirect."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    return urlsplit(url).scheme in ("http", "https")


def serve_stream(
    entry_id: str,
    *,
    catalogue: Mapping[str, CatalogueEntry],
    cache: ResolveCache,
    fetch: Callable[[str], tuple[str | None, str]],
    base_url: str,
    proxy_youtube: bool = False,
) -> Response:
    """Answer a player's `/stream/<id>`. `fetch` returns a body (None on failure)
    and its outcome label, as `Fetcher.get_with_outcome` does."""
    sid = _shown(entry_id)
    entry = catalogue.get(entry_id)
    if entry is None:
        # a player holding a playlist older than the catalogue (a cam since dropped)
        _log(logging.INFO, "", "", "unknown stream: %s", sid)
        return (404, "text/plain", b"unknown stream")
    target = entry.target_url
    lookup = cache.get(entry_id, target)
    resolved = lookup.resolved
    if (
        resolved is not None
        and _redirects_youtube(resolved.url, proxy_youtube)
        and cache.record_redirect(entry_id)
    ):
        # googlevideo can stop serving a cached URL's segments hours before its
        # token expires, while a fresh resolve of the same broadcast plays.
        log.info("player retry, re-resolving: %s", entry_id)
        lookup = cache.get(entry_id, target, fresh=True)
        resolved = lookup.resolved
        if resolved is not None and lookup.error is not None:
            _log(
                logging.WARNING,
                sid,
                lookup.error,
                "re-resolve failed, keeping the cached URL: %s -> %s: %s",
                sid,
                loggable(target),
                lookup.error,
            )
    if resolved is None:
        _log(
            logging.WARNING,
            sid,
            str(lookup.error),
            "resolve failed: %s -> %s: %s",
            sid,
            loggable(target),
            lookup.error,
        )
        return (502, "text/plain", b"resolve failed")
    if not _safe_redirect_target(resolved.url):
        # a scraped og:video / iframe src that isn't a clean http(s) URL — reject
        # rather than reflect it into a redirect (see _safe_redirect_target)
        _log(
            logging.WARNING,
            sid,
            "",
            "unsafe resolved URL: %s -> %s",
            sid,
            loggable(resolved.url),
        )
        return (502, "text/plain", b"unsafe upstream URL")
    if resolved.stream_type == "mp4":
        return (302, resolved.url, b"")  # redirect; 2nd field is the Location
    if _is_direct_playback(resolved.url):
        # IP-bound session: let the player fetch the whole chain itself (no proxy)
        return (302, resolved.url, b"")
    if _redirects_youtube(resolved.url, proxy_youtube):
        # Default: hand the player the googlevideo manifest directly — lower latency
        # and less buffering on shallow live windows. Trade-off: playback stops when
        # the ~6h googlevideo token expires (re-select to resume). PROXY_YOUTUBE=true
        # proxies instead — survives expiry via re-resolve, DVR trimmed to live edge.
        return (302, resolved.url, b"")
    return _proxy_manifest(
        entry_id, resolved.url, fetch=fetch, base_url=base_url, kind="manifest"
    )


def serve_child_manifest(
    entry_id: str,
    upstream_url: str,
    sig: str,
    *,
    fetch: Callable[[str], tuple[str | None, str]],
    base_url: str,
) -> Response:
    sid = _shown(entry_id)
    if not verify(upstream_url, sig):
        # the signing key is per-process: after a restart, a player still holding
        # the old URLs lands here until it reloads /stream/<id>
        _log(logging.WARNING, "", "", "bad signature on a child manifest: %s", sid)
        return (403, "text/plain", b"bad signature")
    return _proxy_manifest(
        entry_id, upstream_url, fetch=fetch, base_url=base_url, kind="child manifest"
    )


def _proxy_manifest(
    entry_id: str,
    upstream_url: str,
    *,
    fetch: Callable[[str], tuple[str | None, str]],
    base_url: str,
    kind: str,
) -> Response:
    """Fetch an upstream playlist and relay it trimmed and rewritten through us, or
    refuse it with a 502. `kind` names the playlist in the log."""
    sid = _shown(entry_id)
    manifest, outcome = fetch(upstream_url)
    if manifest is None:
        _log(
            logging.WARNING,
            sid,
            f"{kind} {outcome}",
            "%s fetch failed (%s): %s -> %s",
            kind,
            outcome,
            sid,
            loggable(upstream_url),
        )
        return (502, "text/plain", b"upstream manifest fetch failed")
    if not is_playable_manifest(manifest):
        # Not HLS (a DASH .mpd, an error page), or HLS with nothing in it: the empty
        # playlist a CDN returns for an expired token, or a variant dead behind a
        # master that still answers 200. Refusing beats handing the player a
        # playlist it cannot start.
        why = "no segments or variants" if "#EXTM3U" in manifest else "not HLS"
        _log(
            logging.WARNING,
            sid,
            f"{kind} {why}",
            "unplayable %s (%s): %s -> %s",
            kind,
            why,
            sid,
            loggable(upstream_url),
        )
        return (502, "text/plain", b"not a playable HLS stream")
    manifest = truncate_to_live_edge(manifest)
    body = rewrite_manifest(
        manifest,
        upstream_url=upstream_url,
        entry_id=entry_id,
        base_url=base_url,
        proxy_segments=_proxy_segments_for(upstream_url),
    )
    return (200, _HLS_CT, body.encode())


def serve_segment(
    entry_id: str,
    upstream_url: str,
    sig: str,
    *,
    fetch_segment: Callable[[str, str | None], tuple[SegmentResponse | None, str]],
    range_header: str | None = None,
) -> SegmentResponse:
    """Relay one segment. `fetch_segment` returns the upstream response (None on
    failure) and its outcome label, as `Fetcher.get_segment` does."""
    sid = _shown(entry_id)
    if not verify(upstream_url, sig):
        # see child manifest
        _log(logging.WARNING, "", "", "bad signature on a segment: %s", sid)
        return (403, "text/plain", None, b"bad signature")
    result, outcome = fetch_segment(upstream_url, range_header)
    if result is None:
        _log(
            logging.WARNING,
            sid,
            outcome,
            "segment fetch failed (%s): %s -> %s",
            outcome,
            sid,
            loggable(upstream_url),
        )
        return (502, "text/plain", None, b"segment fetch failed")
    if result[0] >= 400:
        # relayed as-is, so the player sees it too; an IP-bound or lapsed token
        # usually shows up here, as a 401/403/404 on every segment
        _log(
            logging.WARNING,
            sid,
            str(result[0]),
            "segment upstream http-%d: %s -> %s",
            result[0],
            sid,
            loggable(upstream_url),
        )
    return result
