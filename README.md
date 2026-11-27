# Live Webcam Aggregator

Turn live webcams from across the web into a single, categorised **M3U8 playlist**
you can open in any M3U8/HLS-capable player.

It discovers live webcam streams from multiple sources, merges and de-duplicates them
into one playlist, and serves it over HTTP. Streams are resolved **on demand** when a
channel is opened, so the playlist stays current and the server only does work when a
stream is actually played.

## Sources

| Source | What it adds |
| ------ | ------------ |
| airportwebcams.net | Airport-cam directory — resolves each cam's YouTube channel to its current live stream |
| beachcam.meo.pt | Portuguese beach and surf cams |
| camscape.com | Aggregator directory — pulls in cams from many third-party providers |
| camsecure.co.uk | Directory of hosted webcams |
| cxtvlive.com | Global webcam directory — beaches, cities, wildlife, transit and traffic |
| earthcam.com | EarthCam's own cams plus its partner network |
| explore.org | Nature and wildlife cams |
| feratel.com | Alpine/European tourism cams (panorama-sweep video, refreshed every few minutes) |
| hdontap.com | US streaming provider — beaches, piers, wildlife nest cams |
| its-i.com (Share-Ju) | Japanese live-cam directory — rivers, traffic, trains, scenic |
| livefromiceland.is | Iceland — volcanoes, glaciers, aurora, Reykjavík |
| livespotting.tv | German and Alpine coastal, harbour and town cams |
| ozolio.com | Hawaii and US resort/beach cams |
| resortcams.com | US Southeast ski, mountain and beach-town cams |
| skylinewebcams.com | Camera directory |
| tomarigi.me (とまり木) | Curated YouTube live cams — Japan-founded, now largely worldwide |
| viewsurf.com | French coastal, harbour, ski and city cams |
| webcamera.pl | Polish network — Baltic beaches, Tatra mountains, ski resorts |
| whatsupcams.com | Adriatic network — Croatia, Slovenia, Italy and beyond |
| wildlifetrusts.org | UK wildlife-trust cams — the national index plus every regional trust |
| windy.com | Meta-aggregator — routes each live cam to its original provider's stream |
| worldcams.tv | Camera directory |
| YouTube Data API | Live webcam broadcasts found by search |

