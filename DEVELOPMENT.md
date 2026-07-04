# Development Guide

This guide covers local development setup for contributors and developers who want to build and test changes locally.

## Prerequisites

- Docker and Docker Compose
- pre-commit (for code quality checks)

## Local Development Setup

### Clone and Configure

```bash
git clone https://github.com/mrgeetv/live-webcam-aggregator.git
cd live-webcam-aggregator

# Copy environment template
cp .env.example .env

# Edit .env and add your YouTube API key
```

### Using Docker Compose

Docker Compose is recommended for local development as it rebuilds from source:

```bash
# Start the service
docker compose up -d

# View logs
docker compose logs -f

# Restart after code changes
docker compose down && docker compose up -d --build
```

**Playlist URL:** `http://localhost:23457/playlist.m3u8`

### Environment Variables

Configure these in your `.env` file or pass directly to docker-compose:

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `CATALOGUE_INTERVAL_HOURS` | `6` | Hours between catalogue refresh cycles |
| `EXCLUDE_CATEGORIES` | (none) | Comma-separated categories to drop across all sources (case-insensitive) |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_PARALLEL_SOURCES` | `4` | How many sources discover + liveness-check at once (min 1); total build concurrency ≈ this × `SCRAPE_WORKERS` |
| `PROXY_YOUTUBE` | `false` | `true` proxies YouTube through the server (survives the ~6h token expiry, trims DVR to the live edge); `false` (default) redirects players direct to YouTube (lower latency, stops at token expiry) |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base URL used in playlist and manifest links |
| `SCRAPE_WORKERS` | `min(16, cpu×4)` | Per-source concurrency for scraping + liveness; sources also run in parallel, so build-peak concurrency ≈ this × the number running at once |
| `SEARCH_QUERY` | built-in webcam query | YouTube search terms (`\|`=OR, space=AND, `-`=exclude) |
| `YOUTUBE_API_KEY` | (required) | YouTube Data API v3 key |

### Using the Run Script

A helper script is provided for common operations:

```bash
# Build and run with cache
scripts/run.sh

# Build without cache (clean build)
scripts/run.sh --no-cache
```

## Docker Hardened Images (DHI)

This project uses [Docker Hardened Images](https://www.docker.com/blog/docker-hardened-images-for-every-developer/) in CI/production for enhanced security. DHI images are minimal, pre-hardened containers with reduced attack surface.

### Local Development

Local development uses standard Python images (no authentication required). The `docker-compose.yml` overrides the Dockerfile defaults to use `python:3.14-slim`:

```bash
docker compose up -d
```

### CI/Production

CI builds use DHI images from `dhi.io`, requiring authentication. Repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` must be configured in GitHub.

### Image Variants

| Stage   | Image                                    | Purpose                                  |
|---------|------------------------------------------|------------------------------------------|
| Build   | `dhi.io/python:3.14-alpine3.24-sfw-dev`  | Has pip for installing dependencies      |
| Runtime | `dhi.io/python:3.14-alpine3.24`          | Minimal, no pip (reduced attack surface) |

## Pre-commit Hooks

This project uses pre-commit hooks for code quality. Install them before making changes:

```bash
pip install pre-commit
pre-commit install
```

Hooks will run automatically on commit, checking:

- Python formatting (black)
- Python linting (flake8)
- Shell script validation (shellcheck)
- Dockerfile linting (hadolint)
- Markdown formatting (markdownlint)
- Conventional commit messages
- Type checking (basedpyright)
- **Tests + coverage (pytest)**: runs the full suite with a coverage floor when
  `src/`, `tests/`, or `requirements*.txt` change
- **Dead-code detection (vulture)**: flags unused functions/attributes on `src/`

> **Note:** the `pytest` hook calls `pytest` directly, so your dev virtualenv must
> be active (or otherwise on `PATH`) when committing. The same checks run in CI on
> every pull request, so nothing merges without the tests passing.

To run hooks manually:

```bash
pre-commit run --all-files
```

## Testing

