from __future__ import annotations

import logging
import re
from typing import override
from urllib.parse import parse_qsl, urlencode, urlsplit

# googleapiclient puts the developer key in the request URI, and HttpError.__str__ (which
# IS its __repr__) prints that URI — so ANY log of such an exception writes the key to
# disk. log.exception is the easiest way to do it by accident, since a traceback ends in
# str(exc). Call sites are responsible for scrubbing; this filter is the backstop that
# stops a new one regressing it. Unlike the CDN playback tokens in serving.py, this is
# our own long-lived credential, so it is redacted at every level, DEBUG included.
# The lookbehind stops words that merely END in "key" (monkey=, turkey=) being eaten,
# while an underscore still counts as a boundary so session_key=/auth_key= stay covered.
_KEY_PARAM = re.compile(
    r"(?i)(?<![a-z0-9])((?:developer_?key|api_?key|key)=)[^&\s\"']+"
)


def scrub(text: str) -> str:
    """Replace credential query-param values with REDACTED, keeping the rest intact."""
    return _KEY_PARAM.sub(r"\1REDACTED", text)


# Query params that name a cam rather than authorise a fetch, each kept only on the
# domain where it does (elsewhere a `uid=` could be a CDN session). For these targets
# the query IS the cam's identity, so a line without it can't say which cam failed.
_ID_PARAMS = {
    "v": "youtube.com",
    "cam": "feratel.com",  # webtv.feratel.com/webtv/?cam=<id>
    "alias": "ipcamlive.com",  # player.php?alias=<alias>
    "uid": "wetmet.net",  # widgets/stream/frame.php?uid=<id>
    "uuid": "joada.net",  # viewsurf's embeded.html?uuid=<id>
}


def trim_url(url: str, *, keep_ids: bool = True) -> str:
    """A URL cut for an INFO log line to scheme://host/path, plus (with `keep_ids`)
    any query params that only identify the cam (`_ID_PARAMS`).

    Upstream URLs carry live session credentials in the query string (skyline's
    `?a=<token>`, a beachcam target's `wmsAuthSign`), googlevideo carries its
    playback token in the PATH (`_googlevideo_path`), and any URL can carry
    userinfo, so all of that goes."""
    try:
        parts = urlsplit(url)
        name = (parts.hostname or "").rstrip(".")  # `host.` is the same host
    except ValueError:
        return "<malformed url>"
    host = parts.netloc.rpartition("@")[2]
    path = parts.path
    if name.endswith(".googlevideo.com"):
        path = _googlevideo_path(path)
    ids = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if keep_ids
        and k in _ID_PARAMS
        and (name == _ID_PARAMS[k] or name.endswith("." + _ID_PARAMS[k]))
    ]
    query = f"?{urlencode(ids)}" if ids else ""
    return f"{parts.scheme}://{host}{path}{query}"


def _googlevideo_path(path: str) -> str:
    """A googlevideo path cut to its route and video id. After the route the path
    is key/value pairs carrying the playback credentials (`ip/<our egress IP>`,
    `sig/…`), so of those only `id/<video>.<n>` is kept, whatever their order. A
    route other than a manifest's or `videoplayback` keeps nothing but the id."""
    segs = path.split("/")
    if segs[1:3] == ["api", "manifest"]:
        kept = segs[:4]  # /api/manifest/<kind>
    elif segs[1:2] == ["videoplayback"]:
        kept = segs[:2]
    else:
        kept = [""]
    if "id" in segs[len(kept) :]:
        i = segs.index("id", len(kept))
        kept += segs[i : i + 2]
    return "/".join(kept) or "/"


# An extractor error often quotes its target URL, token and all.
_URL = re.compile(r"https?://[^\s\"']+")


def trim_urls(text: str, *, keep_ids: bool = True) -> str:
    """`text` with every URL it quotes cut by `trim_url`."""
    return _URL.sub(lambda m: trim_url(m.group(), keep_ids=keep_ids), text)


def describe_error(exc: BaseException) -> str:
    """`Type: message` for one INFO log line: credentials scrubbed, every URL the
    message quotes trimmed (`trim_url`), and line breaks flattened."""
    text = " ".join(trim_urls(scrub(str(exc))).splitlines())
    return f"{type(exc).__name__}: {text}"


def _scrub_arg(value: object) -> object:
    """Pre-scrub a log arg's string form. Non-str args are %s-formatted later, and the
    usual leak shape is `log.warning("…: %s", exc)` where the exception's str carries the
    key — so the arg has to be scrubbed, not just the format string. Clean values are
    returned unchanged so formatting (%d and friends) still works."""
    if isinstance(value, str):
        return scrub(value)
    text = str(value)
    cleaned = scrub(text)
    return cleaned if cleaned != text else value


class RedactingFilter(logging.Filter):
    """Strip credential query params from a record's message and its args.

    Attach to the root HANDLER, not to a logger: a logger's filters only see records
    logged directly on it, and every module here uses a child logger
    (webcam-aggregator.catalogue, .fetch, .sources.youtube, …).
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_scrub_arg(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _scrub_arg(v) for k, v in args.items()}
        return True
