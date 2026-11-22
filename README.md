# ga4-ecommerce-analytics

Product and marketing analytics on a GA4-schema ecommerce event export, run locally on DuckDB:
a sessionized SQL library, a checkout funnel with significance tests, five multi-touch
attribution models, a revenue metric tree with exact day-over-day decomposition, KPI anomaly
detection, a data-quality audit, an interactive Streamlit dashboard and a stakeholder readout.

> **About the data.** The brief targets Google's public GA4 sample
> (`bigquery-public-data.ga4_obfuscated_sample_ecommerce`), which can only be read through a Google
> Cloud project. This project doesn't use GCP. It generates a dataset with the **same GA4 export
> schema and daily-file layout**, with known effects planted in it, so every analysis can be checked
> against ground truth. Absolute numbers are illustrative. See [docs/DATA.md](docs/DATA.md).

**Start with the [stakeholder readout](docs/READOUT.md).**

## Headline findings

| Finding | Evidence | Planted truth |
|---|---|---|
| Mobile converts 38% worse than desktop, entirely at payment (−14%) and purchase (−29%) | Step-by-step z-tests, Holm-corrected | ×0.85 and ×0.72 on mobile ✔ |
| A mobile/tablet payment outage on 2020-12-15 | STL + robust z; drill-down to device and step | ×0.08 on mobile/tablet that day ✔ |
| Display and Social open journeys; Email and Direct close them | First vs last click; position profile; Markov | Opener and closer channel mixes ✔ |
| Returning sessions add to cart 38% more | Step tests, new vs returning | ×1.25 add to cart ✔ |
| Black Friday was a traffic event (73% of the lift from users) | LMDI metric tree | 2.2× traffic ✔ |

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,app,notebooks]"

python -m ga4_analytics.generate         # ~1 min: 92 daily Parquet files, 856k events
python -m ga4_analytics.build            # ~7 s: runs sql/00-06, dry run printed before each query
pytest -q                                # 56 tests
streamlit run app/dashboard.py           # dashboard on http://localhost:8501
python scripts/build_notebooks.py        # re-executes notebooks/src/*.py into notebooks/*.ipynb
```

Every query is dry-run first, and the dry run prints the daily files and columns it will read and
their compressed bytes, the same idea as BigQuery's bytes processed:

```
-- 01_events_flat.sql
dry run OK | 25.1 MB from 92 daily file(s) [20201101..20210131], columns: device, ecommerce, ...
```

## What's where

| Deliverable | Location |
|---|---|
| SQL library (UNNEST, sessionization, session and user tables, funnel, attribution paths, anomaly drill-down, data quality) | [`sql/`](sql/) · [lineage and dialect notes](sql/README.md) |
| Analysis notebooks | [`notebooks/`](notebooks/): 01 data and sessions, 02 funnel, 03 attribution, 04 metric tree, 05 anomalies, 06 data quality |
| Dashboard | [`app/dashboard.py`](app/dashboard.py) |
| Stakeholder readout | [`docs/READOUT.md`](docs/READOUT.md) |
| Data-quality write-up | [`docs/DATA_QUALITY.md`](docs/DATA_QUALITY.md) |
| Python package | [`src/ga4_analytics/`](src/ga4_analytics/): `generate`, `db` (DuckDB + dry run), `build`, `funnel`, `attribution`, `metric_tree`, `anomaly`, `quality` |
| Branch-by-branch history | [`PROJECT_LOG.md`](PROJECT_LOG.md) |
| Interview talking points | [`INTERVIEW_NOTES.md`](INTERVIEW_NOTES.md) |

## Methods

- **Sessionization:** session key = `user_pseudo_id` + `ga_session_id`; params pivoted in one
  `UNNEST` pass; events missing a session id reattached within GA4's 30-minute timeout; session
  channel from `event_params`, not the first-touch `traffic_source`.
- **Funnel:** open and closed funnels; two-proportion z-tests with Newcombe intervals, a chi-square
  omnibus, Holm correction, Cohen's h, a user-clustered bootstrap and minimum detectable effects.
- **Attribution:** last-click, first-click, linear, a first-order Markov chain (removal effect solved
  exactly from the absorbing chain) and exact Shapley values (count and rate games).
- **Metric tree:** revenue = users × (sessions/user × transactions/session) × (items/transaction ×
  revenue/item), split exactly with LMDI.
- **Anomalies:** robust STL (weekly period, stiffened windows) plus a modified z-score (MAD), with a
  known-event calendar and a segment and funnel-step drill-down.

## Project layout

```
sql/                 SQL library (DuckDB; BigQuery equivalents noted)
src/ga4_analytics/   generator, warehouse + dry run, analysis modules
notebooks/src/       notebook sources (percent format, reviewed in diffs)
notebooks/           executed notebooks (outputs render on GitHub)
app/                 Streamlit dashboard
docs/                data, data quality, readout, figures
tests/               pytest suite (builds a small warehouse from a generated export)
```

## License

MIT © 2026 Farhan Patel
