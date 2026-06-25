# ETL Pipeline — Technical Build Log

A chronological record of how the Enabled Talent disability employment ETL pipeline was built: what was added at each stage, what broke, and how it was fixed. Intended as an engineering companion to the client-facing **Enabled Talent Client Takeover Documentation** — that document explains what the pipeline does and how to run it; this one explains how it got built and what to watch for if you're maintaining or extending it.

Commit hashes refer to `https://github.com/dat-c-le/enabled-talent-etl-pipeline`.

---

## Step 1 — Initial pipeline scaffold
**Commit:** `01d7590`

Built the core ETL skeleton:
- `extract/acs.py` — Census ACS 1-year extraction for tables S1810, S1811, B18120, B18121, at both state and county geography
- `extract/bls.py` — BLS extraction, originally covering QCEW (state employment by industry) and a small hand-curated set of CPS disability series
- `transform/acs_transform.py` — converts raw Census variable codes into human-readable column names, handles percent-to-count conversion, attaches FIPS geography
- `transform/bls_transform.py` — cleans BLS outputs
- `load/bigquery_loader.py` — sanitizes column names for BigQuery, groups cleaned CSVs by table, loads with `WRITE_TRUNCATE`
- `validate/report.py` — row counts, duplicates, null profiling, FIPS coverage checks
- `main.py` — CLI orchestrator (`--step extract|transform|combine|validate|load`, `--source acs|bls`)
- `config.py` — table list, year ranges, FIPS list, output paths

## Step 2 — Documentation
**Commit:** `9b6a67d`

Added `README.md` with setup instructions and an initial data dictionary.

## Step 3 — 2024 ACS data and column normalization
**Commit:** `e1e8e8f`

- Extended `ACS_YEARS` to include 2024
- Normalized S1811 column names across all years — Census changed variable labels mid-series (e.g. occupation/industry breakdowns shifted from percent-only to count+percent in later years), so the transform needed consistent column naming regardless of the year's raw label format

## Step 4 — BLS CPS disability integration + QCEW extractor rewrite
**Commit:** `255420b`

The largest single change. Two mostly-independent efforts landed in one commit:

**CPS disability series (new):**
- Replaced the original 8 hand-picked series with all 770 series from the BLS "Persons with a Disability" publication's "All series" tab, plus 24 general-population series in both NSA (`LNU0`) and SA (`LNS1`) form — 818 series total, tracked in `bls_series.json`
- Built dimension decoding in `transform/bls_transform.py`: `disability_status`, `sex`, `age_group`, `race_ethnicity` (merged separate race and Hispanic/Latino origin into one column per client direction — Hispanic/Latino takes priority when both apply), `labor_force_status`, `occupation`, `industry`, `class_of_worker`, `nilf_subcategory`
- Converted percent-distribution series (Tables 3/4 — occupation, industry, class of worker) into counts in thousands, using the annual-average total employed (with/without disability) as the denominator
- Tested whether seasonally-adjusted (`LNS1`) versions of disability-specific series exist by querying the BLS API directly (`scripts/test_lns_series.py`) — confirmed they don't (BLS doesn't seasonally adjust disability series; the underlying CPS sample is too small). SA series were only added for the general-population totals, which do exist.

**QCEW extractor (rewritten, later removed in Step 6):**
- Found the original QCEW extractor's endpoint (`/cew/data/api/{year}/a/area/{area}/industry/{code}/ownership/0/`) had been retired by BLS — it returned HTTP 404 for all 16,016 requests (52 states × 22 industries × 14 years) after a ~21 hour run, producing zero records
- Rewrote to use the working per-area CSV endpoint (`/cew/data/api/{year}/a/area/{area_fips}.csv`), cutting request count from 16,016 to 728 (52 states × 14 years) by downloading one file per state-year and filtering locally
- Fixed an industry-code format mismatch: `config.py` used concatenated codes (`"3133"`, `"4445"`, `"4849"`), the CSV used hyphenated codes (`"31-33"`, `"44-45"`, `"48-49"`)
- Found that `own_code=0` (total, all ownerships) only exists for the aggregate "all industries" row — individual industries are split by ownership type (private/federal/state/local) with no precomputed total, so the extractor sums all four ownership types per industry

