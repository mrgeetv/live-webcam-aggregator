# Project: Live Webcam Aggregator (multi-source, v2)

> **Note**: CLAUDE.md is a symlink to this file. Edit AGENTS.md only.

## Development Context

@DEVELOPMENT.md

## Architecture & Extension Points (v2)

The app is two phases, decoupled by a catalogue snapshot:

1. **Catalogue build** (`catalogue.py`, every `CATALOGUE_INTERVAL_HOURS`): sources run
   **concurrently** (discover + liveness), capped at `MAX_PARALLEL_SOURCES` so total
   build concurrency stays ~cap × `SCRAPE_WORKERS` no matter how many sources exist, and
   a source that crashes is isolated (reuses its last good set, the rest proceed). Each
   `Source.discover()` yields `Candidate`s → liveness filter (YouTube via the Data
   API batch; everything else via a **fetch-verified probe** — `make_is_alive`
   actually fetches the HLS manifest and drops dead/404 and DASH cams) → per-source
   empty-guard (keeps the last good set if a source collapses ≥50%, needs 2 bad
   cycles to accept) → cross-source `dedupe()` (per-field merge) → YouTube cams get
   their category from the Data API; scraped titles get location appended
   (`with_location`) → `CatalogueEntry`s with a stable id.
2. **On-demand serve** (`serving.py` + `app.py` handler): the playlist holds stable
   `/stream/<id>` URLs. On play, `ResolveCache` resolves the upstream via the
   `Registry`→`Extractor`, and the HLS manifest is proxied — child manifests rewritten
   through `/stream/<id>/m?u=…&sig=…`. Segments go **direct to the CDN by default**,
   with two per-host exceptions (their tokens are IP-bound to the fetcher):
   `_DIRECT_PLAYBACK_HOSTS` (pixelcaster) get a **302 passthrough** so the player
   fetches the whole chain itself; `_PROXY_SEGMENT_HOSTS` (balticlivecam, skylinewebcams, earthcam)
   get their **segments relayed** through `/stream/<id>/s?u=…&sig=…`.
   **YouTube cams 302-redirect straight to googlevideo by default** (`PROXY_YOUTUBE`
   off): lower latency / less buffering on shallow live windows, but playback stops
   when the ~6h googlevideo token expires. `PROXY_YOUTUBE=true` proxies them like the
   rest (survives expiry via re-resolve), and proxied **DVR** playlists are trimmed to
   the live edge (`truncate_to_live_edge`) so we never relay the multi-MB rewind
   buffer (the manifest fetcher uses `MANIFEST_MAX_BYTES`, not the 8 MB default).

**`build_app()` in `app.py` is the wiring seam.** To extend:

