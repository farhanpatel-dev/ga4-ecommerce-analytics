# Project log

What changed in which branch, in merge order. Every branch is merged into `main` with a merge
commit (`--no-ff`), so `git log --first-parent main` shows one entry per branch, and every
branch is kept.

| # | Branch | What it adds |
|---|---|---|
| 1 | `feat/phase-1-skeleton` | Package layout, config, CI, this log, interview notes |
| 2 | `feat/phase-2-data-generator` | GA4-schema event generator with planted effects and injected defects |
| 3 | `feat/phase-3-sql-library` | DuckDB runner with dry run, SQL library: events, items, transactions, sessions, users, daily KPIs |
| 4 | `feat/phase-4-funnel` | Funnel by device, channel and user type with significance tests; notebook tooling; notebooks 01-02 |
| 5 | `feat/phase-5-attribution` | Last-click, first-click, linear, Markov (removal effect) and Shapley attribution; notebook 03 |
| 6 | `feat/phase-6-metric-tree` | Revenue metric tree with exact LMDI decomposition of day-over-day and period changes; notebook 04 |

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

## 3. `feat/phase-3-sql-library`

**Goal:** session- and user-level tables built in SQL, with a BigQuery-style dry run before every query.

**Changes**
- `src/ga4_analytics/db.py`: `Warehouse`, which wraps DuckDB with an `events` view over the daily
  files plus a `_TABLE_SUFFIX` column. `dry_run()` reads DuckDB's JSON plan, applies the
  pushed-down file filter to find which daily files survive, takes the projected columns, and sums
  their compressed bytes from the Parquet metadata. `query()` always prints that estimate first.
- `src/ga4_analytics/build.py`: `python -m ga4_analytics.build` runs `sql/00..06` in order. It
  takes about 7 s on the full export.
- `sql/00_macros.sql`: `channel_group()` (GA4-style default channel grouping), `param_str()` and `param_int()`.
- `sql/01_events_flat.sql`: params pivoted in one `UNNEST` pass; events missing `ga_session_id`
  are reattached to the nearest tagged event within 30 minutes.
- `sql/02_event_items.sql`: lateral `UNNEST` of `items`.
- `sql/03_transactions.sql`: purchase dedup (transaction id, or session + basket value when
  `(not set)`) and NULL-revenue repair.
- `sql/04_sessions.sql`: session channel from `event_params`, open and closed funnel flags, and
  new vs returning.
- `sql/05_users.sql`, `sql/06_daily_kpis.sql`: user-level and daily KPI tables.
- `sql/README.md`: lineage, techniques and DuckDB-to-BigQuery dialect notes.
- `tests/conftest.py`, `tests/test_sql_library.py`: build a small warehouse and test session
  coverage, the repair, dedup, closed vs open funnel, the revenue identity and dry-run pruning.

**Decisions**
- **Pivot instead of correlated lookups.** One correlated `UNNEST` subquery per parameter took
  49 s; a single `UNNEST` pass with `max(...) FILTER (WHERE key = ...)` takes 3.8 s. The
  macros stay for ad-hoc queries.
- **Dry-run bytes are column-level**, like BigQuery billing: a query reading two columns of one
  day reports only those column chunks.

**Full-build numbers:** 856,418 events, 195,563 event-items, 4,218 transactions (after dedup),
139,187 sessions, 80,000 users, 92 days.

## 4. `feat/phase-4-funnel`

**Goal:** the six-step funnel by device, channel and new vs returning, with significance tests.

**Changes**
- `sql/07_funnel.sql`: open and closed step counts for every segment in one pass with `GROUPING SETS`.
- `src/ga4_analytics/funnel.py`: Wilson intervals, a pooled two-proportion z-test with a Newcombe
  interval, a chi-square omnibus, Holm correction, Cohen's h, step-by-step tests that locate where
  a segment loses conversion, a user-clustered Poisson bootstrap and a minimum-detectable-effect
  calculator.
- `scripts/build_notebooks.py`: builds `notebooks/src/*.py` (percent format, reviewable in diffs)
  into executed `notebooks/*.ipynb`, so outputs and figures render on GitHub.
- `notebooks/01_data_and_sessions.ipynb`: dry-run demo, the raw nested record, modelled tables, daily traffic.
- `notebooks/02_funnel.ipynb`: funnel, device / channel / user-type tests, mobile step
  diagnosis, bootstrap, MDE.
- `src/ga4_analytics/plots.py`: one colour-blind-safe style; figures saved to `docs/figures/`.
- `tests/test_funnel.py`: Wilson and z-test cross-checked against statsmodels, chi-square, a
  bootstrap design effect on clustered data, MDE, and the funnel on generated data.

