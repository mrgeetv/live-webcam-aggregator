from __future__ import annotations

import logging
import re
from typing import Any, override

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
