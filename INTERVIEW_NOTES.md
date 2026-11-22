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
