# Changelog

All notable changes to this project will be documented in this file. See [Conventional Commits](https://conventionalcommits.org) for commit guidelines.

## [2.10.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.1...v2.10.2) (2026-07-15)

### Bug Fixes

* **deps:** bump docker/login-action from 4.2.0 to 4.4.0 ([#95](https://github.com/mrgeetv/live-webcam-aggregator/issues/95)) ([37cb497](https://github.com/mrgeetv/live-webcam-aggregator/commit/37cb4976d090a9696bbbfe0450169445ab6a6675))
* **deps:** bump the python-dependencies group with 2 updates ([#96](https://github.com/mrgeetv/live-webcam-aggregator/issues/96)) ([8c00c63](https://github.com/mrgeetv/live-webcam-aggregator/commit/8c00c6325cfbfe754983d04da239e1aa5a4e0482))

## [2.10.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.0...v2.10.1) (2026-07-08)

### Code Refactoring

* **docker:** rename service youtube-streams to live-streams ([#94](https://github.com/mrgeetv/live-webcam-aggregator/issues/94)) ([6783f3b](https://github.com/mrgeetv/live-webcam-aggregator/commit/6783f3b28cf6d05288e63d0bf73eb0337ec82c19))

## [2.10.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.9.1...v2.10.0) (2026-07-04)

### Features

* **playlist:** expose per-source health rollup on /health ([e83bc01](https://github.com/mrgeetv/live-webcam-aggregator/commit/e83bc016fc0788453f998669d1deb4681829215c))

## [2.9.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.9.0...v2.9.1) (2026-06-26)

### Bug Fixes

* **deps:** bump actions/cache from 5.0.5 to 6.0.0 ([#87](https://github.com/mrgeetv/live-webcam-aggregator/issues/87)) ([821722f](https://github.com/mrgeetv/live-webcam-aggregator/commit/821722f8484b1208ff25491bfbf43e49c4855e5f))
* **deps:** bump actions/setup-python from 6.2.0 to 6.3.0 ([#86](https://github.com/mrgeetv/live-webcam-aggregator/issues/86)) ([f71bac4](https://github.com/mrgeetv/live-webcam-aggregator/commit/f71bac4fc932f1c6b3cf4449cdcb8e4abc1fa5eb))
* **deps:** bump google-api-python-client ([#88](https://github.com/mrgeetv/live-webcam-aggregator/issues/88)) ([072a5de](https://github.com/mrgeetv/live-webcam-aggregator/commit/072a5de03f4e351844af165c87c293b0f0ff59fe))

## [2.9.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.8.0...v2.9.0) (2026-06-23)

### Features

* **scraper:** recover categories for uncategorised cams from their titles ([#85](https://github.com/mrgeetv/live-webcam-aggregator/issues/85)) ([2b9f3ed](https://github.com/mrgeetv/live-webcam-aggregator/commit/2b9f3edac01f227fbbad91c3a405d5194bbe3952))

## [2.8.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.7.0...v2.8.0) (2026-06-23)

### Features

* **scraper:** add explore.org + Wildlife Trusts nature-cam sources ([#84](https://github.com/mrgeetv/live-webcam-aggregator/issues/84)) ([787463e](https://github.com/mrgeetv/live-webcam-aggregator/commit/787463e50872b28776490c85abbe1f7923dba088)), closes [#81](https://github.com/mrgeetv/live-webcam-aggregator/issues/81)

## [2.7.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.6.0...v2.7.0) (2026-06-23)

### Features

* **scraper:** add CamSecure source (~210 UK direct-HLS cams via sitemap) ([#83](https://github.com/mrgeetv/live-webcam-aggregator/issues/83)) ([2a81458](https://github.com/mrgeetv/live-webcam-aggregator/commit/2a8145841045bcaf93be3fcf0cdc4d6ad594dbeb))

## [2.6.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.5.0...v2.6.0) (2026-06-23)

### Features

* **scraper:** add EarthCam mapsearch source (+ fix Fetcher.post baltic) ([#82](https://github.com/mrgeetv/live-webcam-aggregator/issues/82)) ([396ce9f](https://github.com/mrgeetv/live-webcam-aggregator/commit/396ce9f1b3f8efe03d0acc9b873554f1a162c16c))

## [2.5.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.4.0...v2.5.0) (2026-06-21)

### Features

* **scraper:** surface unmapped source categories instead of burying them in "Other" ([#80](https://github.com/mrgeetv/live-webcam-aggregator/issues/80)) ([6137750](https://github.com/mrgeetv/live-webcam-aggregator/commit/6137750bf5ddd37d1bc4ca6c6bc9169949c9a956))

## [2.4.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.3.0...v2.4.0) (2026-06-21)

### Features

* **scraper:** add Camscape source (multi-angle aggregator) + EarthCam extractor ([#79](https://github.com/mrgeetv/live-webcam-aggregator/issues/79)) ([662dde4](https://github.com/mrgeetv/live-webcam-aggregator/commit/662dde424c8d0e05769a7984cd2acbb05249ff4e))

## [2.3.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.2.1...v2.3.0) (2026-06-21)

### Features

* **scraper:** add SkylineWebcams source (own HLS + curated YouTube) ([#78](https://github.com/mrgeetv/live-webcam-aggregator/issues/78)) ([1c5a06e](https://github.com/mrgeetv/live-webcam-aggregator/commit/1c5a06ee6958c5ea0d72a03be1ed269bc90a3dac))

## [2.2.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.2.0...v2.2.1) (2026-06-21)

### Performance Improvements

* **scraper:** build catalogue sources concurrently + add shared HtmlScraperSource base ([#77](https://github.com/mrgeetv/live-webcam-aggregator/issues/77)) ([f7322e2](https://github.com/mrgeetv/live-webcam-aggregator/commit/f7322e292c717f884f03aa5f4216d2146d32ffc3))

## [2.2.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.1.2...v2.2.0) (2026-06-21)

### Features

* **playlist:** default youtube to direct playback; add PROXY_YOUTUBE toggle ([#76](https://github.com/mrgeetv/live-webcam-aggregator/issues/76)) ([eaf58b6](https://github.com/mrgeetv/live-webcam-aggregator/commit/eaf58b6e5f3a8c977f2ccab9922139a9f924b85c))

## [2.1.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.1.1...v2.1.2) (2026-06-21)

### Bug Fixes

* **playlist:** serve the live edge of DVR youtube manifests (was 502) ([#75](https://github.com/mrgeetv/live-webcam-aggregator/issues/75)) ([7d1b310](https://github.com/mrgeetv/live-webcam-aggregator/commit/7d1b310585d34a82b89f57dcd031d44164057691))

## [2.1.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.1.0...v2.1.1) (2026-06-21)

### Code Refactoring

* **scraper:** drop the redundant category from the cam-name location suffix ([#73](https://github.com/mrgeetv/live-webcam-aggregator/issues/73)) ([4ac5a0c](https://github.com/mrgeetv/live-webcam-aggregator/commit/4ac5a0c002fb40cb9c5220d06f2cdcac5c2fdbfa))

## [2.1.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.0.2...v2.1.0) (2026-06-21)

### Features

* **scraper:** per-cam names on worldcams multi-stream pages; proxy enhd.es segments ([#72](https://github.com/mrgeetv/live-webcam-aggregator/issues/72)) ([49cdf95](https://github.com/mrgeetv/live-webcam-aggregator/commit/49cdf95cd19cc6f778961cee24a46564c299286f))

## [2.0.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.0.1...v2.0.2) (2026-06-20)

### Code Refactoring

* **config:** remove the unused PORT env var ([#71](https://github.com/mrgeetv/live-webcam-aggregator/issues/71)) ([cc7a218](https://github.com/mrgeetv/live-webcam-aggregator/commit/cc7a2188937b5f358147bf415a84dbc2afd63db5))

## [2.0.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.0.0...v2.0.1) (2026-06-20)

### Bug Fixes

* **docker:** correct stale image description label after rename ([#70](https://github.com/mrgeetv/live-webcam-aggregator/issues/70)) ([018662c](https://github.com/mrgeetv/live-webcam-aggregator/commit/018662c2bb92f8248a8bd008db0c0e63f0702f8e))

## [2.0.0](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.23...v2.0.0) (2026-06-20)

### ⚠ BREAKING CHANGES

* **playlist:** replaces the v1 YouTube-only direct playlist with a multi-source aggregator (YouTube Data API + worldcams.tv + cxtvlive.com). The playlist serves stable /stream/<id> URLs resolved on demand via an HLS manifest proxy; new config (PUBLIC_BASE_URL, SEARCH_QUERY, CATALOGUE_INTERVAL_HOURS, PORT); the container runs 'python -m webcam_aggregator' instead of get_streams.py.

* ci(ci): add pytest to basedpyright deps so it resolves test imports

* chore(scraper): retire v1 get_streams.py and drop unused beautifulsoup4 dep

* docs(docs): fix stale architecture/security/test docs; add SEARCH_QUERY+PORT, testing+venv section

* fix(scraper): resilient catalogue/cache — keep last-good on crash, guard live_ids, bound _locks, clamp ttl, race-safe /health

* fix(playlist): only proxy manifest refs on the upstream's own site (close open-proxy surface)

* refactor(scraper): remove dead code — Candidate.angle_label, hint/resolver_hint chain, Registry resolve_redirect param

* fix(scraper): post-review hardening — predisc dedup, workers, dead-code gate

- drop over-generic st/e params from the predisc dedup key (avoid merging distinct streams)
- make SCRAPE_WORKERS env-configurable
- add vulture dead-code pre-commit hook (confidence 60, ignores framework handler methods)
- fix check-python-version gate to also trigger on docker-compose.yml changes

* refactor(scraper): collapse dual HTTP stack — resolvers use Fetcher, drop urllib _OPENER (one SSRF guard)

* test(scraper): cover build_app degraded-mode startup when youtube client init fails

* build(docker): run the runtime container as a non-root user (UID 65532)

* ci(ci): add non-blocking editing-reminder hooks for deps + tests

* docs(docs): document catalogue-build memory peak + SCRAPE_WORKERS tuning lever

* docs(docs): add Upgrading from v1 migration guide (PUBLIC_BASE_URL, renamed/removed env vars)

* feat(playlist): emit stable tvg-id/tvg-name so player favourites survive catalogue rebuilds

* docs(config): add missing SCRAPE_WORKERS to .env.example

* test(ci): run the test suite in parallel via pytest-xdist (-n auto)

* docs(docs): require AGENTS.md + docs kept in lockstep when app behaviour changes

* docs(docs): quantify build time, make player wording M3U8-generic, add SEARCH_QUERY examples + tuning tips

* feat(config): add EXCLUDE_CATEGORIES to drop categories across all sources

* feat(config): warn at startup on suspect settings (bad ints/LOG_LEVEL, localhost PUBLIC_BASE_URL, unknown EXCLUDE_CATEGORIES)

* docs(docs): fix v1 migration note — EXCLUDED_CATEGORIES is renamed to EXCLUDE_CATEGORIES, not removed

* feat(config): warn when a leftover v1 env var is set (renamed or removed)

* docs(docs): quick-start runs the published ghcr image (pinned :v2) instead of building from source

* docs(docs): replace em dashes with plain punctuation in README and DEVELOPMENT

* fix(api): log when youtube search stops early (quota/error) instead of swallowing it

* fix(playlist): /health rss_mb now includes child-process memory (yt-dlp/deno)

* fix(api): paginate youtube live search by publishedBefore window (was capped at ~100 by pageToken)

* fix(scraper): pin validated IP on outbound fetches — close DNS-rebinding SSRF in-app, TLS preserved

* fix(scraper): pin DNS for the SSRF guard instead of a requests adapter (adapter dropped SNI -> Cloudflare 403'd scraped hosts)

### Features

* **playlist:** multi-source on-demand webcam aggregator (v2) ([#67](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/67)) ([4f38e50](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/4f38e5060d344497055d219eeb4651171c822384))

## [1.1.23](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.22...v1.1.23) (2026-06-19)

### Bug Fixes

* **scraper:** make deno JS runtime functional + commit-hook fix ([#66](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/66)) ([4d45974](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/4d459740f2b1f5dfdb99afd98dc2ee0d3e52c186))

## [1.1.22](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.21...v1.1.22) (2026-06-19)

### Bug Fixes

* **deps:** bump actions/checkout from 6.0.3 to 7.0.0 ([#65](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/65)) ([264dcf7](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/264dcf743654c9a803a4ebbc610fce988952c7de))

## [1.1.21](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.20...v1.1.21) (2026-06-12)

### Bug Fixes

* **deps:** bump yt-dlp in the python-dependencies group ([#64](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/64)) ([396d792](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/396d792ba01f7e35546fc896e5fe1287999f64b8))

## [1.1.20](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.19...v1.1.20) (2026-06-05)

### Bug Fixes

* **deps:** bump actions/checkout from 6.0.2 to 6.0.3 ([#63](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/63)) ([a984445](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/a984445953e29d314112d4c337777fac55478bcb))

## [1.1.19](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.18...v1.1.19) (2026-05-29)

### Bug Fixes

* **deps:** bump docker/login-action from 4.1.0 to 4.2.0 ([#61](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/61)) ([05f4932](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/05f4932f78963f2a6e1f99bf4d15a9ee8cc99c29))
* **deps:** bump docker/setup-buildx-action from 4.0.0 to 4.1.0 ([#60](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/60)) ([e520c58](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/e520c588d1a6404f82b5f2e8e1c3c5907f512d2f))
* **deps:** bump google-api-python-client ([#62](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/62)) ([d03eb81](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/d03eb817789a5e2cfff92242b889c018c6aeb79a))

## [1.1.18](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.17...v1.1.18) (2026-05-22)

### Bug Fixes

* **deps:** bump docker/build-push-action from 7.1.0 to 7.2.0 ([#58](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/58)) ([b873307](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/b8733071f82a81a1f4b69dc8d3ad433ee0b53217))
* **deps:** bump the python-dependencies group with 2 updates ([#59](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/59)) ([a63a6e7](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/a63a6e7178a6a56ea4e2b07ff089b059676866f0))

## [1.1.17](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.16...v1.1.17) (2026-05-08)

### Bug Fixes

* **deps:** bump the python-dependencies group with 5 updates ([#57](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/57)) ([d869670](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/d869670c5b8c26e428d7cb983f65e836c3e1f632))

## [1.1.16](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.15...v1.1.16) (2026-05-01)

### Bug Fixes

* **deps:** bump google-api-python-client ([#56](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/56)) ([427c888](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/427c8888d767a3584eed165497a3edb36fdcfda0))

## [1.1.15](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.14...v1.1.15) (2026-04-24)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#55](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/55)) ([e0ba112](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/e0ba112f75b080f47bd3d959a561db1978def5a2))

## [1.1.14](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.13...v1.1.14) (2026-04-17)

### Bug Fixes

* **scraper:** lower default CONCURRENT_EXTRACTIONS to 1 ([#53](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/53)) ([d05c24f](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/d05c24f6580dec294dbec3b39ab5cea3ee3cef3e))

## [1.1.13](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.12...v1.1.13) (2026-04-17)

### Bug Fixes

* **deps:** bump actions/cache from 5.0.4 to 5.0.5 ([#52](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/52)) ([3669268](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/3669268cab8116139c91371ea3a04039e87a627d))
* **deps:** bump docker/build-push-action from 7.0.0 to 7.1.0 ([#51](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/51)) ([52b692a](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/52b692a2264ec5431bc6786434adf176c210ac05))

## [1.1.12](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.11...v1.1.12) (2026-04-10)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#50](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/50)) ([360e227](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/360e22768ce1e2522d9944e6b958bff2714976c8))

## [1.1.11](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.10...v1.1.11) (2026-04-04)

### Bug Fixes

* **deps:** bump docker/login-action from 4.0.0 to 4.1.0 ([#48](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/48)) ([3a306e5](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/3a306e5c2bd5c79e67976872685fdac94586014e))
* **deps:** bump the python-dependencies group with 3 updates ([#49](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/49)) ([810ad65](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/810ad6584c4ec074a114fe23d095f6f99f3b67fc))

## [1.1.10](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.9...v1.1.10) (2026-03-20)

### Bug Fixes

* **deps:** bump actions/cache from 5.0.3 to 5.0.4 ([#46](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/46)) ([8330f03](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/8330f03b1d771079e2b636bf23417d92cc566a6b))
* **deps:** bump the python-dependencies group across 1 directory with 3 updates ([#47](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/47)) ([ad6b04b](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/ad6b04b77dc7a902eb698147f6315dd3fd57ce5a))

## [1.1.9](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.8...v1.1.9) (2026-03-06)

### Bug Fixes

* **deps:** bump docker/build-push-action from 6.18.0 to 7.0.0 ([#41](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/41)) ([2a10c7a](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/2a10c7ae1a00c369d667264b98f1323ff6702236))
* **deps:** bump docker/login-action from 3.7.0 to 4.0.0 ([#42](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/42)) ([f125216](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/f125216ff5934be7ab59a2e4a0c32dd577affb6c))
* **deps:** bump docker/setup-buildx-action from 3.12.0 to 4.0.0 ([#43](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/43)) ([84e200a](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/84e200a600449a5cc5b754d71e98111038362c2b))
* **deps:** bump the python-dependencies group across 1 directory with 4 updates ([#44](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/44)) ([caacc17](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/caacc17a645a9fbb09605e3b2b47d4a4cffdb6d1))

## [1.1.8](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.7...v1.1.8) (2026-01-30)

### Bug Fixes

* **deps:** bump actions/cache from 5.0.2 to 5.0.3 ([#35](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/35)) ([955f4bd](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/955f4bd0c6e267a3f6b50d932fbf4b0b443321af))
* **deps:** bump docker/login-action from 3.6.0 to 3.7.0 ([#36](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/36)) ([6648795](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/6648795ddb655ca356fba1f88abd591f96c0cb23))
* **deps:** bump the python-dependencies group with 3 updates ([#37](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/37)) ([f0ce45d](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/f0ce45d6aaf991a44bd89b6c88fc5ada5f7d3c08))

## [1.1.7](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.6...v1.1.7) (2026-01-27)

### Bug Fixes

* **deps:** bump actions/cache from 5.0.1 to 5.0.2 ([#33](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/33)) ([6d9d2da](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/6d9d2dae4954ad670141bcb4d1ff247fe43c56bc))

## [1.1.6](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.5...v1.1.6) (2026-01-27)

### Bug Fixes

* **deps:** bump actions/checkout from 6.0.1 to 6.0.2 ([#34](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/34)) ([ae6401c](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/ae6401c5e2b39059bb3e7bf300e3b236db0646f8))
* **deps:** bump actions/setup-python from 6.1.0 to 6.2.0 ([#32](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/32)) ([8df1620](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/8df1620d254a11c4e3fc5210024fe4418e51776d))
* **deps:** bump the python-dependencies group with 3 updates ([#31](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/31)) ([7d39eef](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/7d39eef392b97cf192bc842af904f85de38d9a3b))

## [1.1.5](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.4...v1.1.5) (2026-01-02)

### Bug Fixes

* **deps:** bump docker/setup-buildx-action from 3.11.1 to 3.12.0 ([#28](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/28)) ([31243fc](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/31243fc34aebcfe645448b335e81e5a7724c58c9))
* **deps:** bump the python-dependencies group across 1 directory with 2 updates ([#30](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/30)) ([3f5db49](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/3f5db4935a028266e0dad5ccf7049c2051d83599))

## [1.1.4](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.3...v1.1.4) (2025-12-19)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#25](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/25)) ([5f293b6](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/5f293b60d25bfa4f91f214009cebd89e1b707bb4))

### Performance Improvements

* **scraper:** parallelize stream extraction with ThreadPoolExecutor ([#26](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/26)) ([6cf6087](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/6cf6087f67530e73252549e6a155dc05aa0f4624))

## [1.1.3](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.2...v1.1.3) (2025-12-18)

### Bug Fixes

* **docker:** secure HTTP handler and add health endpoint ([#24](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/24)) ([84011ad](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/84011adaf26294782a91a4b6fc999f374a015f2c))

## [1.1.2](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.1...v1.1.2) (2025-12-18)

### Bug Fixes

* **api:** add retry with exponential backoff for transient errors ([#23](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/23)) ([2d26e4e](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/2d26e4eb867c33819d097417ae0e820a38ecdb43))

## [1.1.1](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.1.0...v1.1.1) (2025-12-18)

### Bug Fixes

* **scraper:** add memory management to prevent growth across cycles ([#22](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/22)) ([ca91436](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/ca914367932d7c68331d69d3c25fe00454c65735))

## [1.1.0](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.7...v1.1.0) (2025-12-17)


### Features

* **docker:** migrate to Docker Hardened Images with multi-stage build ([#20](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/20)) ([02beec5](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/02beec540c1382c9d9c38f299389eae508cb82f3))

## [1.0.7](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.6...v1.0.7) (2025-12-13)


### Bug Fixes

* **deps:** bump actions/cache from 4.3.0 to 5.0.1 ([#15](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/15)) ([9e015bb](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/9e015bb4ef0985bc307f17acc712f1f136e0dbd1))
* **deps:** bump hadolint/hadolint-action from 3.1.0 to 3.3.0 ([#16](https://github.com/mrgeetv/youtube-webcam-aggregator/issues/16)) ([e0d570d](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/e0d570d62d1c17f1810b963829842d119b874ba3))

## [1.0.6](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.5...v1.0.6) (2025-12-13)


### Bug Fixes

* **scraper:** use yt-dlp subprocess to prevent memory leak ([f21bc07](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/f21bc07f928a89f491a102fa5df350f22d38fbbe))

## [1.0.5](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.4...v1.0.5) (2025-12-12)


### Bug Fixes

* **deps:** bump yt-dlp in the python-dependencies group ([3b2c9b2](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/3b2c9b27ceb76ecc9aa65991d7cc09e985f4c696))

## [1.0.4](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.3...v1.0.4) (2025-12-05)


### Bug Fixes

* **docker:** add --no-cache-dir flag to pip install ([7e27a5b](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/7e27a5b7656ac1a0d1c0fed373bae49cc63c9ead))
* **docker:** pin Python version to 3.14-slim ([a96eda4](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/a96eda40143f876d6852fa3b086aff561252b41a))

## [1.0.3](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.2...v1.0.3) (2025-12-05)


### Bug Fixes

* **deps:** restore version pinning in requirements.txt ([cb7019f](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/cb7019f41400afae55db2a03cbaea5b6765e8d2c))
* **docker:** add Deno runtime for yt-dlp YouTube extraction ([cf06b4c](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/cf06b4ce091e2b176f8dfc4b00a6c1d4a9671228))

## [1.0.2](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.1...v1.0.2) (2025-12-05)


### Bug Fixes

* **deps:** bump actions/cache from 4.2.3 to 4.3.0 ([e1184f4](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/e1184f49ee3f4c639867a403a37d980209c17bb9))
* **deps:** bump actions/checkout from 4.2.2 to 6.0.1 ([b124b0b](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/b124b0b53f6302bfaa5f0396e97fea5faa82ffac))
* **deps:** bump actions/setup-python from 5.6.0 to 6.1.0 ([4614d57](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/4614d5785901a3b7e056ac0a9c37d5dc3ae29dc3))
* **deps:** bump cycjimmy/semantic-release-action from 4.2.2 to 6.0.0 ([1585263](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/1585263bdbde81e7fb7071aec6102285b3fa3689))
* **deps:** bump docker/login-action from 3.4.0 to 3.6.0 ([8b01de1](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/8b01de158b1485831889703243549f8e250a991a))

## [1.0.1](https://github.com/mrgeetv/youtube-webcam-aggregator/compare/v1.0.0...v1.0.1) (2025-12-05)


### Bug Fixes

* **ci:** exclude auto-generated CHANGELOG.md from markdownlint ([c558846](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/c5588463a779d6d7af1d5685320353277a153e2c))

## 1.0.0 (2025-12-05)


### Bug Fixes

* **ci:** remove duplicate workflow trigger in ci.yml ([33b775e](https://github.com/mrgeetv/youtube-webcam-aggregator/commit/33b775efb875f14fac8b21303c7636e0d21a08eb))