The test suite is **fully offline** (no network or API key needed). Create a
virtualenv with both requirement files, then run pytest:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                                                      # run the suite
pytest --cov=webcam_aggregator --cov-report=term-missing    # with coverage
```

The suite runs in **parallel** via `pytest-xdist` (`-n auto`, one worker per CPU, so
it adapts to whatever box you're on); run `pytest -n 0` to go serial (e.g. for `pdb`).

The same `pytest` + coverage floor runs as a pre-commit hook (when `src/`, `tests/`,
or `requirements*.txt` change) and in CI, so a failing test blocks the commit and
the merge. Test files are named `*_test.py` (enforced by the `name-tests-test` hook).

## Project Structure

```text
live-webcam-aggregator/
├── src/
│   └── webcam_aggregator/      # v2 application package
│       ├── __main__.py         # Entry point (python -m webcam_aggregator)
│       ├── app.py              # App wiring and main loop
│       ├── config.py           # Environment variable config
│       ├── models.py           # Data contracts and stream models
│       ├── fetch.py            # HTTP fetch helpers
│       ├── registry.py         # Extractor registry
│       ├── dedup.py            # Deduplication and field merge
│       ├── categories.py       # Category taxonomy mapping
│       ├── catalogue.py        # Catalogue builder and liveness validation
│       ├── cache.py            # Resolve cache (TTL, LRU, negative caching)
│       ├── serving.py          # Serving logic (playlist render, manifest/segment proxy)
│       ├── signing.py          # HMAC signing of proxied manifest/segment URLs
│       ├── extractors/         # Stream URL extractors
│       │   ├── ytdlp.py        # yt-dlp extractor
│       │   ├── direct_hls.py   # Direct HLS link extractor
│       │   ├── metatag.py      # HTML meta-tag extractor
│       │   ├── baltic.py       # Baltic Live cam extractor
│       │   ├── ipcamlive.py    # IPCamLive extractor
│       │   ├── earthcam.py     # EarthCam extractor (page -> HLS)
│       │   └── skyline.py      # SkylineWebcams extractor (cam page -> hd-auth HLS)
│       └── sources/            # Stream discovery sources
│           ├── youtube_api.py  # YouTube Data API v3 source
│           ├── worldcams.py    # Worldcams.net scraper source
│           ├── cxtvlive.py     # CXTV Live scraper source
│           ├── camscape.py     # Camscape aggregator scraper source
│           ├── camsecure.py    # CamSecure scraper source (sitemap -> direct HLS)
│           ├── earthcam.py     # EarthCam mapsearch API source
│           ├── explore.py      # explore.org streams.json source (direct HLS)
│           ├── skyline.py      # SkylineWebcams scraper source
│           └── wildlife_trusts.py  # The Wildlife Trusts index -> regional YouTube cams
├── scripts/                    # Helper scripts
│   ├── run.sh                  # Docker build/run script
│   └── check-python-version.sh
├── .github/workflows/          # CI/CD pipelines
├── Dockerfile                  # Container definition
├── docker-compose.yml          # Development compose file
├── requirements.txt            # Python dependencies
└── requirements-dev.txt        # Development dependencies
```

## Port Configuration

- **Internal port:** 8000 (fixed; remap on the host side via the compose port mapping)
- **Docker Compose port:** 23457 (mapped from 8000)

The HTTP server serves `/playlist.m3u8`, `/health`, and `/stream/<id>`
(on-demand resolve + HLS manifest proxy) endpoints.

## Debugging

### Log Levels

Set `LOG_LEVEL` in your `.env` file to control logging verbosity:

```bash
# In .env file
LOG_LEVEL=DEBUG
```

Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`

### Catalogue & resolve logging

At `INFO`, each catalogue rebuild logs per-source `kept / discovered` counts and any
source collapse (empty-guard). At `DEBUG`, it also logs dropped/failed resolves and
liveness-probe failures. Live process memory (`rss_mb`) and the per-source **raw**
outcome of the last rebuild (`kept`/`discovered`/`crashed`) are exposed on the `/health`
endpoint, along with a `healthy` rollup (`ready` **and** no source crashed or returned 0
this cycle) and `unhealthy_sources` for a single uptime check. The per-source counts stay
raw even when the empty-guard is still serving a failed source's last good set, so a
failure surfaces immediately. See the *Monitoring* section in the README for the payload
shape and the state table.

A source category we have no mapping for logs a `WARNING` (once per distinct value) and
the cam lands in the `Unmapped Category` group rather than `Other`; camscape and skyline
also log an upfront per-source line listing the unmapped category slugs they crawled. Add
the mapping to `categories._MAP` (or the source's own slug map) to clear it.

A cam a source left **uncategorised** (would be `Other`) gets a title-keyword fallback
(`categories.category_from_title`): keywords in the cam name (`beach`, `harbour`, a
species, `ski`…) map to a category, and failing that a `City, Country`-style location in
the title makes it `Travel & Events`; only titles with no signal at all stay `Other`. It
never touches a cam that already has a real or `Unmapped` category.

Suspect config logs a `WARNING` at startup rather than failing silently: a non-numeric
`CATALOGUE_INTERVAL_HOURS`/`SCRAPE_WORKERS`/`MAX_PARALLEL_SOURCES`, a
non-`true`/`false` `PROXY_YOUTUBE`, an
unknown `LOG_LEVEL`, a `localhost` `PUBLIC_BASE_URL` (unreachable by remote players),
or an unknown name in `EXCLUDE_CATEGORIES`.

Memory peaks **during** the catalogue build: sources run concurrently (up to
`MAX_PARALLEL_SOURCES` at once, each fetching + liveness-checking its site with
`SCRAPE_WORKERS` threads), then it settles back once the playlist is built. The build
is the high-water mark, not a leak — peak concurrency is ~`MAX_PARALLEL_SOURCES` ×
`SCRAPE_WORKERS`. Lower `SCRAPE_WORKERS` to reduce that peak.

### Viewing Debug Logs

```bash
# Ensure LOG_LEVEL=DEBUG is set in .env, then restart
docker compose down && docker compose up -d

# View logs
docker compose logs -f
```

## Making Changes

1. Fork the repository on GitHub
2. Clone your fork locally
3. Create a feature branch from `main`
4. Make your changes
5. Ensure pre-commit hooks pass
6. Test locally with Docker Compose
7. Push to your fork and submit a pull request

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Format:

```text
type(scope): description
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`

Scopes: `docker`, `api`, `playlist`, `config`, `deps`, `ci`, `docs`
