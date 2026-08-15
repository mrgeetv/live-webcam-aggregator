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
| beachcam.meo.pt | Portuguese beach and surf cams |
| camscape.com | Aggregator directory — pulls in cams from many third-party providers |
| camsecure.co.uk | Directory of hosted webcams |
| cxtvlive.com | Camera directory |
| earthcam.com | EarthCam's own cams plus its partner network |
| explore.org | Nature and wildlife cams |
| hdontap.com | US streaming provider — beaches, piers, wildlife nest cams |
| livefromiceland.is | Iceland — volcanoes, glaciers, aurora, Reykjavík |
| livespotting.tv | German and Alpine coastal, harbour and town cams |
| ozolio.com | Hawaii and US resort/beach cams |
| resortcams.com | US Southeast ski, mountain and beach-town cams |
| skylinewebcams.com | Camera directory |
| wildlifetrusts.org | UK regional wildlife-trust cams |
| worldcams.tv | Camera directory |
| YouTube Data API | Live webcam broadcasts found by search |

The same camera found on more than one source is merged into a single channel. A
typical build lands around **5500–6500 live channels**, varying with how many cams are
actually up and with `EXCLUDE_CATEGORIES`.

## How it works

- **Catalogue build** (periodic): every `CATALOGUE_INTERVAL_HOURS`, each source is
  crawled, dead streams are dropped, survivors are de-duplicated, mapped to a unified
  category, and written into a playlist of stable internal URLs. This is the slow
  part — expect **20–30 minutes** for a ~5700-cam catalogue. It's dominated by
  *waiting*, not work: sources are crawled concurrently, and a site that rate-limits
  (429/503) is backed off per host, so the build's wall-clock is set by the most
  rate-limited source. That makes it depend on your egress IP far more than on your
  hardware, and it's why raising `SCRAPE_WORKERS` doesn't reliably speed it up.
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

Pull and run the published image:

```bash
docker run -d --name webcams \
  -p 23457:8000 \
  -e YOUTUBE_API_KEY=your_key_here \
  -e PUBLIC_BASE_URL=http://localhost:23457 \
  ghcr.io/mrgeetv/live-webcam-aggregator:v2
```

Or with a minimal `docker-compose.yml` (uses the published image, no build):

```yaml
services:
  webcams:
    image: ghcr.io/mrgeetv/live-webcam-aggregator:v2
    ports: ["23457:8000"]
    environment:
      YOUTUBE_API_KEY: your_key_here
      PUBLIC_BASE_URL: http://localhost:23457
    restart: unless-stopped
```

The playlist is then available at `http://localhost:23457/playlist.m3u8`. The first
catalogue build takes **20–30 minutes** (discovery + liveness checks across every
source); until it's ready, `/playlist.m3u8` returns `503`.

