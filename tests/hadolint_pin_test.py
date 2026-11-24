"""Tests for the pre-commit check that hadolint's pinned image tag follows its rev.

The check is scripts/check_hadolint_pin.py.
"""

from __future__ import annotations

import check_hadolint_pin

_CONFIG = """\
repos:
  - repo: https://github.com/shellcheck-py/shellcheck-py
    rev: v0.11.0.1
    hooks:
      - id: shellcheck

  - repo: https://github.com/hadolint/hadolint
    rev: {rev}
    hooks:
      - id: hadolint-docker
        entry: ghcr.io/hadolint/hadolint:{tag} hadolint

  - repo: local
    hooks:
      - id: vulture
        entry: vulture
"""


def test_tag_matching_rev_passes() -> None:
    config = _CONFIG.format(rev="v2.15.1", tag="v2.15.1")
    assert check_hadolint_pin.mismatch(config) is None


def test_bumped_rev_with_stale_tag_names_both_versions() -> None:
    config = _CONFIG.format(rev="v2.16.0", tag="v2.15.1")
    message = check_hadolint_pin.mismatch(config)
    assert message is not None
    assert "v2.16.0" in message and "v2.15.1" in message