- **Add a source** — implement the `Source` protocol (`sources/base.py`): a `name`
  and `discover() -> Iterable[Candidate]`. HTML scrapers subclass `HtmlScraperSource`
  (`sources/base.py`) and implement three hooks — `_page_urls()` (the cam detail-page
  URLs), `_page_meta(html, url)` (per-page `(category, ctx)`), and `_title_for(cand,
  url, category, ctx)` — the base owns the concurrent fetch + the extraction ladder.
  Override `_candidates(html, url)` too when a site's embeds aren't in the standard
  ladder (e.g. Skyline's Clappr token / a `videoId` JS var → a page or watch URL).
  Add the instance to `active_sources` in `build_app`. Set `Candidate.predisc_key` so
  dedup can merge it (`yt:<id>` for YouTube, `hls:<normalised>` for direct m3u8,
  `None` = never merged).
- **Add an extractor** — implement the `Extractor` protocol (`extractors/base.py`):
  `resolve(target_url) -> Resolved(url, stream_type, ttl_seconds)`. Add it to the
  `extractors` dict in `build_app` AND a predicate to `build_registry` (startup
  validation raises if a rule names an extractor not in the dict). If the CDN's
  tokens are IP-bound, ALSO add its host to `_DIRECT_PLAYBACK_HOSTS` (passthrough)
  or `_PROXY_SEGMENT_HOSTS` (segment relay) in `serving.py`, or segments will 403.
- **Category mapping** lives in `categories.py` (`_MAP`); YouTube categories come
  from the Data API (`videos.list` categoryId) and pass through, everything else
  maps to the unified taxonomy. `map_category` splits the two miss cases: a source
  that gave **no** category → "Other"; a source that gave one we **don't recognise** →
  **"Unmapped Category"** (a distinct group, visible in the player) + a once-per-process
  `WARNING` naming the raw value, so a missing mapping surfaces instead of hiding in
  "Other". Sources that pre-map slugs (`camscape`, `skyline`) pass an unknown slug
  through raw so it reaches that path, and **crawl their category index first** —
  camscape's `/showing/`, skyline's `/en/live-cams.html` — logging slugs absent from
  their slug map (worldcams/cxtvlive have no clean index, so they surface unmapped
  categories per-stream via `map_category`). A cam that still lands in **"Other"** (the
  source gave no category) gets a last-resort **title fallback** (`category_from_title`,
  applied in `catalogue._to_entry` **only** when the mapped category is "Other" — never
  over a real or "Unmapped" one): ordered keyword rules over the cam **name** (the part
  before the `with_location` " — geo" suffix, so a region in the geo can't false-trigger),
  first match wins (a species/"harbour" beats a generic "street"/"city"); failing that, a
  name carrying a `City, Region, Country`-style geo is a place view → **"Travel & Events"**.
  Keep `_TITLE_RULES` GENERAL (real category words, not one-off cam names) — an import-time
  guard raises if a rule names a category outside `ALL_CATEGORIES`. `EXCLUDE_CATEGORIES`
  (config) post-filters the built catalogue by mapped category, across all sources. The
  full excludable set is `categories.ALL_CATEGORIES` — a test guards the README list matches.
- **Add a config/env var** — parse it in `config.py` via the `_*_env` helpers, and
  ALWAYS validate: a bad/unparseable value must log a `WARNING` at startup and fall
  back to the default, never crash or silently misbehave (e.g. `_int_env` warns on a
  bad int, `_bool_env` on a non-`true`/`false`; `_warn_on_suspect_config` flags
  dubious-but-valid values). Document every new var in README + DEVELOPMENT +
  `.env.example`.

**Hard-won lessons (don't relearn these):**

- Route ipcamlive **`player/player.php` URLs only** to the resolver; direct
  `s*.ipcamlive.com/.../stream.m3u8` (the majority) must fall through to `DirectHls`.
- Baltic's admin-ajax POST needs `Referer` = the **site origin** (`origin_of`), not
  the ajax URL — wrong Referer 403s silently.
- YouTube extraction needs the deno/yt-dlp-ejs stack (the n-challenge); the Dockerfile
  patches deno's ELF interpreter so it runs on the hardened Alpine runtime — leave it.
- Liveness is a **build-time** probe (the playlist must not list dead cams); the
  serve-time resolve is separate and fresh (tokens expire). Don't merge them.
- yt-dlp is forced to an **HLS** format (`-f b[protocol*=m3u8]`) — some live streams
  default to DASH (`.mpd`) which the HLS proxy can't serve; `serve_stream` rejects
  any non-`#EXTM3U` body (so DASH-only cams are dropped, not served broken).
- YouTube's `eventType=live` search is capped at ~100 results via `pageToken` (it
  reports a huge `totalResults` but returns an empty page 3). `discover()` paginates
  by `publishedBefore` time-windows instead (walking back from the last item's
  `publishedAt`) to reach the deeper hundreds; `pageToken` silently caps you at ~100.
- Skyline cam pages carry NO per-cam category — it lives only in which category page
  lists the cam, so `SkylineSource` crawls the category pages for `cam -> category`
  then BFS's country/region pages for the rest (uncategorised -> "Other"). Two embed
  types: own Clappr HLS (`source:'livee.m3u8?a=<token>'`, token regenerated per
  page-load so `SkylineResolver` re-resolves the page at serve-time to
  `hd-auth.skylinewebcams.com`) and "from the web" YouTube (`videoId:'…'` → watch URL,
  dedups via `yt:`). Names come from the **breadcrumb** (English geo), not the URL path
  (native: italia/espana).
- camscape aggregates from many providers — the cam page's `"streams":[{…}]` JSON is the
  source of truth (the rendered iframe shows only the active angle), one candidate per
  stream `url`. Most route to existing extractors (YouTube, ipcamlive, m3u8, feratel);
  plus a bespoke `earthcam` extractor (fetch page → grab its `.m3u8`; EarthCam 403s
  without a Referer, see `_REFERER_HOSTS` in `fetch.py`) and a twitch normalise
  (`player.twitch.tv/?channel=X` → `twitch.tv/X` → yt-dlp). ivideon (WebRTC), rtsp.me
  (server fetch gets only stub manifests, segments 404) + angelcam (auth) are
  unservable, dropped. Category + location come from the cam page's
  `/showing/<cat>` + `/location/<loc>` tags.
- EarthCam is a source via its **mapsearch JSON API** (no HTML scrape): `get_locations_network`
  (its own cams) + the global-bbox `get_locations` (the whole map, incl partners). It's a
  meta-aggregator — ~4000 mapped cams across 2400+ one-off sites — so `EarthCamSource._routable`
  keeps only URLs that hit an existing extractor: EarthCam's own geographic pages (`/usa/`,
  `/world/` resolve; `/clients/`, `/top25/`, `myearthcam.com` roots don't) + partner YouTube /
  balticlivecam / ipcamlive / direct-HLS. The long tail (gov traffic cams = static JPEG, the
  one-off sites) is dropped. The API needs the `earthcam.com` Referer (`_REFERER_HOSTS`); the
  feed carries no content category → all "Other".
- CamSecure is a 2-hop scrape off its **sitemap** (`sitemap.xml`, ~800 URLs) → per-cam pages
  → the player iframe on `camsecure.co`/`.uk` (`httpswebcam/…`) whose video.js carries a
  direct `/HLS/<name>.m3u8` (open CDN, served by `DirectHls`; segments need no token/Referer).
  **A page is a cam iff it embeds that player iframe AND its player page has an HLS `<source>`
  — decided by the check, NOT the URL** (many cam pages have no "webcam" in the name; a URL
  filter silently dropped ~100). `_SKIP` only drops non-cams that *do* embed a demo player
  (homepage, demo index, product/widget pages). Both hops fetch concurrently (~240 cams). The
  player **page** serves a decoy without `Referer: camsecure.co.uk`, so `camsecure.co`/`.uk`
  are in `_REFERER_HOSTS`. A few cams embed third-party HLS — `rtsp.me` is skipped (its stub
  manifest passes liveness but segments 404). Titles come from the page `<title>` (boilerplate
  stripped, the "… from `<place>`" tail when it leads with boilerplate, the URL filename as a
  last resort). No category → "Other".
- explore.org is a source via its **`streams.json`** API (`d11gsgd2hj8qxd.cloudfront.net`,
  the `id_in` filter is ignored — one call returns all ~160). Keep `state == "live"` with a
  `.m3u8` `playlistUrl` (~140) — a direct, open HLS served by `DirectHls` (no token/Referer).
  We deliberately use the HLS, **not** YouTube: explore embeds each cam's YouTube from its
  **partner** channel (so there's no single channel to enumerate, and the per-cam id is
  JS-redacted on the page), and `streams.json` is the only complete list. Trade-off: cams
  also on YouTube can't dedup (the `hls:` key won't merge a `yt:` one) — accepted as a small,
  bounded overlap. No category in the feed → "Other".
- The Wildlife Trusts webcams index links out to ~17 **regional-trust** cam pages on their own
  domains, mostly YouTube embeds the standard ladder resolves; all are wildlife → hardcoded
  category **Animals**. Titles come from the index link text (the "`<Region> Wildlife Trust`"
  prefix + trailing "Watch…" stripped). Pages whose embed is JS/consent-gated (no id in the
  static HTML, or only a channel link) yield nothing and drop — so only the statically-
  extractable ones (~11) make it.

**Security model:** every outbound fetch is validated by `fetch._resolve_validated_ip`
(rejects non-http(s) and private/loopback/link-local/reserved IPs), an 8 MB cap, and
**per-hop redirect re-validation** in the `requests` `Fetcher`; proxied `/m` and `/s`
URLs are HMAC-signed (`signing.py`) so only server-emitted URLs are fetched.
**DNS-rebinding TOCTOU is now closed in-app** (no firewall needed): the `Fetcher`
resolves+validates the host once (`_resolve_validated_ip`) and then **pins the DNS
resolution to that validated IP** for the connect, via a thread-local `getaddrinfo`
override scoped by `_PinDNS` (the curl `--resolve` approach). urllib3 still connects
to the hostname, so SNI, the `Host` header, and certificate validation stay bound to
the original hostname (and `verify` stays on) while the socket goes to the pinned IP;
there's no second lookup between check and connect. (An earlier adapter + pool-kwargs
attempt was dropped: urllib3 2.x ignores `server_hostname` passed that way, so SNI
fell back to the IP and Cloudflare 403'd it.) **Known residual** (mitigate by running
behind your own network controls): the **egress-proxy surface** — the proxy will sign
and fetch any *public* host that appears in an upstream manifest; durable fix = a
CDN-host allowlist on the rewritten `/m`/`/s` URLs.