**Results (full data)**
- Mobile converts 37.7% worse than desktop (closed funnel, Holm p < 1e-10). Step tests put the
  whole gap at `add_payment_info` (-13.8%) and `purchase` (-28.5%), with nothing significant at
  the four earlier steps. Planted: -15% and -28%. **Recovered.**
- Channel gaps sit at product views and add to cart (Email +, Display and Social -), not checkout.
- Returning sessions are ahead at the first three steps (add to cart +38%); checkout completion
  is equal.
- Tablet (4.2k sessions) can only detect a 26% relative gap: underpowered, so no conclusion.
- Design effect from the user-clustered bootstrap is about 1.0 here, because the generator has no
  user-level purchase propensity. The test suite shows it rising above 1.5 when users differ.

## 5. `feat/phase-5-attribution`

**Goal:** compare five attribution models on the same journeys and explain why they disagree.

**Changes**
- `sql/08_attribution_paths.sql`: one journey per user, made of their session channels up to and
  including the first purchase. It also builds a distinct-path table (journeys, conversions,
  non-conversions, revenue).
- `src/ga4_analytics/attribution.py`:
  - last-click, first-click and linear;
  - a first-order Markov chain with removal effects, solved exactly from the absorbing-chain
    fundamental matrix;
  - exact Shapley values over channel sets, using a zeta transform for subset sums, with two
    games: `count` (the standard one) and `rate`;
  - a position profile showing where each channel sits in a journey;
  - Poisson-bootstrap intervals on credit shares.
- `notebooks/03_attribution.ipynb`: all models side by side, why they disagree, bootstrap
  intervals, a revenue view and recommendations.
- `tests/test_attribution.py`: heuristic rules, conservation of totals, single-touch agreement,
  Shapley-count equal to an equal split over distinct channels, Markov closed form and removal
  effects, symmetry, and channel roles on generated data.

**Results (full data, 4,101 converting journeys, 53% multi-touch)**

| Channel | Last | First | Linear | Markov | Shapley |
|---|---|---|---|---|---|
| Display | 3.6% | 8.7% | 6.0% | 6.3% | 6.1% |
| Organic Social | 4.2% | 9.3% | 6.6% | 6.9% | 6.7% |
| Email | 16.2% | 4.1% | 10.1% | 12.9% | 10.1% |
| Direct | 27.4% | 17.9% | 23.3% | 22.8% | 22.9% |

- When Display or Organic Social is the first or last touch, it is the first touch 84-88% of the
  time. Email is the last touch 88% of the time. This recovers the planted opener and closer roles.
- Bootstrap intervals on Markov shares are about ±1 percentage point, so the gaps between models
  come from assumptions, not noise.

**Decisions**
- **Shapley needs a stated game.** With the count game, the Shapley value is exactly an equal split
  over a journey's distinct channels, so it matches linear. The rate game uses non-converting
  journeys but gives Display and Social negative credit, because it rewards raising conversion
  *rate* rather than driving conversions. Both are shown; neither is the recommended default.
- **Markov is the recommended default**: it uses non-converting journeys and stays stable under
  resampling. All of these models split observed conversions; none of them are causal.

## 6. `feat/phase-6-metric-tree`

**Goal:** revenue = users × conversion × AOV, with each day's change split across the tree.

**Changes**
- `sql/09_metric_tree.sql`: daily users (new and returning), sessions, transactions, items sold and revenue.
- `src/ga4_analytics/metric_tree.py`: tree nodes (sessions/user, transactions/session,
  items/transaction, revenue/item), exact LMDI decomposition with additive and multiplicative
  levels, day-over-day and any-lag decomposition, period totals, and a per-node tree table.
- `notebooks/04_metric_tree.ipynb`: the tree over time, daily contributions by node, the Dec 15 and
  Black Friday waterfalls, week-over-week, and December vs January.
- `tests/test_metric_tree.py`: log mean, exactness, a single-factor change, decomposition on
  generated data and period identities.

**Results (full data)**
- Largest residual over 91 decompositions: 2e-12 (exact).
- **2020-12-15:** revenue -$1,841 (-58%). Transactions per session -62% contributes -$2,035;
  users +$58 and AOV +$129. This points at checkout and matches the planted payment incident.
- **Black Friday:** revenue +$2,254 (+110%), with users contributing +$1,635 (73%). A traffic
  event, not a conversion change.
- **January vs early December:** revenue -26%, almost all from users (-$10.5k); conversion and
  AOV held.
- Day-over-day moves are driven mostly by conversion (48% of days) and AOV (40%) noise.
  Week-over-week is not less noisy (mean contributions are slightly larger), so single-day
  comparisons need anomaly detection first.

**Decisions**
- **LMDI over sequential substitution:** exact, order-independent, and consistent from parent to
  child. Substitution leaves an interaction residual and depends on factor order.
- Users split into new and returning in proportion to each part's change, because users is a sum,
  not a product.
