#!/bin/bash
# Pre-commit hook to validate Python versions match across:
# - .python-version (source of truth)
# - Dockerfile ARG defaults (DHI images for CI)
# - docker-compose.yml build args (standard images for local dev), whose Alpine
#   release must also match the Dockerfile's
# - pyrightconfig.json pythonVersion (type checker)
# - pyproject.toml requires-python (uv's lock and venv, ruff's target version)

PYTHON_VERSION_FILE=".python-version"
DOCKERFILE="Dockerfile"
COMPOSE_FILE="docker-compose.yml"
PYRIGHT_CONFIG="pyrightconfig.json"
PYPROJECT="pyproject.toml"

# Read expected version from .python-version
if [[ ! -f "$PYTHON_VERSION_FILE" ]]; then
    echo "Error: $PYTHON_VERSION_FILE not found"
    exit 1
fi
EXPECTED_VERSION=$(cat "$PYTHON_VERSION_FILE" | tr -d '\n\r')

# Check Dockerfile ARG RUNTIME_IMAGE default
if [[ ! -f "$DOCKERFILE" ]]; then
    echo "Error: $DOCKERFILE not found"
    exit 1
fi
DOCKERFILE_VERSION=$(grep -E '^ARG RUNTIME_IMAGE=.*python:' "$DOCKERFILE" | sed -E 's/.*python:([0-9]+\.[0-9]+).*/\1/')
if [[ -z "$DOCKERFILE_VERSION" ]]; then
    echo "Error: Could not extract Python version from $DOCKERFILE ARG RUNTIME_IMAGE"
    exit 1
fi
if [[ "$EXPECTED_VERSION" != "$DOCKERFILE_VERSION" ]]; then
    echo "❌ Python version mismatch in Dockerfile!"
    echo "  .python-version: $EXPECTED_VERSION"
    echo "  Dockerfile ARG:  $DOCKERFILE_VERSION"
    exit 1
fi

# Check docker-compose.yml RUNTIME_IMAGE build arg
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Error: $COMPOSE_FILE not found"
    exit 1
fi
COMPOSE_VERSION=$(grep -E 'RUNTIME_IMAGE:.*python:' "$COMPOSE_FILE" | sed -E 's/.*python:([0-9]+\.[0-9]+).*/\1/')
if [[ -z "$COMPOSE_VERSION" ]]; then
    echo "Error: Could not extract Python version from $COMPOSE_FILE RUNTIME_IMAGE"
    exit 1
fi
if [[ "$EXPECTED_VERSION" != "$COMPOSE_VERSION" ]]; then
    echo "❌ Python version mismatch in docker-compose.yml!"
    echo "  .python-version:      $EXPECTED_VERSION"
    echo "  docker-compose.yml:   $COMPOSE_VERSION"
    exit 1
fi

# Check docker-compose.yml's BUILD_IMAGE and RUNTIME_IMAGE use the Dockerfile runtime's Alpine
DOCKERFILE_ALPINE=$(grep -E '^ARG RUNTIME_IMAGE=' "$DOCKERFILE" | sed -nE 's/.*-alpine([0-9]+\.[0-9]+).*/\1/p')
if [[ -z "$DOCKERFILE_ALPINE" ]]; then
    echo "Error: Could not extract an Alpine release from $DOCKERFILE ARG RUNTIME_IMAGE"
    exit 1
fi
while read -r line; do
    COMPOSE_ALPINE=$(sed -nE 's/.*-alpine([0-9]+\.[0-9]+).*/\1/p' <<< "$line")
    if [[ "$COMPOSE_ALPINE" != "$DOCKERFILE_ALPINE" ]]; then
        echo "❌ Alpine release mismatch in docker-compose.yml!"
        echo "  Dockerfile ARG:       alpine$DOCKERFILE_ALPINE"
        echo "  docker-compose.yml:   ${line// /}"
        exit 1
    fi
done < <(grep -E '(BUILD|RUNTIME)_IMAGE:' "$COMPOSE_FILE")

# Check pyrightconfig.json pythonVersion (if file exists)
if [[ -f "$PYRIGHT_CONFIG" ]]; then
    PYRIGHT_VERSION=$(grep -E '"pythonVersion"' "$PYRIGHT_CONFIG" | sed -E 's/.*"([0-9]+\.[0-9]+)".*/\1/')
    if [[ -z "$PYRIGHT_VERSION" ]]; then
        echo "Error: Could not extract pythonVersion from $PYRIGHT_CONFIG"
        exit 1
    fi
    if [[ "$EXPECTED_VERSION" != "$PYRIGHT_VERSION" ]]; then
        echo "❌ Python version mismatch in pyrightconfig.json!"
        echo "  .python-version:      $EXPECTED_VERSION"
        echo "  pyrightconfig.json:   $PYRIGHT_VERSION"
        exit 1
    fi
fi

# Check pyproject.toml requires-python, which pins one minor version ("==3.14.*")
if [[ ! -f "$PYPROJECT" ]]; then
    echo "Error: $PYPROJECT not found"
    exit 1
fi
PYPROJECT_VERSION=$(grep -E '^requires-python *= *"==[0-9]+\.[0-9]+\.\*"' "$PYPROJECT" | sed -E 's/.*"==([0-9]+\.[0-9]+)\.\*".*/\1/')
if [[ -z "$PYPROJECT_VERSION" ]]; then
    echo "Error: Could not extract a '==X.Y.*' requires-python from $PYPROJECT"
    exit 1
fi
if [[ "$EXPECTED_VERSION" != "$PYPROJECT_VERSION" ]]; then
    echo "❌ Python version mismatch in pyproject.toml!"
    echo "  .python-version:      $EXPECTED_VERSION"
    echo "  pyproject.toml:       $PYPROJECT_VERSION"
    exit 1
fi

echo "✅ Python versions match: $EXPECTED_VERSION"
exit 0
