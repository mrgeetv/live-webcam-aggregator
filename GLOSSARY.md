# Live Webcam Aggregator

Gathers live webcams from many sites into one playlist, and serves each cam on demand.

Terms marked _(planned)_ name parts of the per-source refresh and store model in #214 that the code does not have yet. Until the ticket named beside each one lands, the code and the other docs still use the words that term avoids.

## Language

**Source**:
One site or feed that cams are gathered from.
_Avoid_: provider, scraper (a source need not scrape)

**Catalogue**:
The merged, deduplicated set of live cams the playlist is rendered from.
_Avoid_: snapshot, index

**Refresh**:
One pass over a single source: discover its cams, then check which are live.
_Avoid_: build, rebuild, cycle (those name an all-sources-at-once pass)

**Interval**:
How long a source waits between refreshes.
_Avoid_: period, schedule

**Budget** _(planned, #220)_:
The most cam pages a source fetches fresh in one refresh; the rest reuse what was learned before.
_Avoid_: limit, quota (quota is a site's own cap on us)

**Index**:
A source's list of its cam pages (a sitemap, a listing page, a feed, windy's grid scan), read before any cam page is fetched. It may carry details about each page, such as a name or a last-modified date.
_Avoid_: corpus, sitemap (one kind of index), catalogue (that is the merged output)

**Page record** _(planned, #220)_:
What a source learned from one cam page: the cams on it, or that it has none.
_Avoid_: cache entry, cached page (the page itself is never kept)

**Kept set**:
The cams a source's latest accepted refresh found live: what that source contributes to the catalogue.
_Avoid_: last good set, snapshot

**Store**:
The place on disk where what each source learned survives a restart: its kept set, its page records, when it last refreshed. Everything in it can be learned again; losing it costs time, never correctness.
_Avoid_: database, cache (the page records are one part of it)

**Storeless**:
Running with a throwaway store that is discarded at restart, because the real one can't be used (written by a newer release, or its location isn't writable). Everything works; nothing learned survives a restart.
_Avoid_: offline, degraded (that is a source status)
