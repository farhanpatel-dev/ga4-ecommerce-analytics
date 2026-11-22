# Interview notes

Talking points for this project, filled in phase by phase.

## The 30-second pitch

I built a product and marketing analytics project on a GA4-schema ecommerce event export, run
locally on DuckDB. It covers a sessionized SQL library, a checkout funnel with significance
tests, five attribution models, a revenue metric tree with day-over-day decomposition, KPI
anomaly detection, a Streamlit dashboard and a stakeholder readout.

## Why synthetic data, and why that is a strength

- The brief named Google's public GA4 sample, which needs a Google Cloud project to read. I
  chose not to use GCP, so I wrote a generator that reproduces the GA4 export schema exactly:
  nested `event_params` and `items`, `ga_session_id`, user-level `traffic_source`.
- Because I control the generator, I planted effects with known size. That lets me validate the
  analysis: if the funnel test, attribution or anomaly detector misses a planted effect, the
  method is wrong. On the real export there is no ground truth to check against.
- The honest limit: findings describe the planted world, not the Google Merchandise Store.

## Data generation

- **Schema fidelity:** same nested columns and daily-file layout as the BigQuery export. Session
  source/medium sits in `event_params`, and `traffic_source` is first touch only. That is the
  classic GA4 trap: people attribute sessions to `traffic_source` and silently get first-touch
  attribution for every session.
- **Ground truth:** five planted effects with known size (see `docs/DATA.md`). Each later phase
  has to recover one.
- **Defects on purpose:** placeholders, `(not set)` transaction ids, duplicate purchases, NULL
  revenue, missing session ids. These are the problems you hit in the real obfuscated export, so
  the SQL has to handle them rather than assume clean data.
- **Likely question: "Isn't synthetic data cheating?"** It's a validation harness. On real data
  you can't tell whether a Markov model or an anomaly detector is right; here I can check it
  against the planted truth. The trade-off is that real data has messier, unknown effects, and I
  say so in the readout.
