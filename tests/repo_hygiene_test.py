"""Guards on what ships in a public repo.

Source comments should explain the code, not narrate the operator's incidents. A date
or a named piece of someone's infrastructure in a comment is a strong signal that a
private debugging session leaked into a public artefact — and it dates the code for no
benefit, since the reason a fix exists outlives the day it was written.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SHIPPED = ("src", "docker-compose.yml", "Dockerfile", ".env.example")

# ISO dates (2026-08-04) and bare month stamps (2026-08) in prose.
_DATE = re.compile(r"\b20\d{2}-\d{2}(?:-\d{2})?\b")
# Named consumer infrastructure/services that belong to whoever runs this, not to it.
_PRIVATE_INFRA = re.compile(
    r"\b(protonvpn|nordvpn|mullvad|tailscale|wireguard|uptime[- ]kuma)\b", re.I
)


def _shipped_files() -> list[Path]:
    out: list[Path] = []
    for entry in _SHIPPED:
        p = _ROOT / entry
        if p.is_dir():
            out.extend(sorted(p.rglob("*.py")))
        elif p.is_file():
            out.append(p)
    return out


def test_no_dates_in_shipped_source() -> None:
    """A date in a comment means "this is when it happened to me", which is of no use
    to a reader and pins the code to a moment. State the hazard instead."""
    offenders = [
        f"{p.relative_to(_ROOT)}:{n}: {ln.strip()}"
        for p in _shipped_files()
        for n, ln in enumerate(p.read_text("utf-8").splitlines(), 1)
        if _DATE.search(ln)
    ]
    assert not offenders, "dated references in shipped source:\n" + "\n".join(offenders)


def test_no_private_infrastructure_named_in_shipped_source() -> None:
    offenders = [
        f"{p.relative_to(_ROOT)}:{n}: {ln.strip()}"
        for p in _shipped_files()
        for n, ln in enumerate(p.read_text("utf-8").splitlines(), 1)
        if _PRIVATE_INFRA.search(ln)
    ]
    assert not offenders, "operator infrastructure named in source:\n" + "\n".join(
        offenders
    )


def test_no_api_key_shaped_literals_in_the_repo() -> None:
    """A real Google API key is AIza + 35 chars. Test fixtures deliberately break the
    shape (an underscore-laden fake), so anything matching the real pattern is a leak.
    """
    key_like = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")
    offenders: list[str] = []
    for p in [*_shipped_files(), *sorted((_ROOT / "tests").rglob("*.py"))]:
        for n, ln in enumerate(p.read_text("utf-8").splitlines(), 1):
            if key_like.search(ln):
                offenders.append(f"{p.relative_to(_ROOT)}:{n}")
    assert not offenders, f"possible API key committed: {offenders}"
