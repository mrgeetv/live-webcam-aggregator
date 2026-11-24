#!/usr/bin/env python3
"""Fail when hadolint's pinned image tag in .pre-commit-config.yaml isn't its `rev:`.

The hook's `entry:` pins the image so CI and a local run use the same hadolint, but
Dependabot only bumps `rev:`, so a hadolint bump needs the tag moved by hand too.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = "https://github.com/hadolint/hadolint"
_IMAGE = "ghcr.io/hadolint/hadolint:"


def mismatch(config: str) -> str | None:
    """Why hadolint's entry tag and rev disagree, or None if they agree."""
    rev = tag = None
    in_block = False
    for line in config.splitlines():
        words = line.split()
        if words[:2] == ["-", "repo:"]:
            in_block = words[2:3] == [_REPO]
        elif in_block and words[:1] == ["rev:"] and len(words) > 1:
            rev = words[1]
        elif (
            in_block
            and words[:1] == ["entry:"]
            and len(words) > 1
            and words[1].startswith(_IMAGE)
        ):
            tag = words[1].removeprefix(_IMAGE)
    if rev is None or tag is None:
        return f"no {_REPO} hook with a pinned {_IMAGE}<tag> entry found"
    if rev != tag:
        return f"hadolint rev is {rev} but its entry pins {_IMAGE}{tag}: set the entry tag to {rev}"
    return None


def main(paths: list[str]) -> int:
    for path in paths or [".pre-commit-config.yaml"]:
        problem = mismatch(Path(path).read_text(encoding="utf-8"))
        if problem:
            print(f"{path}: {problem}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