**Tests:** files are `*_test.py` (the `name-tests-test` hook rejects `test_*.py`).
The suite is **fully offline** — no real-endpoint/live tests (sources, resolvers,
and the HTTP handler are exercised with injected fakes + real sockets on port 0).
The gate is `pre-commit` (which runs `pytest` + a coverage floor as a `files:`-gated
hook) plus the same checks in CI — not ruff/mypy. The `pytest` hook calls `pytest`
directly, so the dev venv must be on `PATH` when committing.

## Branching Workflow

For any new work (fixes, features, chores, etc.):

1. Pull latest main
2. Create new branch from main
3. Make changes and commit

A Claude Code `PreToolUse` hook in `.claude/settings.json` enforces this
by blocking `git commit` when the current branch is `main` or `master`.
The hook script lives at `.claude/hooks/block-commit-to-main.sh`.

`.claude/settings.json` also carries two **non-blocking** editing reminders: editing
`requirements*.txt` recalls the dependency-version rule (check latest, pin new,
never auto-bump existing); editing a `src/webcam_aggregator/*.py` module recalls to
add/update the matching `*_test.py`.

## Conventional Commit Format

Format: `type(scope): description`

### Commit Types

**Release types** (trigger version bumps):

- `feat` - New feature (minor version bump)
- `fix` - Bug fix (patch version bump)
- `perf` - Performance improvement (patch version bump)
- `revert` - Revert previous change (patch version bump)
- `refactor` - Code refactoring (patch version bump)

