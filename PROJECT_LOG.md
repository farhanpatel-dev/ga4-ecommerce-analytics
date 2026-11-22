# Project log

What changed in which branch, in merge order. Every branch is merged into `main` with a merge
commit (`--no-ff`), so `git log --first-parent main` shows one entry per branch, and every
branch is kept.

| # | Branch | What it adds |
|---|---|---|
| 1 | `feat/phase-1-skeleton` | Package layout, config, CI, this log, interview notes |

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