Set `PUBLIC_BASE_URL` to the address your players actually reach; `localhost` only
works for a player on the same machine (see *Exposing it*). These use the `:v2` major
tag, so you get v2.x updates but never an automatic jump to a future breaking major
(`:latest` would); see *Upgrading from v1*. Want to build from source instead? See
[DEVELOPMENT.md](DEVELOPMENT.md).

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
| `/health` | JSON status: readiness, `healthy` rollup, per-source outcome, build duration, memory |

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
    "skyline": {"kept": 0, "discovered": 0, "crashed": false, "status": "dead"}
  },
  "last_build_seconds": 1187.3,
  "rss_mb": 1402.6
}
```

Fields:

- `ready` — the first catalogue build has completed (the playlist is serveable).
- `healthy` — `ready` **and** no source crashed or returned 0 cams on the last rebuild.
- `unhealthy_sources` — the sources that failed this cycle (the reason `healthy` is `false`).
- `sources.<name>` — the **raw** result of the last rebuild: `discovered` (found),
  `kept` (passed liveness), `crashed` (`discover`/liveness threw). These stay raw even
  when the empty-guard is still serving a failed source's last good set, so a dying
  source surfaces immediately.
- `sources.<name>.status` — `ok`, `degraded` (collapsed, guard serving the last good
  set), `dead` (0 cams or crashed), or `unknown` before the first rebuild.
- `last_build_seconds` — wall-clock duration of the last catalogue build attempt
  (`null` until the first attempt finishes).

The payload answers *what happened*; the *why* — per-host fetch outcomes, drop
reasons, hosts with no extractor, pacing/backoff counters — is on the log lines each
rebuild writes (see `DEVELOPMENT.md`, *Catalogue & resolve logging*).

`/health` carries operational detail, so keep it off the public internet — expose only
`/playlist.m3u8` and `/stream/*` at your reverse proxy.

The states you'll see:

| `ready` | `healthy` | `unhealthy_sources` | Meaning |
| ------- | --------- | ------------------- | ------- |
| `false` | `false` | `[]` | Cold start — first build still running, nothing served yet |
| `true` | `true` | `[]` | All sources succeeded |
| `true` | `false` | non-empty | Serving, but a listed source crashed or returned 0 (`crashed` tells which) |

The cold-start build reads as `healthy: false`, so give the monitor a couple of retries
to ride out normal restarts before it alerts.

**Basic HTTP-status monitoring** (no JSON query) is also possible via response codes:

- `/health` returns **200** whenever the process is up — even mid-cold-start — so a plain
  200 check only confirms the server is alive, not readiness or source health.
- `/playlist.m3u8` returns **503** until the first catalogue build completes, then **200**.
  An HTTP-status monitor on it catches a down process and a stuck cold start, but not
  source-level degradation (a failed source still returns a served catalogue as `200`).

Use `$.healthy` for source-level alerting; the `/playlist.m3u8` status check is the
low-effort liveness option.

## Configuration

All via environment variables (see `.env.example`):

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `CATALOGUE_INTERVAL_HOURS` | `6` | Hours between catalogue refreshes (min 1) |
| `EXCLUDE_CATEGORIES` | (none) | Comma-separated categories to drop, across all sources, case-insensitive. See *Filtering by category* |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_PARALLEL_SOURCES` | `4` | How many sources discover + liveness-check at once (min 1). Total build concurrency ≈ this × `SCRAPE_WORKERS`; extra sources queue |
| `PROXY_YOUTUBE` | `false` | `false` redirects players straight to YouTube (lower latency, but playback stops when YouTube's ~6h stream token expires — reselect to resume). `true` proxies YouTube through the server so it keeps playing past that, at a small latency cost |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Externally-reachable base for the emitted URLs |
| `SCRAPE_WORKERS` | `min(16, cpu×4)` | Per-source concurrency for scraping + liveness during the catalogue build (sources also run concurrently). Lower it to reduce peak build-time memory (at the cost of a slower build). A host that starts rate-limiting (429/503) is backed off adaptively per host, so the effective rate to that host can drop below what this implies |
| `SEARCH_QUERY` | built-in webcam query | YouTube search terms (`\|`=OR, space=AND, `-`=exclude) |
| `YOUTUBE_API_KEY` | (required) | YouTube Data API v3 key |

> **Resource usage:** budget **~4 GB** for the container. A ~5700-cam catalogue sits at
> **~1.5 GB** between builds and **peaks near 3 GB** for the first minute or two of each
> build, while every source is fetching and parsing pages concurrently — that discovery
> spike is the high-water mark, not a leak, and the rest of the build is close to flat.
> RSS does **not** fall back to a small baseline afterwards; ~1.5 GB is the steady state.
> Lower `SCRAPE_WORKERS` to cap the peak. Live memory is on `/health` (`rss_mb`).

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
logged at build) instead of hidden in `Other`.

## Upgrading from v1

v2 is a ground-up rewrite and a **breaking change**. Images are tagged `:latest`,
`:v<version>`, and `:v<major>`. A pinned `:v1` keeps getting v1.x untouched, but
if you track `:latest` you'll move to v2. To migrate your existing config:

- **Set `PUBLIC_BASE_URL`** to the address your player actually reaches (see
  *Exposing it*). v1 didn't need it; v2 builds the `/stream/<id>` URLs from it, so
  leaving it unset points the playlist at `localhost` and nothing plays.
- **Renamed:** `UPDATE_INTERVAL_HOURS` → `CATALOGUE_INTERVAL_HOURS`; and
  `EXCLUDED_CATEGORIES` → `EXCLUDE_CATEGORIES` (note: no `D`). The v2 version filters
  **all** sources on the unified taxonomy, not YouTube's category names, so update the
  values to the v2 category names (see *Filtering by category*); old names like
  `Gaming` no longer exist (use `SEARCH_QUERY` exclusions like `-gaming` instead).
- **Removed (silently ignored if still set):** `MAX_VIDEOS_PER_CYCLE`,
  `CONCURRENT_EXTRACTIONS`.
- **Unchanged:** `YOUTUBE_API_KEY`, `SEARCH_QUERY`, `LOG_LEVEL`, and the `23457:8000`
  port mapping.

The catalogue is now multi-source (YouTube plus the directories listed under *Sources*)
and streams resolve on demand, so expect a different, much larger channel list. To stay
on v1, pin the image to a `:v1` tag instead of `:latest`.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for local setup, the test suite, and the
project structure. Built test-first; run the checks with `pre-commit run --all-files`
and `pytest`.

## Security note

The on-demand stream proxy validates and signs the URLs it will fetch and refuses to
reach private/loopback addresses, but this is a self-hosted tool, so put it behind your
own reverse proxy / network controls rather than exposing the raw port to the internet.
