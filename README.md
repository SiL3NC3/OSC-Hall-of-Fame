# KVR OSC Hall of Fame

Focused documentation for the Hall of Fame build, its data sources, and the most important diagnostic files.

## Purpose

The Hall of Fame build generates a static web view from all OSC scorecards, including:

- Hall-of-Fame-Ranking
- full results list across all OSCs
- artist detail dialog with archive player
- archive.org links for available tracks

The webserver output is written to `dist/hof/`.

## Core Workflow

The full rebuild is started with:

```bash
./update_hall-of-fame.sh
```

The process is:

1. `scripts/scorecard_vote_extractor.py` reads all files from `scorecards/`.
2. The extractor generates normalized CSVs and diagnostic files in a temporary build directory.
3. `scripts/kvrosc_hall_of_fame_generator.py` renders `index.html` from that data.
4. `update_hall-of-fame.sh` copies only the release files to `dist/hof/`.

## Important Files

Input:

- `scorecards/`
- `kvrosc_challenges.csv`
- `dist/archive-cache/archive_validation_cache.json`

Core scripts:

- `scripts/scorecard_vote_extractor.py`
- `scripts/kvrosc_hall_of_fame_generator.py`
- `update_hall-of-fame.sh`

Release output:

- `dist/hof/index.html`
- `dist/hof/normalized_results.csv`

Temporary diagnostic/build output:

- `dist/tmp/hof-build.*/scorecard_file_inventory.csv`
- `dist/tmp/hof-build.*/archive_validation_by_osc.csv`
- `dist/tmp/hof-build.*/archive_validation_details.csv`
- `dist/tmp/hof-build.*/hard_reconciliation_by_osc.csv`
- `dist/tmp/hof-build.*/validation_matrix.csv`
- `dist/tmp/hof-build.*/validation_failures.csv`
- `dist/tmp/hof-build.*/validation_reviews.csv`
- `dist/tmp/hof-build.*/run_summary.json`

## Versions

Current versions:

- Extractor: `v0.17`
- Generator: `v0.23`

Changes are tracked in [CHANGELOG.md](/home/frank/Musik/OSC/RESULTS/CHANGELOG.md).

If you change the HOF flow, you should usually update three things:

1. The corresponding `version` variable in the affected script
2. The entry in `CHANGELOG.md`
3. Tests, if parser, matching, or player logic is affected

## Archive Logic

archive.org is used for two things:

- validating result rows against available archive tracks
- player and track links in the HTML output

Important:

- `archive=CHECK` does not automatically mean the build is broken.
- `archive=SKIP` usually means there is no matching archive item for that OSC.
- The actual hard parser or vote issues are primarily reflected in `hard_validation_status`.

The extractor can now repair part of the legacy scorecard data against archive metadata when `artist` and `track` are swapped in historical files.

## Fast Vs. Strict

The default run is intentionally fast:

- the cache is loaded
- missing archive metadata is not aggressively prefetched
- download links are not verified live over HTTP

Optional slower and stricter run:

```bash
python scripts/scorecard_vote_extractor.py \
  --scorecards scorecards \
  --out /tmp/hof-check \
  --archive-prefetch-missing \
  --verify-archive-links
```

## Diagnostics

If tracks are not matched:

1. Check `normalized_results.csv`:
   `archive_url`, `archive_file`, `archive_match_kind`, `archive_link_state`
2. Check `archive_validation_by_osc.csv`:
   `missing_rows`, `unmatched_archive_tracks`, `checks`
3. Check `archive_validation_details.csv`:
   concrete row-level mapping of `result_artist/result_track` against `archive_artist/archive_track`

Common cases:

- historical `Track - Artist` instead of `Artist - Track`
- archive filenames starting with `116pts ...`
- minor title variants using `_`, missing apostrophes, or extra symbols
- OSCs without a usable archive.org item

## Tests

Relevant regressions currently live in:

- `tests/test_archive_links.py`

A useful quick check after HOF changes:

```bash
python -m py_compile scripts/scorecard_vote_extractor.py scripts/kvrosc_hall_of_fame_generator.py tests/test_archive_links.py
pytest -q tests/test_archive_links.py
```