The same camera found on more than one source is merged into a single channel. A
typical catalogue holds around **11000–12000 live channels** once every source is
warm (windy.com's share ramps up over the first couple of days of refreshes), varying
with how many cams are actually up and with `EXCLUDE_CATEGORIES`.

## How it works

- **Per-source refreshes** (periodic): each source refreshes on its own interval —
  hourly for cheap single-feed sources, daily for the big crawls (skyline, tomarigi),
  6 hours for the rest (override with `SOURCE_INTERVAL_HOURS`). A refresh crawls the
  source and drops its dead streams; after each one the catalogue is re-merged from
  every source's latest cams (de-duplicated, mapped to a unified category) into a
  playlist of stable internal URLs. Up to `MAX_PARALLEL_SOURCES` refreshes run at
  once. The big crawls take minutes to tens of minutes, and that time is dominated by
  *waiting*, not work: a site that rate-limits (429/503) is backed off per host. So it
  depends on your egress IP far more than on your hardware, and it's why raising
  `SCRAPE_WORKERS` doesn't reliably speed it up. What each refresh found is kept in a
  small store under `/data`, so a restart serves the playlist at once (see
  *Persistent data*).
- **On-demand serving**: when a player opens a channel, the container resolves the
  stream on request and proxies the HLS manifest, refreshing expiring tokens
  transparently so long sessions keep playing. For most cams the video segments
  stream **directly** from the source CDN (only the small manifest passes through the
  box); a few sources whose streams are tied to the fetcher are handled specially
  (passthrough or relayed) so they still play.

## Quick start

### Prerequisites

- A YouTube Data API v3 key (free with a Google account). In the
  [Google Cloud Console](https://console.cloud.google.com/apis/credentials): create
  a project, enable *YouTube Data API v3*, then create an API key.
- Docker.

### Run

Make a directory for its data (see *Persistent data*), then pull and run the
published image:

```bash
mkdir -p ./data && sudo chown 65532:65532 ./data
docker run -d --name webcams \
  -p 23457:8000 \
  -v "$PWD/data:/data" \
  -e YOUTUBE_API_KEY=your_key_here \
  -e PUBLIC_BASE_URL=http://localhost:23457 \
  ghcr.io/mrgeetv/live-webcam-aggregator:v3
```

Or with a minimal `docker-compose.yml` (uses the published image, no build):

```yaml
services:
  webcams:
    image: ghcr.io/mrgeetv/live-webcam-aggregator:v3
    ports: ["23457:8000"]
    volumes: ["./data:/data"]
    environment:
      YOUTUBE_API_KEY: your_key_here
      PUBLIC_BASE_URL: http://localhost:23457
    restart: unless-stopped
```

The playlist is then available at `http://localhost:23457/playlist.m3u8`. On the
very first start it answers `503` until the first source has finished its first
refresh, then grows as the others land; the full catalogue takes **20–30 minutes**.
After that, a restart serves the stored catalogue straight away.

Set `PUBLIC_BASE_URL` to the address your players actually reach; `localhost` only
works for a player on the same machine (see *Exposing it*). These use the `:v3` major
tag, so you get v3.x updates but never an automatic jump to a future breaking major
(`:latest` would); see *Upgrading from v2*. The latest release's tags (`:v3`
included) are rebuilt daily when a base image is republished with patches, so
pulling `:v3` again picks them up; an older `:v3.x.y` pin is never rebuilt. `:beta`
carries prereleases of the next major: breaking changes, never rebuilt, for trying it
early. Want to build from source instead? See [DEVELOPMENT.md](DEVELOPMENT.md).

## Adding it to a player

It's a standard M3U8/HLS playlist, so it works in anything that can open one: media
players (VLC, mpv), IPTV apps, smart-TV apps, and similar:

1. Make sure the container is reachable at the address your player will use, and set
   **`PUBLIC_BASE_URL`** to that address (see *Exposing it* below), because the playlist hands
   out `/stream/<id>` URLs that must be reachable by the player.
2. Point the player at `https://<your-address>/playlist.m3u8`. Channels load, grouped
   by category.
3. Open a channel. The stream is resolved on demand and begins playing.

Notes:

- First play of a YouTube camera takes a few seconds (it resolves cold, then it's
  instant); other sources are near-instant.
- There's no EPG; webcams have no schedule, so they appear as channels without a guide.
- Each channel carries a stable `tvg-id`, so favourites stay linked to the right cam
  across catalogue refreshes, even as the total channel count changes.
- Tested with HLS/ExoPlayer-based players (e.g. TiViMate, VLC). Try one channel first
  to confirm your player + network path.

## Exposing it

The application is **exposure-agnostic**: it serves HTTP and builds links from
`PUBLIC_BASE_URL`. Any reverse proxy, tunnel (such as Tailscale), or direct port
mapping can sit in front. The only requirement is that the front door forwards
**both** `/playlist.m3u8` **and** `/stream/*` to the container, and that
`PUBLIC_BASE_URL` is set to the address clients actually reach
(for example `https://cams.example.com`).

## Endpoints

| Path | Purpose |
| ---- | ------- |
| `/playlist.m3u8` | The channel list |
| `/stream/<id>` | On-demand resolve + HLS manifest proxy (302 for MP4 sources) |
| `/health` | JSON status: readiness, `healthy` rollup, per-source outcome and refresh times, memory |

### Monitoring

`/health` is built for a single uptime check: point a JSON-query monitor at it and
alert when `$.healthy` isn't `true`.

```json
{
  "ready": true,
  "healthy": false,
  "streams": 3899,
  "unhealthy_sources": ["skyline"],
  "sources": {
    "skyline": {
      "kept": 0,
      "discovered": 0,
      "crashed": false,
      "status": "dead",
      "last_refresh_at": "2026-11-26T21:04:12Z",
      "last_refresh_seconds": 41.8,
      "interval_hours": 24
    }
  },
  "rss_mb": 1402.6
}
```

Fields:

- `ready` — every source has finished at least one refresh. The playlist serves
  before that, from the first refresh to land.
- `healthy` — `ready` **and** no source crashed, returned 0 cams on its last refresh,
  or is overdue.
- `unhealthy_sources` — the sources failing now (the reason `healthy` is `false`).
- `sources.<name>` — the **raw** result of that source's last refresh: `discovered`
  (found), `kept` (passed liveness), `crashed` (`discover`/liveness threw). These stay
  raw even when the empty-guard is still serving a failed source's kept set, so a
  dying source surfaces immediately.
- `sources.<name>.status` — `ok`, `degraded` (collapsed, guard serving the kept
  set), `dead` (0 cams or crashed), `overdue` (its last refresh finished more than
  twice its interval ago: the scheduler isn't getting to it), or `unknown` before its
  first refresh.
- `sources.<name>.last_refresh_at` / `last_refresh_seconds` — when its last refresh
  finished (UTC) and how long it took; `null` until the first one.
- `sources.<name>.interval_hours` — how often it refreshes.

The payload answers *what happened*; the *why* — per-host fetch outcomes, drop
reasons, hosts with no extractor, pacing/backoff counters — is on the log lines each
refresh writes (see `DEVELOPMENT.md`, *Catalogue & resolve logging*), and a stream that
fails to play logs which cam and why (*Playback failures*).

`/health` carries operational detail, so keep it off the public internet — expose only
`/playlist.m3u8` and `/stream/*` at your reverse proxy.

The states you'll see:

| `ready` | `healthy` | `unhealthy_sources` | Meaning |
| ------- | --------- | ------------------- | ------- |
| `false` | `false` | `[]` | Cold start — some sources haven't finished a first refresh (the playlist serves the ones that have) |
| `true` | `true` | `[]` | All sources succeeded |
| `true` | `false` | non-empty | Serving, but a listed source crashed, returned 0 or is overdue (`status` tells which) |

A cold start reads as `healthy: false` until the slowest source's first refresh lands,
so give the monitor a couple of retries to ride out normal restarts before it alerts.

**Basic HTTP-status monitoring** (no JSON query) is also possible via response codes:

- `/health` returns **200** whenever the process is up — even mid-cold-start — so a plain
  200 check only confirms the server is alive, not readiness or source health.
- `/playlist.m3u8` returns **503** until the first source's first refresh completes, then **200**.
  An HTTP-status monitor on it catches a down process and a stuck cold start, but not
  source-level degradation (a failed source still returns a served catalogue as `200`).

Use `$.healthy` for source-level alerting; the `/playlist.m3u8` status check is the
low-effort liveness option.

## Configuration

All via environment variables (see `.env.example`):

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DATA_DIR` | `./data` | **Compose only**, read by `docker compose` itself, never by the app: the host directory the repo's `docker-compose.yml` mounts at `/data`. See *Persistent data* |
| `EXCLUDE_CATEGORIES` | (none) | Comma-separated categories to drop, across all sources, case-insensitive. See *Filtering by category* |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_PARALLEL_SOURCES` | `4` | How many source refreshes (discover + liveness) run at once (min 1). Total concurrency ≈ this × `SCRAPE_WORKERS`; due sources queue, most overdue first |
| `PROXY_YOUTUBE` | `false` | `false` redirects players straight to YouTube (lower latency, but playback stops when YouTube's ~6h stream token expires — reselect to resume; a player retrying a stream that failed gets a freshly resolved one). `true` proxies YouTube through the server so it keeps playing past that, at a small latency cost |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Externally-reachable base for the emitted URLs |
| `SCRAPE_WORKERS` | `min(16, cpu×4)` | Per-source concurrency for scraping + liveness during a refresh (refreshes also run concurrently). Lower it to reduce peak memory (at the cost of slower refreshes). A host that starts rate-limiting (429/503) is backed off adaptively per host, so the effective rate to that host can drop below what this implies |
| `SOURCE_INTERVAL_HOURS` | per source | Override sources' refresh intervals: `name=hours,…`, e.g. `skyline=48,explore=0.5` (names as in `/health`). An unknown name or bad value logs a warning and that source keeps its default |
| `SEARCH_QUERY` | built-in webcam query | YouTube search terms (`\|`=OR, space=AND, `-`=exclude) |
| `YOUTUBE_API_KEY` | (required) | YouTube Data API v3 key |

> **Resource usage:** budget **~4 GB** for the container. A catalogue of this size sits
> under **~1 GB** between refreshes, and spikes **near 3 GB** when big sources refresh
> at once — that spike is the high-water mark, not a leak.
> Sources parse each page as it arrives rather than holding a whole site's pages, so a
> source's memory doesn't grow with its page count. RSS does **not** fall back to a small
> baseline afterwards. Lower `SCRAPE_WORKERS` to cap the peak. Live memory is on
> `/health` (`rss_mb`).

## Persistent data

The app keeps a small SQLite store at `/data/webcam-aggregator.db`: each source's
cams from its last refresh, the outcome of that refresh and when it ran. On a
restart it serves those at once, so players keep working and the `/stream/<id>`
links they hold stay valid, and only the sources that are due refresh. A stored set
older than twice its source's interval is dropped rather than served, and that
source refreshes first.

- **Everything in it can be rebuilt.** Losing it costs one cold start (the 20–30
  minutes above), never data. Deleting the directory is a safe reset. It needs no
  backup; if you take one anyway, use `sqlite3 webcam-aggregator.db ".backup copy.db"`
  or `VACUUM INTO`, never a file copy while the app is running.
- **Mount a directory, never the file.** SQLite keeps `-wal` and `-shm` files beside
  the DB, and they must live on the same filesystem. Create the directory and give it
  to the container's user (uid 65532) before the first start:
  `mkdir -p /srv/webcams && sudo chown 65532:65532 /srv/webcams`. With the repo's
  `docker-compose.yml`, set `DATA_DIR` to that path (default `./data`).
- **Local disks only.** NFS and SMB shares are unsupported: SQLite's WAL mode needs
  shared memory, which network filesystems don't reliably provide, and the app can't
  detect it.
- **Upgrades migrate it automatically**, including across several skipped releases.
- **Without a mount** the store lives in the container's own layer: it survives a
  restart but not a recreate, and startup logs a warning saying so. If it can't be
  opened at all the app runs **storeless** (a throwaway store, discarded at restart)
  and logs a warning; everything else works.

## Tuning the search query

`SEARCH_QUERY` shapes **only the YouTube source**; every other source (see *Sources*)
is taken as-is. It's passed to YouTube search with a simple syntax:

- `|` = OR: `beach|harbor|coast` matches any of them
- space = AND: `live cam` requires both words
- `-` = exclude: `-gaming -asmr` drops results that mention those terms

Examples:

```text
# Nature & wildlife
SEARCH_QUERY=animal|wildlife|bird|nature|zoo|aquarium|safari|live|cam -gameplay -gaming

# Transport
SEARCH_QUERY=train|railway|airport|harbor|traffic|ferry|live|cam -gameplay -gaming
```

Tips:

- **Less is more**: piling on terms tends to *narrow* results and make them worse,
  not wider. Start broad, then add a few exclusions.
- **Exclusions do the heavy lifting**: `-gaming -asmr -reaction`-style terms are the
  most effective way to filter out non-webcam noise.
- Leave `SEARCH_QUERY` unset to use the built-in default (a broad webcam query with
  sensible exclusions already baked in).

## Filtering by category

`EXCLUDE_CATEGORIES` drops whole categories (comma-separated, case-insensitive).
Unlike `SEARCH_QUERY` it applies to **every source**, filtering on the unified
category each cam is mapped to. For example, `EXCLUDE_CATEGORIES=Religion,Sports` drops
every religion and sports cam regardless of which source it came from.

The available categories are:

```text
Airports, Animals, Aquariums, Bars & Nightlife, Beaches, Cities, Comedy, Education,
Entertainment, Film & Animation, Gaming, Hotels, Howto & Style, Landmarks, Mountains,
Music, Nature & Parks, News & Politics, Nonprofits & Activism, Other, People & Blogs,
Ports & Ships, Religion, Science & Technology, Seasonal, Space, Sports, Studios,
Traffic, Trains & Railways, Travel & Events, Unmapped Category, Water & Waterways,
Weather
```

`Gaming`, `Film & Animation`, `Howto & Style` and `Comedy` come from YouTube's own
taxonomy and are the usual culprits for junk sneaking through the search — live game
streams, 24/7 cartoon loops, lottery-draw channels. `EXCLUDE_CATEGORIES` is the reliable
way to drop them; `SEARCH_QUERY`'s `-gaming`-style exclusions only filter on title text,
so they miss anything that doesn't say so in its title.

`Other` is for cams a source left uncategorised *and* whose title gave no usable hint —
when a source provides no category, the title is checked for a keyword (a species,
`harbour`, `beach`, `ski`…) or a `City, Country`-style location (→ `Travel & Events`)
before falling back to `Other`. `Unmapped Category` is different — it flags cams whose
source *did* give a category we don't recognise yet, so a missing mapping is visible (and
logged when its source refreshes) instead of hidden in `Other`.

## Upgrading from v2

v3 refreshes each source on its own schedule instead of rebuilding everything at once,
which is a **breaking change** if you tune or monitor the refresh. A pinned `:v2` keeps
running but gets no more updates.

- **Removed:** `CATALOGUE_INTERVAL_HOURS` (it warns if still set). Each source has its
  own interval, and `SOURCE_INTERVAL_HOURS` (`name=hours,…`) overrides any of them (see
  *Configuration*).
- **`/health`:** `last_build_seconds` is gone. Each source reports `last_refresh_at`,
  `last_refresh_seconds` and `interval_hours` instead, and a source whose last refresh
  finished more than twice its interval ago is `overdue`, which counts as unhealthy
  (see *Monitoring*).
- **Playlist:** it serves from the first finished refresh and grows as the others land,
  instead of waiting for every source.
- **New: a `/data` mount.** Mount a host directory at `/data` so a restart serves at
  once instead of starting cold (see *Persistent data*). Without one the app still
  runs, and says so at startup.

## Upgrading from v1

v2 is a ground-up rewrite and a **breaking change**. Images are tagged `:latest`,
`:v<version>`, and `:v<major>`. A pinned `:v1` keeps getting v1.x untouched, but
if you track `:latest` you'll move to v2. To migrate your existing config:

- **Set `PUBLIC_BASE_URL`** to the address your player actually reaches (see
  *Exposing it*). v1 didn't need it; v2 builds the `/stream/<id>` URLs from it, so
  leaving it unset points the playlist at `localhost` and nothing plays.
- **Renamed:** `EXCLUDED_CATEGORIES` → `EXCLUDE_CATEGORIES` (note: no `D`). The v2 version filters
  **all** sources on the unified taxonomy, not YouTube's category names, so update the
  values to the v2 category names (see *Filtering by category*); old names like
  `Gaming` no longer exist (use `SEARCH_QUERY` exclusions like `-gaming` instead).
- **Removed (ignored, with a startup warning, if still set):** `MAX_VIDEOS_PER_CYCLE`,
  `CONCURRENT_EXTRACTIONS`, and `UPDATE_INTERVAL_HOURS` — each source now refreshes on
  its own interval, which `SOURCE_INTERVAL_HOURS` (`name=hours,…`) overrides.
- **Unchanged:** `YOUTUBE_API_KEY`, `SEARCH_QUERY`, `LOG_LEVEL`, and the `23457:8000`
  port mapping.

The catalogue is now multi-source (YouTube plus the directories listed under *Sources*)
and streams resolve on demand, so expect a different, much larger channel list. To stay
on v1, pin the image to a `:v1` tag instead of `:latest`.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for local setup, the test suite, and the
project structure. Built test-first; run the checks with
`uv run pre-commit run --all-files` and `uv run pytest`.

## Security note

The on-demand stream proxy validates and signs the URLs it will fetch and refuses to
reach private/loopback addresses, but this is a self-hosted tool, so put it behind your
own reverse proxy / network controls rather than exposing the raw port to the internet.

Each image the release workflow publishes carries signed build provenance, so you can
check that this repo's build workflow made it before running it:

```bash
gh attestation verify oci://ghcr.io/mrgeetv/live-webcam-aggregator:v3 \
  --repo mrgeetv/live-webcam-aggregator \
  --signer-workflow mrgeetv/live-webcam-aggregator/.github/workflows/docker-build.yml
```

Found a vulnerability? Report it privately, as [SECURITY.md](SECURITY.md) describes.
