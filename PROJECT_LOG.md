# Project log

What changed in which branch, in merge order. Every branch is merged into `main` with a merge
commit (`--no-ff`), so `git log --first-parent main` shows one entry per branch, and every
branch is kept.

| # | Branch | What it adds |
|---|---|---|
| 1 | `feat/phase-1-skeleton` | Package layout, config, CI, this log, interview notes |
| 2 | `feat/phase-2-data-generator` | GA4-schema event generator with planted effects and injected defects |

---

## 1. `feat/phase-1-skeleton`

**Goal:** a project you can install, lint and test before any analysis exists.

**Changes**
- `pyproject.toml`: the `ga4-analytics` package (src layout) with `app`, `notebooks` and `dev`
  extras, plus ruff and pytest config.
- `src/ga4_analytics/config.py`: paths and the analysis window (2020-11-01 to 2021-01-31, the
  same three months as the public GA4 sample).
- `.github/workflows/ci.yml`: ruff and pytest on every push to `main` and every pull request.
- `.gitattributes`: LF line endings in the repo, whatever OS commits.
- `tests/test_smoke.py`: import and config checks.
- `PROJECT_LOG.md` (this file) and `INTERVIEW_NOTES.md`.

**Decisions**
- **No GCP.** The brief targets `bigquery-public-data.ga4_obfuscated_sample_ecommerce`, which
  can only be read through a Google Cloud project. The project instead generates a dataset with
  the same GA4 export schema and runs locally on DuckDB (see phase 2).

## 2. `feat/phase-2-data-generator`

**Goal:** a dataset with the exact GA4 export schema and a known ground truth.

**Changes**
- `src/ga4_analytics/generate.py`: writes `data/events/events_YYYYMMDD.parquet` (92 daily
  files, the `_TABLE_SUFFIX` layout) with nested `event_params`, `items`, `device`, `geo`,
  `traffic_source` and `ecommerce`. Default build: 80,000 users, 139,198 sessions, 856,418
  events, 29 MB, about 1 minute.
- Planted effects (`PLANTED`): mobile checkout friction, a mobile/tablet payment incident on
  2020-12-15, better-converting returning users, opener vs closer channels, Black Friday and
  Cyber Monday peaks.
- Injected defects (`DEFECT_RATES`) that mimic the obfuscated export: `<Other>` and
  `(data deleted)` placeholders, `(not set)` transaction ids, duplicated purchases, NULL
  revenue, missing `ga_session_id` and `session_start`, dropped checkout steps.
- `data/events/_generation.json`: summary counts, planted effects and defect rates.
- `docs/DATA.md`: schema, generation process, ground truth and defect table.
- `tests/test_generate.py`: daily file layout, schema, determinism, ground-truth file,
  session-id coverage.
- `pyproject.toml`: SPDX `license = "MIT"` (setuptools 77+) and a 120-character line limit.

**Decisions**
- Generated data stays out of git (`.gitignore`); anyone rebuilds it bit-for-bit from the seed.
- Session source/medium lives in `event_params` and user `traffic_source` is first touch only,
  as in real GA4, so the SQL has to rebuild session channels the way it would on the real export.
- DuckDB 2.0 removed the `->` lambda syntax and `fetchone()` on relations; the code uses
  `lambda x: ...` and `con.execute(...)`.
