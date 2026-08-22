#!/bin/bash
# pre-commit entry point for the pytest hook, so the hook doesn't depend on the
# committing shell's PATH: prefer the project venv, fall back to whatever pytest
# is installed (CI installs into the runner's environment).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv/bin/pytest ]]; then
    exec .venv/bin/pytest "$@"
fi
exec pytest "$@"
