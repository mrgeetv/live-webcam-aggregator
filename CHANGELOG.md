# Changelog

All notable changes to this project will be documented in this file. See [Conventional Commits](https://conventionalcommits.org) for commit guidelines.

## [2.14.13](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.12...v2.14.13) (2026-11-24)

### Bug Fixes

* **docker:** rebuild the published image when a base image is patched ([#191](https://github.com/mrgeetv/live-webcam-aggregator/issues/191)) ([72f3931](https://github.com/mrgeetv/live-webcam-aggregator/commit/72f3931159cd9c29b6293e36dae6f866d85fbf99))

## [2.14.12](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.11...v2.14.12) (2026-11-24)

### Bug Fixes

* **scraper:** bump yt-dlp past youtube's n-challenge solver break ([#189](https://github.com/mrgeetv/live-webcam-aggregator/issues/189)) ([2f4ba44](https://github.com/mrgeetv/live-webcam-aggregator/commit/2f4ba44681af1466e4f4024fcf8ce4f2fffd2505)), closes [#184](https://github.com/mrgeetv/live-webcam-aggregator/issues/184)

## [2.14.11](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.10...v2.14.11) (2026-11-24)

### Bug Fixes

* **playlist:** log which cam and why when a stream request fails ([#188](https://github.com/mrgeetv/live-webcam-aggregator/issues/188)) ([b3fb837](https://github.com/mrgeetv/live-webcam-aggregator/commit/b3fb837f3bfb82095c30ba49a1ccb69f6867c27f)), closes [#176](https://github.com/mrgeetv/live-webcam-aggregator/issues/176)

## [2.14.10](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.9...v2.14.10) (2026-11-24)

### Bug Fixes

* **scraper:** follow camscape's site redesign ([#183](https://github.com/mrgeetv/live-webcam-aggregator/issues/183)) ([19ef912](https://github.com/mrgeetv/live-webcam-aggregator/commit/19ef9129e89b462b59f97b11bbe51c3fe0154e50))

## [2.14.9](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.8...v2.14.9) (2026-11-24)

### Bug Fixes

* **playlist:** decode html entities in cam titles through one shared helper ([#182](https://github.com/mrgeetv/live-webcam-aggregator/issues/182)) ([71cbaf4](https://github.com/mrgeetv/live-webcam-aggregator/commit/71cbaf4133971b617ae011b3299507738a8fbac4)), closes [#39](https://github.com/mrgeetv/live-webcam-aggregator/issues/39) [#174](https://github.com/mrgeetv/live-webcam-aggregator/issues/174)

## [2.14.8](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.7...v2.14.8) (2026-11-22)

### Bug Fixes

* **playlist:** drop a master whose first variant is dead ([#180](https://github.com/mrgeetv/live-webcam-aggregator/issues/180)) ([81be4ff](https://github.com/mrgeetv/live-webcam-aggregator/commit/81be4ff10fe91e998694378d75cdb246e3feb593)), closes [#EXT-X-ENDLIST](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-ENDLIST) [#169](https://github.com/mrgeetv/live-webcam-aggregator/issues/169)

## [2.14.7](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.6...v2.14.7) (2026-11-22)

### Bug Fixes

* **playlist:** carry key and init section across the live-edge trim ([#179](https://github.com/mrgeetv/live-webcam-aggregator/issues/179)) ([b4258a5](https://github.com/mrgeetv/live-webcam-aggregator/commit/b4258a51c200a22ed2bcd2a0cfb9fe1b1602833f)), closes [#EXT-X-KEY](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-KEY) [#EXT-X-MAP](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-MAP) [#EXT-X-INDEPENDENT-SEGMENTS](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-INDEPENDENT-SEGMENTS) [#EXT-X-DEFINE](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-DEFINE) [#164](https://github.com/mrgeetv/live-webcam-aggregator/issues/164)

## [2.14.6](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.5...v2.14.6) (2026-11-22)

### Bug Fixes

* **scraper:** keep every regex linear on hostile input ([#178](https://github.com/mrgeetv/live-webcam-aggregator/issues/178)) ([93cf795](https://github.com/mrgeetv/live-webcam-aggregator/commit/93cf795074215abd397613152f37581ee57ac127)), closes [#163](https://github.com/mrgeetv/live-webcam-aggregator/issues/163) [#163](https://github.com/mrgeetv/live-webcam-aggregator/issues/163)

## [2.14.5](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.4...v2.14.5) (2026-11-22)

### Bug Fixes

* **playlist:** re-resolve a youtube redirect when the player retries ([#175](https://github.com/mrgeetv/live-webcam-aggregator/issues/175)) ([9366cb0](https://github.com/mrgeetv/live-webcam-aggregator/commit/9366cb03a295f006072dc54326c539c5bcea0be4)), closes [#162](https://github.com/mrgeetv/live-webcam-aggregator/issues/162)

## [2.14.4](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.3...v2.14.4) (2026-11-22)

### Bug Fixes

* **scraper:** read the first iframe that could be a player, not the first iframe ([#173](https://github.com/mrgeetv/live-webcam-aggregator/issues/173)) ([6e3bdd4](https://github.com/mrgeetv/live-webcam-aggregator/commit/6e3bdd4b70d82225a192a6631cfab36ed9660588)), closes [#160](https://github.com/mrgeetv/live-webcam-aggregator/issues/160)

## [2.14.3](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.2...v2.14.3) (2026-11-22)

### Bug Fixes

* **scraper:** stream page bodies through a bounded buffer ([#171](https://github.com/mrgeetv/live-webcam-aggregator/issues/171)) ([0d52d4e](https://github.com/mrgeetv/live-webcam-aggregator/commit/0d52d4e21e6174d641cc50a7f1d25f7b7306190c)), closes [#155](https://github.com/mrgeetv/live-webcam-aggregator/issues/155)

## [2.14.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.1...v2.14.2) (2026-11-22)

### Bug Fixes

* **playlist:** rewrite every hls reference attribute on tag lines ([#168](https://github.com/mrgeetv/live-webcam-aggregator/issues/168)) ([ff5e534](https://github.com/mrgeetv/live-webcam-aggregator/commit/ff5e5348a655192cd3c0fb089fef0d97e07fcaf2)), closes [#157](https://github.com/mrgeetv/live-webcam-aggregator/issues/157)

## [2.14.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.0...v2.14.1) (2026-11-22)

### Bug Fixes

* **scraper:** restore cxtvlive via the live index and curl_cffi path ([8e05684](https://github.com/mrgeetv/live-webcam-aggregator/commit/8e05684afb1faf57f35784f64f24e9acc451e6d7)), closes [#149](https://github.com/mrgeetv/live-webcam-aggregator/issues/149) [#148](https://github.com/mrgeetv/live-webcam-aggregator/issues/148)
* **scraper:** take the feratel cam id from the detail-page url ([#167](https://github.com/mrgeetv/live-webcam-aggregator/issues/167)) ([f91de2c](https://github.com/mrgeetv/live-webcam-aggregator/commit/f91de2c2a62f766e6e257a7a121a66136d2c047f)), closes [#121](https://github.com/mrgeetv/live-webcam-aggregator/issues/121)

### Maintenance

* **release:** 2.14.1 [skip ci] ([31d0c84](https://github.com/mrgeetv/live-webcam-aggregator/commit/31d0c8408cb043a3bc12d5db3f100926e389ab98))

## [2.14.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.14.0...v2.14.1) (2026-11-22)

### Bug Fixes

* **scraper:** take the feratel cam id from the detail-page url ([#167](https://github.com/mrgeetv/live-webcam-aggregator/issues/167)) ([f91de2c](https://github.com/mrgeetv/live-webcam-aggregator/commit/f91de2c2a62f766e6e257a7a121a66136d2c047f)), closes [#121](https://github.com/mrgeetv/live-webcam-aggregator/issues/121)

## [2.14.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.12...v2.14.0) (2026-11-21)

### Features

* **scraper:** add a curl_cffi fetch path and restore wildlife-trusts ([d50f021](https://github.com/mrgeetv/live-webcam-aggregator/commit/d50f0218b5c88edb915a7a61300e99e817224843)), closes [#149](https://github.com/mrgeetv/live-webcam-aggregator/issues/149) [#EXT-X-](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-) [#149](https://github.com/mrgeetv/live-webcam-aggregator/issues/149)

## [2.13.12](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.11...v2.13.12) (2026-11-21)

### Bug Fixes

* **scraper:** follow explore's moved streams feed and relay its mediamtx segments ([#156](https://github.com/mrgeetv/live-webcam-aggregator/issues/156)) ([4d50362](https://github.com/mrgeetv/live-webcam-aggregator/commit/4d50362c75278f92320790eb83dc3a9014a52f26)), closes [#147](https://github.com/mrgeetv/live-webcam-aggregator/issues/147)

## [2.13.11](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.10...v2.13.11) (2026-11-21)

### Bug Fixes

* **scraper:** enumerate skyline from its sitemap after the site restructure ([#154](https://github.com/mrgeetv/live-webcam-aggregator/issues/154)) ([55bad5d](https://github.com/mrgeetv/live-webcam-aggregator/commit/55bad5dfdd941dc410f9486e9f95f330b3a92558)), closes [#146](https://github.com/mrgeetv/live-webcam-aggregator/issues/146)

## [2.13.10](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.9...v2.13.10) (2026-11-21)

### Bug Fixes

* **scraper:** enumerate tomarigi via its region listings ([#153](https://github.com/mrgeetv/live-webcam-aggregator/issues/153)) ([b449888](https://github.com/mrgeetv/live-webcam-aggregator/commit/b449888cc78dbaa2d9ee29f793e1d62de79ca916)), closes [#145](https://github.com/mrgeetv/live-webcam-aggregator/issues/145)

## [2.13.9](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.8...v2.13.9) (2026-11-21)

### Bug Fixes

* **scraper:** accept http:// sitemap locs for hdontap ([#152](https://github.com/mrgeetv/live-webcam-aggregator/issues/152)) ([5fee741](https://github.com/mrgeetv/live-webcam-aggregator/commit/5fee7418244b90efbf75ec9c531ef83a703a4730)), closes [#144](https://github.com/mrgeetv/live-webcam-aggregator/issues/144)

## [2.13.8](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.7...v2.13.8) (2026-11-21)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#143](https://github.com/mrgeetv/live-webcam-aggregator/issues/143)) ([32c9ded](https://github.com/mrgeetv/live-webcam-aggregator/commit/32c9ded803c658da51cd413f76873a87f9efff86))

### Maintenance

* **deps:** bump docker/login-action from 4.7.0 to 4.8.0 ([#142](https://github.com/mrgeetv/live-webcam-aggregator/issues/142)) ([d65c1a7](https://github.com/mrgeetv/live-webcam-aggregator/commit/d65c1a7a90d86491842046f8b511783a8df2d9e4))

## [2.13.7](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.6...v2.13.7) (2026-11-06)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#141](https://github.com/mrgeetv/live-webcam-aggregator/issues/141)) ([eab2a4b](https://github.com/mrgeetv/live-webcam-aggregator/commit/eab2a4baafd6014f7dc1b2a3367edfae8a44ac36))

### Maintenance

* **deps:** bump cycjimmy/semantic-release-action from 8.1.3 to 8.1.4 ([#140](https://github.com/mrgeetv/live-webcam-aggregator/issues/140)) ([b0c8ab6](https://github.com/mrgeetv/live-webcam-aggregator/commit/b0c8ab6211f1bdb7a2cec8f63ca18759e72b8788))

## [2.13.6](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.5...v2.13.6) (2026-10-30)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#139](https://github.com/mrgeetv/live-webcam-aggregator/issues/139)) ([f05b249](https://github.com/mrgeetv/live-webcam-aggregator/commit/f05b249b7b116bc51eff50d90bd701d43ac74174))

### Maintenance

* **deps:** bump actions/setup-python from 7.0.0 to 7.1.0 ([#138](https://github.com/mrgeetv/live-webcam-aggregator/issues/138)) ([58c90c8](https://github.com/mrgeetv/live-webcam-aggregator/commit/58c90c8afbe30ba6ffef075b9d0da2abbdfa8139))
* **deps:** bump cycjimmy/semantic-release-action from 8.0.1 to 8.1.3 ([#136](https://github.com/mrgeetv/live-webcam-aggregator/issues/136)) ([34516b2](https://github.com/mrgeetv/live-webcam-aggregator/commit/34516b2b850e3b8a643aa59b3d136b78a15e6ddd))
* **deps:** bump docker/login-action from 4.6.0 to 4.7.0 ([#137](https://github.com/mrgeetv/live-webcam-aggregator/issues/137)) ([4406cf1](https://github.com/mrgeetv/live-webcam-aggregator/commit/4406cf10fdaa341469aa1112b6bd9b61e62d2078))

## [2.13.5](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.4...v2.13.5) (2026-10-21)

### Bug Fixes

* **docker:** stop the image trusting the compromised emudhra intermediates ([#135](https://github.com/mrgeetv/live-webcam-aggregator/issues/135)) ([f8204c5](https://github.com/mrgeetv/live-webcam-aggregator/commit/f8204c516bfa862d6b4073b34a8f20ca2cb297a5))

## [2.13.4](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.3...v2.13.4) (2026-10-17)

### Bug Fixes

* **deps:** bump the python-dependencies group across 1 directory with 5 updates ([#134](https://github.com/mrgeetv/live-webcam-aggregator/issues/134)) ([80cd240](https://github.com/mrgeetv/live-webcam-aggregator/commit/80cd2406716f827fa132d80e21ad817c5d249a31))

### Maintenance

* **deps:** bump cycjimmy/semantic-release-action from 6.0.0 to 8.0.1 ([#133](https://github.com/mrgeetv/live-webcam-aggregator/issues/133)) ([7d9a5d6](https://github.com/mrgeetv/live-webcam-aggregator/commit/7d9a5d6b279a8647413ed4552725f3e9341c1929))
* **deps:** bump docker/build-push-action from 7.2.0 to 7.4.0 ([#130](https://github.com/mrgeetv/live-webcam-aggregator/issues/130)) ([ca00b10](https://github.com/mrgeetv/live-webcam-aggregator/commit/ca00b10239e0c3798721a22c9a7c07d9670ef809))
* **deps:** bump docker/setup-buildx-action from 4.3.0 to 4.4.0 ([#131](https://github.com/mrgeetv/live-webcam-aggregator/issues/131)) ([0659c31](https://github.com/mrgeetv/live-webcam-aggregator/commit/0659c318f68189a94ede5402052924c65ff06a4e))

## [2.13.3](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.2...v2.13.3) (2026-09-12)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#129](https://github.com/mrgeetv/live-webcam-aggregator/issues/129)) ([92d4117](https://github.com/mrgeetv/live-webcam-aggregator/commit/92d411764598f4a63cb87dfc3956495934c56e79))

## [2.13.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.1...v2.13.2) (2026-09-04)

### Bug Fixes

* **deps:** bump the python-dependencies group with 4 updates ([#128](https://github.com/mrgeetv/live-webcam-aggregator/issues/128)) ([fa4f6bd](https://github.com/mrgeetv/live-webcam-aggregator/commit/fa4f6bd57907ca8d9131d821605b100dce39ff18))

## [2.13.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.13.0...v2.13.1) (2026-08-28)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#126](https://github.com/mrgeetv/live-webcam-aggregator/issues/126)) ([9bd3ae6](https://github.com/mrgeetv/live-webcam-aggregator/commit/9bd3ae607fb65f926340709f2a3695f520ccfa64))

### Maintenance

* **deps:** bump hadolint/hadolint-action from 3.4.0 to 3.5.0 ([#125](https://github.com/mrgeetv/live-webcam-aggregator/issues/125)) ([51b14d1](https://github.com/mrgeetv/live-webcam-aggregator/commit/51b14d11556eb0c1cd065721c99fcb97b486c194))

## [2.13.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.12.2...v2.13.0) (2026-08-22)

### Features

* **scraper:** add tomarigi.me source, teach the title fallback Japanese ([#124](https://github.com/mrgeetv/live-webcam-aggregator/issues/124)) ([a7a6b7c](https://github.com/mrgeetv/live-webcam-aggregator/commit/a7a6b7c0f332d7e78e3e93f857fb994f0f515ddb))

### Maintenance

* **deps:** bump docker/setup-buildx-action from 4.1.0 to 4.3.0 ([#123](https://github.com/mrgeetv/live-webcam-aggregator/issues/123)) ([11a0e73](https://github.com/mrgeetv/live-webcam-aggregator/commit/11a0e73de37413fb507e4d80194701efd947e0cb))

## [2.12.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.12.1...v2.12.2) (2026-08-20)

### Bug Fixes

* **scraper:** index livefromiceland via the WP REST collection ([#120](https://github.com/mrgeetv/live-webcam-aggregator/issues/120)) ([bfba8ef](https://github.com/mrgeetv/live-webcam-aggregator/commit/bfba8ef2bfc775279bc4fbcb3bd5a0c97222366d))

## [2.12.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.12.0...v2.12.1) (2026-08-20)

### Bug Fixes

* **scraper:** pin yt-dlp to muxed youtube clients and bump to 2026.8.19 ([#119](https://github.com/mrgeetv/live-webcam-aggregator/issues/119)) ([4cdc3ed](https://github.com/mrgeetv/live-webcam-aggregator/commit/4cdc3edfa999e4b8c8ec321902ebcb9e9c3ec05a)), closes [#74](https://github.com/mrgeetv/live-webcam-aggregator/issues/74)

## [2.12.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.5...v2.12.0) (2026-08-16)

### Features

* **scraper:** thirteen new sources (5,664 → 9,760 channels) + probe dedup, category and docs fixes ([#117](https://github.com/mrgeetv/live-webcam-aggregator/issues/117)) ([499ace5](https://github.com/mrgeetv/live-webcam-aggregator/commit/499ace5cb98b83176f45907ec294f2e83706f156))

## [2.11.5](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.4...v2.11.5) (2026-08-15)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#115](https://github.com/mrgeetv/live-webcam-aggregator/issues/115)) ([53d7230](https://github.com/mrgeetv/live-webcam-aggregator/commit/53d7230bfdd59622135dea4f851ae3f3b49d742a))

## [2.11.4](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.3...v2.11.4) (2026-08-08)

### Bug Fixes

* **scraper:** resolve camscape's camsecure embeds to their HLS ([#114](https://github.com/mrgeetv/live-webcam-aggregator/issues/114)) ([0979ef1](https://github.com/mrgeetv/live-webcam-aggregator/commit/0979ef1548b9cea2764a592ff158a48767151c06)), closes [#108](https://github.com/mrgeetv/live-webcam-aggregator/issues/108)

## [2.11.3](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.2...v2.11.3) (2026-08-07)

### Bug Fixes

* **scraper:** drop analytics iframes at the extraction seam ([#113](https://github.com/mrgeetv/live-webcam-aggregator/issues/113)) ([2e19ae1](https://github.com/mrgeetv/live-webcam-aggregator/commit/2e19ae1818df1cf486a2cf20a95552d674ca9e6b)), closes [#109](https://github.com/mrgeetv/live-webcam-aggregator/issues/109)

## [2.11.2](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.1...v2.11.2) (2026-08-07)

### Bug Fixes

* **scraper:** reject empty HLS playlists and outlive skyline's token lapse ([#111](https://github.com/mrgeetv/live-webcam-aggregator/issues/111)) ([39091fa](https://github.com/mrgeetv/live-webcam-aggregator/commit/39091fad892eccfa35fd393c24a2c70489f0496a)), closes [#EXTM3U](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXTM3U) [#EXT-X-ENDLIST](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXT-X-ENDLIST) [#EXTM3U](https://github.com/mrgeetv/live-webcam-aggregator/issues/EXTM3U)

## [2.11.1](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.11.0...v2.11.1) (2026-08-07)

### Bug Fixes

* **scraper:** back off per host when a site sheds load ([#110](https://github.com/mrgeetv/live-webcam-aggregator/issues/110)) ([e9642a1](https://github.com/mrgeetv/live-webcam-aggregator/commit/e9642a1b9c3ab1e58406bc1c01eb3dff2e73e14c))

## [2.11.0](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.6...v2.11.0) (2026-08-06)

### Features

* **scraper:** make source failures self-diagnosing, and fix what that surfaced ([#105](https://github.com/mrgeetv/live-webcam-aggregator/issues/105)) ([4264009](https://github.com/mrgeetv/live-webcam-aggregator/commit/42640096bbf21fef7d12f31e0844a01be4b2a395))

## [2.10.6](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.5...v2.10.6) (2026-07-31)

### Bug Fixes

* **deps:** bump docker/login-action from 4.5.0 to 4.5.2 ([#102](https://github.com/mrgeetv/live-webcam-aggregator/issues/102)) ([1532af2](https://github.com/mrgeetv/live-webcam-aggregator/commit/1532af2d2049123488e1cbc49ef9c12d413392fe))

## [2.10.5](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.4...v2.10.5) (2026-07-31)

### Bug Fixes

* **docker:** satisfy hadolint DL3025 in the healthcheck ([#103](https://github.com/mrgeetv/live-webcam-aggregator/issues/103)) ([a19ec43](https://github.com/mrgeetv/live-webcam-aggregator/commit/a19ec434456c99736940d7e1809cf0083ea95a7d))

## [2.10.4](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.3...v2.10.4) (2026-07-24)

### Bug Fixes

* **deps:** bump actions/checkout from 7.0.0 to 7.0.1 ([#99](https://github.com/mrgeetv/live-webcam-aggregator/issues/99)) ([87a477b](https://github.com/mrgeetv/live-webcam-aggregator/commit/87a477bd0b9c65b8a30022a459444503c595a707))
* **deps:** bump actions/setup-python from 6.3.0 to 7.0.0 ([#100](https://github.com/mrgeetv/live-webcam-aggregator/issues/100)) ([b0cfd3e](https://github.com/mrgeetv/live-webcam-aggregator/commit/b0cfd3ee8b4383b90b74265ac67313e1c3e333c5))
* **deps:** bump docker/login-action from 4.4.0 to 4.5.0 ([#98](https://github.com/mrgeetv/live-webcam-aggregator/issues/98)) ([fca8f2b](https://github.com/mrgeetv/live-webcam-aggregator/commit/fca8f2b2a443e83ad49ad808dae20a3c75942535))
* **deps:** bump pre-commit in the python-dependencies group ([#101](https://github.com/mrgeetv/live-webcam-aggregator/issues/101)) ([129b027](https://github.com/mrgeetv/live-webcam-aggregator/commit/129b027bf3977f26fd6ce47a39c8d4fe29d8e892))

## [2.10.3](https://github.com/mrgeetv/live-webcam-aggregator/compare/v2.10.2...v2.10.3) (2026-07-17)

### Bug Fixes

* **deps:** bump the python-dependencies group with 2 updates ([#97](https://github.com/mrgeetv/live-webcam-aggregator/issues/97)) ([78a4be6](https://github.com/mrgeetv/live-webcam-aggregator/commit/78a4be6ecf732275d77188c2c23ca36a1020463a))

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
