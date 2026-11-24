#!/usr/bin/env python3
"""Base-image bookkeeping for the published image.

docker-build.yml stamps every image with LABEL, its bases' digests at build time;
base-images.yml rebuilds the latest release when that label (or the image's revision)
no longer matches, and flags newer python/alpine tags. The parsing is pure and tested
offline; the `label` and `stale` commands query registries through `docker buildx`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

LABEL = "io.github.mrgeetv.base-images"

# <python major.minor>-alpine<alpine major.minor>[-variant], e.g. 3.14-alpine3.24-sfw-dev.
# A patch-level python tag (3.14.1-…) or a prerelease (3.15-rc-…) doesn't match.
_PYTHON_ALPINE_TAG = re.compile(r"(\d+)\.(\d+)-alpine(\d+)\.(\d+)(-[a-z-]+)?")


class _PythonAlpine(NamedTuple):
    python: tuple[int, int]
    alpine: tuple[int, int]
    variant: str


def _python_alpine(tag: str) -> _PythonAlpine | None:
    m = _PYTHON_ALPINE_TAG.fullmatch(tag)
    if not m:
        return None
    py_major, py_minor, a_major, a_minor = (int(g) for g in m.group(1, 2, 3, 4))
    return _PythonAlpine((py_major, py_minor), (a_major, a_minor), m.group(5) or "")


def newer_tags(current: str, available: list[str]) -> list[str]:
    """Return the newer tags of current's variant and python major, oldest first.

    Newer means python and alpine are both at least current's.

    A newer python on an older alpine doesn't count (it would downgrade the OS), nor
    does a new python major (a rewrite, not a bump).
    """
    now = _python_alpine(current)
    if now is None:
        msg = f"not a <python>-alpine<version> tag: {current}"
        raise ValueError(msg)
    newer: list[tuple[_PythonAlpine, str]] = []
    for tag in available:
        cand = _python_alpine(tag)
        if (
            cand is not None
            and cand.variant == now.variant
            and cand.python[0] == now.python[0]
            and cand.python >= now.python
            and cand.alpine >= now.alpine
            and cand != now
        ):
            newer.append((cand, tag))
    return [tag for _, tag in sorted(newer)]


def base_images(dockerfile: str) -> list[str]:
    """Every external image the Dockerfile builds FROM, in order of first use.

    A FROM naming an ARG resolves to that ARG's default; a FROM naming an earlier
    stage is internal and skipped.
    """
    args: dict[str, str] = {}
    stages: set[str] = set()
    images: list[str] = []
    for line in dockerfile.splitlines():
        words = line.split()
        if not words:
            continue
        keyword = words[0].upper()
        if keyword == "ARG" and len(words) > 1 and "=" in words[1]:
            name, _, default = words[1].partition("=")
            args[name] = default
        elif keyword == "FROM":
            operands = [w for w in words[1:] if not w.startswith("--")]
            if not operands:
                continue
            image = operands[0]
            if image.startswith("$"):
                image = args.get(image.lstrip("$").strip("{}"), "")
            if image and image not in stages and image not in images:
                images.append(image)
            match operands:
                case [_, as_keyword, stage] if as_keyword.upper() == "AS":
                    stages.add(stage)
                case _:
                    pass
    return images


def _inspect(ref: str, field: str) -> dict[str, object]:
    docker = shutil.which("docker")
    if docker is None:
        msg = "docker is not on PATH"
        raise RuntimeError(msg)
    # No shell: the image reference is one argv entry, read from our own Dockerfile.
    out = subprocess.run(  # noqa: S603
        [
            docker,
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            f"{{{{json .{field}}}}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def _pinned_bases(dockerfile_path: str) -> str:
    """LABEL's value: `<image>@<digest>` per base, space-separated, Dockerfile order."""
    images = base_images(Path(dockerfile_path).read_text(encoding="utf-8"))
    if not images:
        msg = f"no base images found in {dockerfile_path}"
        raise ValueError(msg)
    return " ".join(
        f"{image}@{_inspect(image, 'Manifest')['digest']}" for image in images
    )


def _published_labels(image: str) -> dict[str, str]:
    data = _inspect(image, "Image")
    # A multi-platform image maps platform -> config; a single-platform one is the config.
    config = data.get("linux/amd64", data)
    if not isinstance(config, dict):
        msg = f"unexpected image config for {image}: {type(config).__name__}"
        raise TypeError(msg)
    return config.get("config", {}).get("Labels") or {}


def stale_reason(dockerfile_path: str, image: str, commit: str) -> str:
    """Why `image` needs rebuilding from `commit`, or "" if it doesn't."""
    labels = _published_labels(image)
    revision = labels.get("org.opencontainers.image.revision", "")
    if revision != commit:
        return f"{image} was built from {revision or '<no revision>'}, not the release's {commit}"
    published, current = labels.get(LABEL, ""), _pinned_bases(dockerfile_path)
    if published != current:
        return f"base images changed\n  published: {published or '<no label>'}\n  current:   {current}"
    return ""


def main(argv: list[str]) -> int:
    """Run the subcommand named in `argv`.

    Usage:
    images <Dockerfile>              its external base images, one per line
    newer-tags <tag>                 the newer tags among those read from stdin
    label <Dockerfile>               the LABEL=<value> line to stamp on a build
    stale <Dockerfile> <image> <sha> why <image> needs a rebuild ("" if it doesn't)
    """
    match argv:
        case ["images", path]:
            print("\n".join(base_images(Path(path).read_text(encoding="utf-8"))))
        case ["newer-tags", current]:
            print("\n".join(newer_tags(current, sys.stdin.read().split())))
        case ["label", path]:
            print(f"{LABEL}={_pinned_bases(path)}")
        case ["stale", path, image, commit]:
            print(stale_reason(path, image, commit))
        case _:
            print(main.__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