**Non-release types** (no version bump):

- `docs` - Documentation changes
- `style` - Code style/formatting
- `chore` - Maintenance tasks
- `test` - Test changes
- `build` - Build system changes
- `ci` - CI/CD changes

### Valid Scopes

- `docker` - Dockerfile, docker-compose.yml
- `api` - YouTube API integration
- `playlist` - M3U8 playlist generation
- `scraper` - Stream extraction, yt-dlp, memory management
- `config` - Environment variables, configuration
- `deps` - Dependency updates
- `ci` - CI/CD workflows, automation
- `docs` - Documentation, README

## Dependency Version Research

When adding or updating versioned dependencies (Python packages, GitHub Actions, pre-commit hooks, Docker images, etc.):

1. Find the GitHub repo (WebSearch if URL unknown)
2. Get latest version using one of:
   - `gh release list --repo owner/repo --limit 5` (preferred when repo is known)
   - WebFetch on GitHub releases page (fallback)
3. If version cannot be verified from GitHub, stop and ask user to confirm

## Pre-commit Behavior

When pre-commit finds issues:

- **Never automatically fix them**
- Always present the issues to the user first
- Let the user decide whether to fix, ignore, or configure exceptions
- This includes: file permissions, line length violations, formatting issues, etc.

## Bash Script Best Practices

