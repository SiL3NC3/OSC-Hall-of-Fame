# Changelog

All notable changes to this project are documented here.

## [0.23] - 2026-06-21

### Changed
- The Hall-of-Fame generator now renders its visible UI text in English, including filters, dialogs, status chips, and loading messages.
- Duration, bio, and dashboard labels in the generated HTML are now English-only for a consistent public-facing output.

## [0.17] - 2026-06-21

### Changed
- The extractor version was bumped to keep the generator/extractor release pair in sync after the Hall-of-Fame language switch.

## [0.22] - 2026-06-21

### Changed
- The Hall-of-Fame player now shows the OSC row's `Artist - Track` as its visible title instead of trusting archive.org title text.
- The dashboard stats now distinguish between scorecards and OSCs with extracted results.

### Fixed
- Python warnings from invalid `\s` escape sequences in the generated player JavaScript are gone.

## [0.16] - 2026-06-21

### Added
- Regression coverage for archive track matching by `artist + track`, including misordered archive track lists.
- Regression coverage for legacy `Track - Artist` result rows that are repaired against archive.org metadata.

### Changed
- Legacy result rows are now repaired against cached archive.org track metadata before archive validation and HTML enrichment.
- Missing archive items now count as `SKIP` instead of being reported as archive failures.
- Extractor summary output now distinguishes `archive_checks` from `archive_skipped`.

### Fixed
- Early V02 scorecards no longer keep the wrong `artist/track` orientation when archive.org clearly indicates the opposite.
- Archive playback assignments no longer rely on fallback ordering when a matching `artist + track` pair exists.

## [0.14] - 2026-06-20

### Changed
- archive.org cache warmup now prints progress while track metadata is fetched.
- archive cache is written incrementally during warmup so interrupted runs can resume faster.

## [0.20] - 2026-06-20

### Changed
- Track titles in the Hall-of-Fame tables now link to the matching archive.org player/details page.
- The embedded artist popup player keeps using the direct archive.org stream URL.

## [0.13] - 2026-06-20

### Added
- Fallback stream-link assignment so every result row can still get an archive.org URL when exact name matching misses.
- Pytest coverage for archive.org stream-link enrichment.

### Changed
- Stream-link enrichment now prefers exact matches, then falls back to remaining archive files in order.

## [0.12] - 2026-06-20

### Changed
- archive.org metadata and track lists are cached locally under `dist/archive-cache/`.
- archive.org track fetches run in parallel to keep rebuilds responsive.

### Fixed
- Rebuilds no longer have to refetch all archive.org track metadata every time.

## [0.19] - 2026-06-20

### Added
- Hall-of-Fame artist dialogs now include an archive.org audio player.
- The extractor now writes archive.org page and stream URLs into the normalized results.

### Changed
- OSC detail links now point at the matching archive.org release page.
- Stream URLs are refreshed on every rebuild because they are derived from the latest archive metadata.

### Fixed
- Artist detail dialogs can now stream the matching track directly from archive.org.

## [0.17] - 2026-06-20

### Changed
- Hall of Fame generator now uses `kvrosc_challenges.csv` for year, synth, and URL metadata.
- Top summary cards were simplified to the useful overview metrics.
- Leader cards now show Gold/Silver/Bronze counts.
- Artist detail popups now use a shorter bio line while keeping the full bio as tooltip.
- The result table and artist popup now keep DQ rows as `DQ` instead of collapsing them to `0`.

### Fixed
- `Place 0` rows for DQ entries in the generated HOF output.
- Missing year metadata in the HOF output.
- Leader card stat layout and popup badge ordering.

## [0.18] - 2026-06-20

### Fixed
- V02 total-score rows were parsing `Artist - Track` entries in reverse order.
- OSC049 and similar V02 scorecards now keep artist and track in the right columns.

### Changed
- Bumped generator and extractor versions to track the parser fix.

## [0.11] - 2026-06-20

### Added
- archive.org validation for extracted scorecard rows.
- archive validation reports and stream URLs in the extractor output.

### Fixed
- The extractor can now use archive.org as a verification source for matching artist and track rows.

## [0.10] - 2026-06-20

### Fixed
- The V02 TOTAL SCORES fallback path now parses `Artist - Track` as `artist` and `track` in the right order.
- OSC049 and similar sparse V02 scorecards no longer flip artist and track.

## [0.8] - 2026-06-20

### Changed
- Extractor version bump after the scorecard parsing and reporting cleanup.
- TXT scorecard parsing remains supported for OSC001-OSC015 and continues to feed the normalized CSV output.

### Fixed
- DQ handling stays as raw `dq` in normalized results.
- OSC206 and other modern scorecards keep their full result rows in the single source of truth.

## [0.7] - 2026-06-20

### Added
- Support for legacy OSC001-OSC015 TXT scorecards.
- Cleaner one-line extractor progress output.
- Normalized CSV-based HOF pipeline.
