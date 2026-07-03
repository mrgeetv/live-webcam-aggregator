from __future__ import annotations

import hashlib
import hmac
import secrets

_KEY = secrets.token_bytes(32)  # per-process; signed URLs don't survive a restart


def sign(value: str) -> str:
    return hmac.new(_KEY, value.encode(), hashlib.sha256).hexdigest()[:32]


def verify(value: str, sig: str) -> bool:
    return hmac.compare_digest(sign(value), sig)
