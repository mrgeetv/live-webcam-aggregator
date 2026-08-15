# Project: Live Webcam Aggregator (multi-source, v2)

> **Note**: CLAUDE.md is a symlink to this file. Edit AGENTS.md only.

## Development Context

@DEVELOPMENT.md

**Every change must leave the repo better than it found it.** Fix what you touch —
don't bolt new code onto known-bad old code, and don't leave a wart you noticed for
the next person. (Surfacing a genuinely separate fix for a decision is fine; silently
skipping it is not.)

## Architecture & Extension Points (v2)

The app is two phases, decoupled by a catalogue snapshot:

1. **Catalogue build** (`catalogue.py`, every `CATALOGUE_INTERVAL_HOURS`): sources run
   **concurrently** (discover + liveness), capped at `MAX_PARALLEL_SOURCES` so total
   build concurrency stays ~cap × `SCRAPE_WORKERS` no matter how many sources exist, and
   a source that crashes is isolated (reuses its last good set, the rest proceed). Each
   `Source.discover()` yields `Candidate`s → liveness filter (YouTube via the Data
   API batch; everything else via a **fetch-verified probe** — `make_liveness_check`
   actually fetches the HLS manifest and drops dead/404 and DASH cams; probed **once
   per distinct `target_url` across sources** — two sources carrying the identical URL
   share one probe and the verdict counts in each's stats. The key is `target_url`, NOT
   `predisc_key`: the merge identity is lossy (strips tokens, collapses feratel URL
   shapes), so sharing a verdict across it would apply one URL's result to a different
   one) → per-source
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
   fetches the whole chain itself; `_PROXY_SEGMENT_HOSTS` (balticlivecam, enhd.es,
   skylinewebcams, earthcam, wetmet, iol.pt/beachcam, hdontap, ozolio, webcamera.pl)
   get their **segments relayed** through `/stream/<id>/s?u=…&sig=…`.
   **YouTube cams 302-redirect straight to googlevideo by default** (`PROXY_YOUTUBE`
   off): lower latency / less buffering on shallow live windows, but playback stops
   when the ~6h googlevideo token expires. `PROXY_YOUTUBE=true` proxies them like the
   rest (survives expiry via re-resolve), and proxied **DVR** playlists are trimmed to
   the live edge (`truncate_to_live_edge`) so we never relay the multi-MB rewind
   buffer (the manifest fetcher uses `MANIFEST_MAX_BYTES`, not the 8 MB default).

**`build_app()` in `app.py` is the wiring seam.** To extend:

- **Candidates are normalised at the extraction seam** (`extract_candidates`): a
  site-relative embed is resolved against its page (worldcams' `streams[]` array mixes
  absolute iframes with paths), and a still-image target is dropped outright — a JPEG
  "cam" can never be a stream, and counting it as `no-extractor` buries the hosts that
  genuinely need one. `_NEVER_A_STREAM_HOSTS` drops the same class of noise for
  analytics/consent iframes (googletagmanager) that sit beside a page's real player.
  That denylist is ONLY for hosts serving no video at all — an embed we simply can't
  play yet (ivideon, rtsp.me, angelcam) stays a `no-extractor` drop on purpose, so the
  report keeps listing real gaps rather than hiding them.
