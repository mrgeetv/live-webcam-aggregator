#!/bin/bash

# Navigate to project root (where docker-compose.yml is)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

# Stop any existing containers
docker compose down

# Build, optionally without cache
if [ "$1" == "--no-cache" ]; then
    echo "Building without cache..."
    docker compose build --force-rm
else
    echo "Building with cache..."
    docker compose build
fi

# Run the container
docker compose up -d

# Follow logs
docker compose logs -f