Always use modern bash syntax:

- Use `[[ ]]` instead of `[ ]` for test conditions
- Use `$(command)` instead of backticks
- Quote all variable expansions: `"$var"`
- Use `#!/bin/bash` shebang

## Documentation Rules

**Keep the docs in lockstep with the code — this is part of the change, not a
follow-up.** Whenever a change alters how the app actually works (architecture, the
catalogue→serve flow, sources/extractors, config or env vars, serving/CDN behaviour,
the security model, build/CI, or the "hard-won lessons"), update **`AGENTS.md` in the
same change** — it is the agent-facing source of truth, so a stale entry silently
misleads the next agent or contributor. Also update `README.md` (users),
`DEVELOPMENT.md` (contributors), and `.env.example` wherever they document the changed
behaviour. If a change touches something a doc covers and the doc isn't updated, the
change isn't finished.

When updating this file:

- **Never duplicate information** - check existing sections before adding new content
- **Reorganize instead of duplicating** - if information exists but is unclear, reorganize or clarify existing sections
- **Add only project-specific information** - valid scopes, project-specific tools, version constraints

## Code Quality Tools

Pre-commit hooks enforced:

- **black** - Python code formatting
- **flake8** - Python linting (ignores: E501, E203)
- **shellcheck** - Shell script validation (severity: warning)
- **markdownlint-cli2** - Markdown formatting (CHANGELOG.md excluded)
- **hadolint** - Dockerfile linting
- **conventional-pre-commit** - Commit message validation (strict mode with forced scopes)
- **check-python-version** - Custom validation that .python-version matches Dockerfile, docker-compose.yml, and pyrightconfig.json
- **basedpyright** - Python type checking (stricter pyright fork with pylance features)
- **pytest** - Full test suite + coverage floor (`--cov-fail-under`); runs when `src/`, `tests/`, or `requirements*.txt` change. Calls `pytest` directly, so the dev venv must be on `PATH` when committing. Also runs in CI.
- **vulture** - Dead-code detection (unused functions/attributes/fields) on `src/` at confidence 60; catches what flake8/basedpyright miss (they only flag unused imports/locals). Framework-dispatched handler methods are ignored by name.

## Python Version Synchronization

This project enforces Python version consistency:

- `.python-version` - Source of truth (currently 3.14)
- `Dockerfile` - `ARG RUNTIME_IMAGE` default must use `dhi.io/python:{version}-alpine3.24`
- `docker-compose.yml` - `RUNTIME_IMAGE` build arg must use `python:{version}-slim`
- `pyrightconfig.json` - Must have `pythonVersion` matching .python-version
- Pre-commit hook validates synchronization automatically
- CI uses .python-version for GitHub Actions Python setup

**When updating Python version:**

1. Update `.python-version` file
2. Update `Dockerfile` `ARG RUNTIME_IMAGE` and `ARG BUILD_IMAGE` defaults to match
3. Update `docker-compose.yml` `RUNTIME_IMAGE` and `BUILD_IMAGE` args to match
4. Update `pyrightconfig.json` pythonVersion to match
5. Pre-commit hook validates consistency
6. Test Docker build before committing