- **Add a source** — implement the `Source` protocol (`sources/base.py`): a `name`
  and `discover() -> Iterable[Candidate]`. HTML scrapers subclass `HtmlScraperSource`
  (`sources/base.py`) and implement three hooks — `_page_urls()` (the cam detail-page
  URLs), `_page_meta(html, url)` (per-page `(category, ctx)`), and `_title_for(cand,
  url, category, ctx)` — the base owns the concurrent fetch + the extraction ladder.
  Override `_candidates(html, url)` too when a site's embeds aren't in the standard
  ladder (e.g. Skyline's Clappr token / a `videoId` JS var → a page or watch URL).
  Add the instance to `active_sources` in `build_app`. Set `Candidate.predisc_key` so
  dedup can merge it (`yt:<id>` for YouTube, `hls:<normalised>` for direct m3u8,
  `None` = never merged). Add the site to the README *Sources* table and the module
  to DEVELOPMENT's project-structure tree in the same change.
- **Add an extractor** — implement the `Extractor` protocol (`extractors/base.py`):
  `resolve(target_url) -> Resolved(url, stream_type, ttl_seconds)`. Add it to the
  `_extractor_set` factory in `build_app` AND a predicate to `build_registry`
  (startup validation raises if a rule names an extractor not in the dict). The one
  factory builds TWO sets: the build-time one (its fetcher is paced + stats-wired,
  because liveness re-fetches source sites en masse) and the serve-time one (unpaced
  — a build-inflicted cooldown must never stall a player's `/stream` resolve, which
  holds a `ResolveCache` per-entry lock). Never hand-maintain the sets separately.
  If the CDN's tokens are IP-bound, ALSO add its host to `_DIRECT_PLAYBACK_HOSTS`
  (passthrough) or `_PROXY_SEGMENT_HOSTS` (segment relay) in `serving.py`, or
  segments will 403. Three rules for the extractor body: raised error messages
  carry NO URL (they feed the aggregated `resolve-failed` detail at INFO — a URL
  leaks paths/tokens and shatters one failure mode into per-prefix buckets when the
  detail truncates; liveness already logs the URL at DEBUG); `ttl_seconds` must sit
  BELOW the upstream token's real lifetime (`ResolveCache` serves the entry for
  ttl × 0.8, and `None` means ~8 min — a too-long TTL hands players a token that
  lapsed while nobody watched; short is cheap, a re-resolve is one fetch on the
  unpaced serve path); and if the page or CDN gates on `Referer`, add the host to
  `_REFERER_HOSTS` in `fetch.py` (a missing Referer can 403 — or worse, serve a
  decoy 200).
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
- **Diagnostics come free** — failures aggregate at two seams, so a new source or
  extractor is diagnosable without extra work. Each source gets its **own `Fetcher`
  carrying its own `FetchStats`** (wired in `build_app._source_fetcher`; the keys must
  match `Source.name` and a startup check raises if they don't), and `catalogue`
  reports that source's fetch outcome on its own `kept / discovered` line. Per-source,
  NOT global: a fully blocked source makes very few requests (skyline: 11), so a global
  "top N failing hosts" view ranks it below healthy sources' routine dead-cam probes —
  the worse the outage, the better it hides. The second seam is the liveness probe,
  which returns a **reason** (`models.NO_EXTRACTOR` / `RESOLVE_FAILED` /
  `DEAD_MANIFEST` / `YT_OFFLINE`, optionally `":<detail>"`) that `filter_source`
  counts. Log **hostnames** in aggregates; full URLs only via `serving.loggable()`,
  which keeps the query string at DEBUG only — and the `resolve-failed:<detail>`
  strings have embedded URL query strings stripped (`app._URL_QUERY`) before they
  reach the INFO aggregate, since an extractor error often quotes its target URL,
  token and all — and the shared resolver error messages (`app._resolver_get` /
  `_baltic_post`) deliberately carry NO URL, so one failure mode aggregates as one
  detail bucket instead of thousands of URL-prefix ones. A third seam covers what
  the per-source stats can't see: the cross-source liveness fetchers (resolver +
  probe) carry their own named `FetchStats`, and the pacer counts per-host
  penalties/backoff — both drained per rebuild into one INFO line each. All of this
  detail is LOG-ONLY: `/health` stays a compact WHAT summary (per-source
  `kept`/`discovered`/`crashed`/`status`, plus `last_build_seconds`).
- **Add a config/env var** — parse it in `config.py` via the `_*_env` helpers, and
  ALWAYS validate: a bad/unparseable value must log a `WARNING` at startup and fall
  back to the default, never crash or silently misbehave (e.g. `_int_env` warns on a
  bad int, `_bool_env` on a non-`true`/`false`; `_warn_on_suspect_config` flags
  dubious-but-valid values). Document every new var in README + DEVELOPMENT +
  `.env.example`.

