from __future__ import annotations

import logging
import re
from typing import Any, override

# googleapiclient puts the developer key in the request URI, and HttpError.__str__ (which
# IS its __repr__) prints that URI — so logging such an exception writes the key to disk.
# That already happened once, on 2026-07-14, via app.py's live_ids handler. The call
# sites are fixed individually; this filter is the backstop so a future one can't regress
# it. Unlike the CDN playback tokens in serving.py, this is OUR long-lived credential —
# it is redacted at every level, including DEBUG.
_KEY_PARAM = re.compile(r"(?i)((?:developer_?key|api_?key|key)=)[^&\s\"']+")


def scrub(text: str) -> str:
    """Replace credential query-param values with REDACTED, keeping the rest intact."""
    return _KEY_PARAM.sub(r"\1REDACTED", text)


def _scrub_arg(value: Any) -> Any:
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
