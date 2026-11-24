"""HMAC signing of proxied manifest and segment URLs; the key is per-process."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_KEY = secrets.token_bytes(32)  # per-process; signed URLs don't survive a restart


def sign(value: str) -> str:
    """Return the signature for `value`."""
    return hmac.new(_KEY, value.encode(), hashlib.sha256).hexdigest()[:32]


def verify(value: str, sig: str) -> bool:
    """Return whether `sig` is the signature for `value`."""
    # as bytes: compare_digest refuses a str holding non-ASCII, and `sig` is
    # whatever the request carried
    return hmac.compare_digest(sign(value).encode(), sig.encode())