**Hard-won lessons (don't relearn these):**

- **Anything that changes the egress IP (a VPN, a new host, a proxy) can get a scraper
  blocked**, and the block does not look like an error. Two properties make it easy to
  miss, which is why the per-source fetch/liveness aggregation above exists: a **total
  block produces very few requests** (a source that can't read its index never reaches
  its cam pages — skyline manages 11 fetches), so anything ranking failures globally
  buries the worst outages; and a block **can return HTTP 200** (a challenge or consent
  page), so "0 failed" is not the same as "nothing wrong" — fetched-ok-but-extracted-
  nothing needs its own warning. A gradual decline rather than a cliff points at
  progressive challenge/rate-limiting rather than a hard IP ban.
- **A build can rate-limit itself off its biggest source.** A source's whole worker
  pool aims at ONE host, and the liveness phase then re-fetches the same site through
  the resolver at zero delay — the site sheds load (429/503) and every shed response
  is a cam silently missing from the playlist, with the resolver phase (not the polite
  scrape) as the bigger offender. Every build-side `Fetcher` therefore shares a
  reactive `fetch.HostPacer`: idle until a host sheds, then per-host spacing plus a
  growing cooldown that gates the RETRY past the shedding window (that recovery is the
  whole mechanism — a retry that fires straight back into the burst just loses the cam
  twice), escalating to a fail-fast breaker (`host-backoff` outcome — on the SOURCE's
  log line for the scrape phase, on the cross-source `liveness` line for the
  resolver/probe phase) so a hard-blocked host costs minutes, not an unbounded
  build. Three scope rules that must survive refactors: serve-time fetchers stay
  UNPACED (a build-inflicted cooldown must not stall playback — hence the
  build/serve extractor-set split); `get_segment` is never
  paced; and yt-dlp/googleapiclient traffic doesn't pass through `Fetcher`, so the
  pacer doesn't cover it. Because a fetch that recovers on its gated retry records
  `ok`, the `pacing` penalty counters — not `http-503` counts — are the honest measure
  of how hard a host is shedding. The pacer holds nothing while a thread waits
  (sleep-only), which is why it may safely span the nested build pools.
- **An expired token usually answers 200, not 404 — with an EMPTY playlist.** Handed a
  lapsed or bogus token, a CDN commonly returns a well-formed HLS document listing
  nothing (skyline: `#EXTM3U` + `#EXT-X-ENDLIST`, zero segments, 109 bytes). Testing for
  the `#EXTM3U` header therefore reads a dead stream as live — the cam ships in the
  playlist and the player gets something it can never start. Both gates
  (`app.make_liveness_check` and `serving.serve_stream`) judge on CONTENT via
  `serving.is_playable_manifest`: a media playlist with `#EXTINF`, or a master playlist
  with `#EXT-X-STREAM-INF`. Accepting either matters — requiring segments alone would
  reject every master playlist, which is what most CDN top-level URLs return. This is
  the same failure shape as the rtsp.me stub that is dodged by skipping that host by
  URL; content-based detection catches the class rather than one domain at a time.
- **A wedged googleapiclient connection survives retries.** httplib2's `Http.request`
  evicts a pooled connection **only** on `socket.timeout` — a `BrokenPipeError` leaves
  the dead socket in `self.connections`, so every later call reuses it and fails
  instantly with no round trip, for the life of the client. `num_retries` on `.execute()`
  does NOT fix this: googleapiclient retries via `http.request()`, which pulls the same
  wedged connection back out of that dict. The Http object has to be **replaced** —
  hence `YoutubeApiSource` taking a client **factory** and calling `_drop_client()` on
  any failure (`build()` with `static_discovery=True` costs ~5 ms and no network). Note
  `videos.list` (1 unit) can succeed while `search.list` (100 units) fails, so "one
  YouTube call works" doesn't rule out quota.
- **Every env var the docs promise must be in `docker-compose.yml`'s `environment:`
  list**, or setting it in `.env` does nothing and silently falls back to the built-in
  default — `SEARCH_QUERY` and `EXCLUDE_CATEGORIES` were missing for months. Compose
  forwards `${VAR:-}` as an empty string when a var is absent, so `config._int_env`,
  `_bool_env` and `fetch.resolve_scrape_workers` all treat **blank as unset**, not as an
  invalid value to warn about.

- Route ipcamlive **`player/player.php` URLs and bare `www.ipcamlive.com/<alias>` share
  pages** to the resolver; direct `s*.ipcamlive.com/.../stream.m3u8` (the majority) must
  fall through to `DirectHls`, so the alias predicate is anchored to the apex/www host.
  A share page carries neither `address`/`streamid` nor a link to the player (it builds
  the player client-side), so the resolver rewrites it to
  `g0.ipcamlive.com/player/player.php?alias=<alias>` rather than scraping it.
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
  types: own Clappr HLS (`source:'livee.m3u8?a=<token>'`, resolved at serve-time by
  `SkylineResolver` to `hd-auth.skylinewebcams.com`) and "from the web" YouTube
  (`videoId:'…'` → watch URL, dedups via `yt:`). Names come from the **breadcrumb**
  (English geo), not the URL path (native: italia/espana).
  The Clappr token is **not** regenerated per page-load (an earlier note here said it
  was): the same page returns the same token string for at least tens of minutes. What
  lapses is its AUTHORISATION — left unused it stops working in ~1-2 minutes (measured:
  alive at 60s, dead by 150s across several cams), and re-fetching the page is what
  re-arms it. So serve-time must still re-resolve the page, its `Resolved.ttl_seconds`
  must stay under that lapse (60 → ~48s cached), and a discovery-time token is useless
  to the liveness phase minutes later — which is why skyline's cam pages are
  deliberately fetched twice per build and that cost is not a bug to optimise away.
- camscape aggregates from many providers — the cam page's `"streams":[{…}]` JSON is the
  source of truth (the rendered iframe shows only the active angle), one candidate per
  stream `url`. Most route to existing extractors (YouTube, ipcamlive, m3u8, feratel);
  plus a bespoke `earthcam` extractor (fetch page → grab its `.m3u8`; EarthCam 403s
  without a Referer, see `_REFERER_HOSTS` in `fetch.py`) and a twitch normalise
  (`player.twitch.tv/?channel=X` → `twitch.tv/X` → yt-dlp). ivideon (WebRTC), rtsp.me
  (server fetch gets only stub manifests, segments 404) + angelcam (auth) are
  unservable, dropped. Category + location come from the cam page's
  `/showing/<cat>` + `/location/<loc>` tags.
  **camsecure embeds are resolved to their `.m3u8` during discovery**
  (`_camsecure_hls`, reusing `sources.camsecure`'s `player_iframe` /
  `hls_from_player`; a cam-page embed costs one extra hop to its player). Do this at
  DISCOVERY, not via a serve-time extractor: the m3u8 is the `predisc_key` both
  sources share, so resolving early is what lets dedup merge camscape's copy with
  the camsecure source's instead of shipping the cam twice — and the m3u8 is NOT
  derivable from the embed URL (`cityair1.html` → `cityair.m3u8`,
  `portland_harbour_webcam.html` → `weymouthsailing.m3u8`, sometimes a different
  camsecure host). Worth the hops because camscape sees white-label camsecure
  players for third parties that are absent from camsecure's own sitemap, so that
  source can never reach them. An embed that resolves to nothing keeps its original
  URL and stays a visible `no-extractor` drop.
- camscape/cxtvlive also embed **wetmet** widgets (`api.wetmet.net/widgets/stream/frame.php?uid=`);
  the frame's inline script assigns the master playlist to `var vurl`, so `WetmetResolver`
  just reads it. Wowza/Nimble signs it (`wmsAuthSign` + a `nimblesessionid` minted for
  whoever fetched the manifest) and both propagate into the segment URLs — a segment
  stripped of its query 404s — so `wetmet.net` is in `_PROXY_SEGMENT_HOSTS` and the
  `Resolved` carries a short TTL rather than being cached indefinitely.
- Re-probed and still unservable, so their candidates are expected `no-extractor` drops:
  **ivideon** (`open.ivideon.com/embed/v3`, WebRTC — no HLS in the embed),
  **rtsp.me** (builds the stream URL in a JS bundle at runtime; a server fetch gets only
  stub manifests whose segments 404), **angelcam** (`v.angelcam.com/iframe`, JWT-gated),
  **video.nest.com** (exposes only `nexusapi…/get_image`, a still JPEG, not a stream),
  **console.rhombussystems.com** and **surfline** (403 without a Referer, 404 with).
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
- livespotting.tv (~135 DACH/Adriatic cams): hardcoded country pages list cam ids, each id's
  `player.livespotting.com/v2/livesource/<id>?type=hub` JSON carries a **stable, tokenless**
  `source` HLS on `cdn.livespotting.com` → `DirectHls`, `hls:` predisc key. The edge appends
  a per-request `?session=` to child playlists but segments work without it (viewer
  analytics, not auth). German names — the title-keyword fallback mostly misses them.
- livefromiceland.is (~30 cams): WP `webcam` post-type sitemap → cam pages whose ipcamlive
  iframe is **LiteSpeed lazy-loaded** (`src="about:blank"`, real URL in `data-litespeed-src`),
  so the ladder's iframe rule can't be trusted — a one-regex `_candidates` override greps the
  `g0.ipcamlive.com/player/player.php?alias=` URL straight from the HTML.
- beachcam.meo.pt (~190 Portuguese cams, blanket category **Beaches** — the few
  lake/wake-park/city outliers beat ~190 Portuguese titles the English keyword fallback
  would dump in "Other"): the page's `data-video-url` m3u8 (video-auth1.iol.pt)
  answers **403 bare** — the JW player appends `?wmsAuthSign=<token>` from
  `services.iol.pt/matrix?userId=` (anonymous, ~24h validity), so the source fetches ONE
  token per build and bakes it into `target_url` (predisc key stays on the BARE url for
  cross-build stability). Child/segment URLs carry a per-manifest-fetch Nimble session →
  `video-auth1.iol.pt` in `_PROXY_SEGMENT_HOSTS`. Raising `CATALOGUE_INTERVAL_HOURS` past
  ~24 outlives the token; an empty-guard-preserved stale set carries a lapsing one. The
  ladder is bypassed (`_candidates` override) because every page footer links the site's
  YouTube channel, which the channel rule would emit as a bogus extra candidate per page.
- resortcams.com (~100 US-Southeast cams): WP sitemap → pages with an **open** Wowza HLS
  (`stream.resortcams.com`) in static HTML; fully tokenless chain, plain `DirectHls`, no
  host-list entries. Pages with no static player yield nothing and drop.
- hdontap.com (~200 cams): sitemap → `/stream/<id>/<slug>/` pages (id is the dedupe key —
  one cam can carry two slugs). A `<script id="player-data">` JSON blob marks a native-HLS
  cam: the candidate is the **page URL** (predisc None) and `HdontapResolver` re-reads the
  blob at serve time because the `streamSrc` HLS carries a per-page-fetch `t`/`e` token pair
  (~13h; BOTH params required — a URL clipped at the JSON-escaped `&` 403s, hence
  `json.loads`, never a regex over raw HTML). No blob → standard ladder (~25% are YouTube
  embeds). Token may be IP-bound → `hdontap.com` in `_PROXY_SEGMENT_HOSTS`.
- feratel.com (~1000 Alpine/European cams, blanket **Travel & Events**): the portal's
  `sitemap-0.xml` lists the `/en/webcams/<country>/<region>/<slug>` detail pages; the
  page's OWN cam is the lazy main-player iframe (`data-src`, the "nearby" carousel uses
  plain `src=` — an Astro-props scan is unreliable, some pages carry no props blob for
  their own cam). Candidates are rewritten to the canonical
  `webtv.feratel.com/webtv/?cam=<id>` (metatag/og:video path). **feratel serves
  panorama-sweep MP4s, not streams**: ~40MB/1080p clips a few minutes long, re-recorded
  every ~5-10 min per cam — an ended clip is NORMAL (players that reconnect to
  `/stream/<id>` replay/refresh it; measured: old clip URLs keep answering 206 after
  rotation, so mid-play rotation can't cut a viewer off). A still-image-only cam has
  og:video **metas** but no content URL → clean resolve-failed drop. `predisc_key` is
  `feratel:<cam id>` (in `base.predisc_key`): the id is the identity across the
  webtv/webtvfc URL shapes third-party embeds carry, which is also why the registry
  predicate is `.feratel.com/webtv/` (webtvfc pages serve the same og:video; a
  `webtvfc` URL WITHOUT the portal's `pg=` param resolves to the per-clip GUID form —
  fine — while `/streams/latest/` URLs are avoided: that endpoint 400s Range requests,
  which some players need).
- ozolio.com (~190 cams, mostly Hawaii): Yoast cameras-sitemap → `/explore/<CID>` pages;
  discovery scrapes only the `<title>` (the player is built client-side). `OzolioResolver`
  does the relay's 2-call session dance (`ses.api?cmd=init` → `cmd=open`); the gate is the
  **`document=` query param, not Referer/Origin headers** — plain resolver fetch works.
  Filter `media != "LIVE"` / non-`/hls-live/` sources: those are canned media-library loops
  ("ROLL"), not cameras. Relay host rotates per open and the Wowza session is minted for
  whoever fetches the manifest (the wetmet shape) → short TTL (180s) + `ozolio.com` in
  `_PROXY_SEGMENT_HOSTS`.

- whatsupcams.com (~1450 Adriatic cams): Yoast `webcams-sitemap*.xml` → cam pages (the
  JSON-LD `embedUrl`'s `/wgt/<id>/` is the cam id — the snapshot thumbnails also carry
  ids but include the "nearby cams" strip's, so never anchor on those) → per-id
  `cdn-api/streams/<id>` JSON whose `hls.url` is a **stable, tokenless, open** m3u8
  (`DirectHls`, `hls:` key; no Referer/session anywhere — verified to the segment).
  The cdn-0NN host varies per cam, so the API call is the only id→URL mapping. The
  tempting `/cdn-api/streams/` list endpoint returns a random sample — unusable.
- viewsurf.com (~1000 French cams): the sitemap is the index AND the category seam
  (`/univers/<slug>/vue/<id>-…`; a cam repeats once per univers → dedup on id,
  preferring a mapped univers; unknown slugs log once and pass through raw). Target =
  the canonical joada embed (`platforms5.joada.net/embeded/embeded.html?uuid=…&type=…`,
  presentation params stripped); `ViewsurfResolver` GETs
  `platforms8/9.joada.net/api/videos/manifest/<uuid>` (the player rotates platforms6-12
  but several are dead/expired-cert) → open HLS, no token/Referer, but the delivery
  host is the API's per-call choice → resolve at serve time, moderate TTL. `type=vod`
  cams are ~60s clips re-encoded every few minutes (ENDLIST — plays then stops, the
  feratel/player-reconnect behaviour); "Panoramique HD" pages are photo cams and
  expected zero-candidate drops.
- webcamera.pl (~600 Polish cams): enumeration is the `/mapa` `MAP_MARKERS` JSON
  **unioned with** the five top-level `/kategoria,<slug>` listings — the map alone
  misses ~40% (most ski cams), and the listings carry the category (priority-ordered
  first-match-wins). The page's inline `"video_src"` is the HLS URL **ROT13-encoded**;
  decoded at discovery → `DirectHls`. Nimble mints a `nimblesessionid` per manifest
  fetch that must survive into segments (the wetmet shape) → `webcamera.pl` in
  `_PROXY_SEGMENT_HOSTS`. Playlist/PREMIUM compilation feeds are skipped (one shared
  rotating stream = dups; paywalled).
- airportwebcams.net (~700 pages): enumerate via the "by webcam speed" index's
  Streaming section (the full A-Z would add ~2000 still-image pages). Pages link each
  camera's **YouTube channel**, not a video id → one candidate per channel as
  `youtube.com/<channel>/live`, resolved to the current stream by yt-dlp; channels
  under the "Live streamers with regular broadcasts" heading are sometimes-live
  spotters, skipped unless the page has nothing else. A large
  `resolve-failed: channel is not currently live` share is NORMAL here (~40-60% kept),
  and each candidate costs a yt-dlp resolve — watch the yt-dlp bot-check bucket if
  build volume grows.
- its-i.com / Share-Ju (~475 Japanese cams): the ONE `/camera` index page carries a
  JSON-LD ItemList of every cam (name, YouTube embedUrl, page url, JP address, tags in
  the description) — one fetch covers the site; detail pages are JS-built and
  useless. `json.loads(strict=False)` (the blob has a literal newline in a string).
  Titles stay Japanese; the geo suffix is built by hand because `base._norm` strips
  non-ASCII, so `with_location_parts` would drop Japanese parts entirely. Tags map via
  `_TAG_CATEGORY` (guarded by a test against `categories._MAP`); town/scenic tags stay
  unmapped on purpose — the geo title suffix files those under Travel & Events.
- windy.com (meta-aggregator, ramps to ~thousands): the **keyless internal API**
  (`node.windy.com/webcams/v2.0/list?nearby=<lat>,<lon>&radius=250`, limit 25, offset
  paginates; no key/UA/Referer needed anywhere) enumerates the ~65k-cam corpus via a
  ~350 km grid scan, but carries **no live flag** — a cam's live-ness is only visible
  on its stream page (`webcams.windy.com/webcams/stream/<id>`: ~170-byte stub = not
  live, else a static wrapper with ONE entity-encoded iframe = the original provider's
  embed, ~85% servable by existing extractors and dedup-keyed by the shared predisc
  rules). One build can't probe 65k pages politely, so `WindySource` keeps state
  across rebuilds (sources live for the process lifetime) and spends a ~9k-request
  budget per build: known-live re-probed first, the rest in rotating slices — coverage
  RAMPS over ~2 days and resets after a restart; growing counts are normal, not an
  outage. The API is internal and unversioned: if windy gates it, the per-source
  stats/status seam is what will say so.

**Security model:** every outbound fetch is validated by `fetch._validate_ip`
(rejects non-http(s) and private/loopback/link-local/reserved IPs; `_resolve_validated_ip`
is a thin wrapper for callers that don't need the rejection reason), an 8 MB cap, and
**per-hop redirect re-validation** in the `requests` `Fetcher`; proxied `/m` and `/s`
URLs are HMAC-signed (`signing.py`) so only server-emitted URLs are fetched.
**DNS-rebinding TOCTOU is now closed in-app** (no firewall needed): the `Fetcher`
resolves+validates the host once (`_validate_ip`) and then **pins the DNS
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
The rejection **reason** (`bad-scheme`/`dns-error`/`blocked-ip`) comes from that one
lookup, never a second one — re-resolving to label a failure would be slow on the
dead-domain long tail and, on flaky or round-robin DNS, could report a genuine
unsafe-IP block as a transient `dns-error`, losing the only signal that says the guard
fired.
**Credentials must never reach the logs.** googleapiclient puts the developer key in
the request URI and `HttpError.__str__` (which IS its `__repr__`) prints that URI — so
`log.exception` on any Data API failure writes `YOUTUBE_API_KEY` to disk (a traceback
ends in `str(exc)`). Never `log.exception` a googleapiclient error, and never `%s` one raw:
log `type(exc).__name__` plus `logging_redaction.scrub(str(exc))`. `RedactingFilter`
(installed on the root **handler** in `main()`, because a logger's filters only see
records logged directly on it and every module uses a child logger) is the backstop,
not the primary defence — fix the call site too.

**Tests:** files are `*_test.py` (the `name-tests-test` hook rejects `test_*.py`).
`tests/repo_hygiene_test.py` guards what ships in a public repo: shipped source must
carry no dates, no named operator infrastructure (VPNs, uptime tooling, the edge
proxy), and nothing matching the real Google-API-key shape — state the hazard in a
comment, never the incident.
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
- `chore` - Maintenance tasks — **listed in the changelog** under *Maintenance*
  (the only visible non-release type; it is how dependabot's `chore(deps)` action
  bumps get recorded, since they ship in an image without cutting a release)
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