## Step 5 — README update
**Commit:** `57926cb`

Documented the CPS and QCEW table schemas in `README.md`.

## Step 6 — QCEW removed
**Commit:** `f26f616`

Decision: QCEW has no disability breakdown at all — it's general employment by state/industry. Since ACS (S1811, B18120) and CPS already cover disability-specific employment by occupation/industry, QCEW was assessed as redundant rather than complementary, and removed:
- Deleted QCEW functions from `extract/bls.py`, the QCEW transform from `transform/bls_transform.py`, `QCEW_INDUSTRY_CODES` from `config.py`, and the QCEW extraction step from `main.py`
- Dropped the `bls_qcew` BigQuery table and deleted `output/cleaned/bls_qcew_cleaned.csv`

## Step 7 — B18120 state-level extraction gap
**Commit:** `9c2c07d`

Discovered while preparing a state-level correlation analysis: **B18120 only had true state-level rows for 2024** — all other years (2010–2023) were county-only, despite the extractor appearing to run successfully.

Root cause: an old version of `_fetch_geo_data` (before a since-fixed merge bug) had left a stale `NAME.1` column baked into the *cached* raw county CSVs — an artifact of merging two API batches that both included a `NAME` column. The state-extraction "fast path" (used when county data is cached but state data is missing) read all non-geo columns from the cached CSV header to rebuild the variable list for the state-level API call. It picked up `NAME.1` as if it were a real Census variable, and the Census API rejected the entire batch request with HTTP 204 (no content) — silently, with no per-variable error.

Fix: changed the fast-path's column filter in `extract/acs.py` from a geo-column blacklist (`{"NAME", "state", "county", "ucgid"}`) to a regex matching actual Census variable ID patterns (`^[A-Z]\d+(_C\d+)?_\d+(E|M|PE|PM)$`), so any non-variable artifact is excluded regardless of name. Backfilled all 13 missing years (`scripts/backfill_b18120_state.py`), rebuilt the cleaned and combined files (12,429 rows, 52 states × 14 years), and reloaded `acs_b18120` to BigQuery.

## Step 8 — B18121 state-level extraction gap (same bug as Step 7)
**Commit:** `42d5d15`

Found during a final audit before writing this build log: **B18121 had the identical gap B18120 had before Step 7's fix** — true state-level rows only for 2024, all other years county-only, caused by the exact same `NAME.1` artifact issue. The `extract/acs.py` fix from Step 7 already covers this case; only needed to re-run the backfill (`scripts/backfill_b18121_state.py`) for B18121 specifically. Rebuilt cleaned and combined files (12,429 rows, 52 states × 14 years) and reloaded `acs_b18121` to BigQuery.

S1810 and S1811 were audited at the same time and confirmed **not** affected — both have full 52-state coverage for all 14 years.

---

## Current state (as of `42d5d15`)

| Table | Rows | Coverage |
|---|---|---|
| `acs_s1810` | 12,429 (combined) | 52 states × 14 years, full county coverage |
| `acs_s1811` | 12,429 (combined) | 52 states × 14 years, full county coverage |
| `acs_b18120` | 12,429 (combined) | 52 states × 14 years — fixed in Step 7 |
| `acs_b18121` | 12,429 (combined) | 52 states × 14 years — fixed in Step 8 |
| `bls_cps_disability` | 34,898 | National, 818 series, 2008–2024 |

All five tables are loaded to BigQuery project `msba-capstone-498915`, dataset `disability_employment`.

## Open items for a future maintainer

- The `NAME.1`-style merge artifact bug class (Steps 7 and 8) was fixed at its root in `extract/acs.py`, but any *already-cached* raw CSV from before the fix could still carry the artifact. If a future extraction run unexpectedly returns HTTP 204 on a cached-county/missing-state fast path, check `acs_{table}_{year}_raw.csv` headers for unexpected non-variable columns before assuming it's an API-side issue.
- QCEW (removed in Step 6) is fully recoverable from commit `255420b` if a future need arises for general industry-employment context alongside the disability-specific datasets.
