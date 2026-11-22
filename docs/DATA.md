# The dataset

## Why it is generated

The brief targets `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, the Google
Merchandise Store GA4 export. That dataset is only published in BigQuery, so reading it needs a
Google Cloud project. This project does not use GCP. Instead, `ga4_analytics.generate` writes an
export with the **same schema and file layout**, and every query runs locally on DuckDB.

The data is **synthetic**. Numbers in this repo describe the generated world, not the Google
Merchandise Store.

## Layout

```
data/events/events_20201101.parquet   one file per day (the BigQuery _TABLE_SUFFIX convention)
...
data/events/events_20210131.parquet
data/events/_generation.json           summary counts, planted effects, defect rates
```

Rebuild with `python -m ga4_analytics.generate --users 80000 --seed 42` (about 1 minute). The
default build has 80,000 users, 139,198 sessions and 856,418 events (29 MB).

## Schema (subset of the GA4 BigQuery export)

| Column | Type | Notes |
|---|---|---|
| `event_date` | STRING `YYYYMMDD` | |
| `event_timestamp` | INT64, microseconds UTC | |
| `event_name` | STRING | `session_start`, `first_visit`, `page_view`, the six funnel events |
| `event_params` | ARRAY<STRUCT<key, value STRUCT<string_value, int_value, float_value, double_value>>> | `ga_session_id`, `ga_session_number`, `source`, `medium`, `campaign`, `page_location`, `engagement_time_msec` |
| `user_pseudo_id` | STRING | cookie-level user id |
| `user_first_touch_timestamp` | INT64 | |
| `device` | STRUCT | `category`, `operating_system`, `mobile_brand_name`, `language`, `web_info.browser` |
| `geo` | STRUCT | `continent`, `country`, `city` |
| `traffic_source` | STRUCT | `name`, `medium`, `source`: the **user's first-touch** acquisition, as in GA4 |
| `ecommerce` | STRUCT | `transaction_id`, `purchase_revenue_in_usd`, `total_item_quantity`, `unique_items` |
| `items` | ARRAY<STRUCT> | `item_id`, `item_name`, `item_category`, `price_in_usd`, `quantity`, `item_revenue_in_usd` |

As in GA4, session-level source/medium is **not** a column. It sits in `event_params` on the
`session_start` and first `page_view` events, so channel attribution has to be rebuilt in SQL.

## How behaviour is generated

1. **Users** arrive on a day drawn from a weekly cycle with holiday peaks (Black Friday 2.2×,
   Cyber Monday 1.8×, pre-Christmas 1.3×, Christmas dip 0.6×, January 0.85×).
2. Each user gets a device, country, browser and **1 + Geometric(0.55)** sessions spaced by
   exponential gaps (mean 6 days).
3. **Session channel**: the first session draws from an acquisition mix (Organic, Paid, Display,
   Social heavy); later sessions from a return mix (Direct and Email heavy).
4. **Funnel**: each step happens with probability P(step | previous step), adjusted by device,
   channel quality, new vs returning and date.
5. Carts hold 1 + Poisson(0.8) items from a 240-product catalog with log-normal prices.

## Planted effects (the ground truth)

| Effect | Size | Which analysis should find it |
|---|---|---|
| Mobile checkout friction | P(add_payment_info \| shipping) × 0.85, P(purchase \| payment) × 0.72 on mobile | Funnel by device, significance tests |
| Payment incident, 2020-12-15 | P(purchase \| payment) × 0.08 on mobile and tablet for one day | Anomaly detection and drill-down |
| Returning users convert better | add_to_cart × 1.25, begin_checkout × 1.10 | Funnel new vs returning |
| Channel roles | Display, Organic Social open journeys; Direct, Email close them | Attribution: first vs last click disagree |
| Black Friday / Cyber Monday | traffic up to 2.2×, add_to_cart × 1.15 | Metric tree and anomalies (explained spikes) |

## Injected defects (mimicking the obfuscated export)

| Defect | Rate |
|---|---|
| User `traffic_source` = `<Other>` / `(data deleted)` | 3% / 2% of users |
| `geo.country` = `<Other>` | 4% of users |
| `device.operating_system` = `<Other>` | 5% of users |
| `page_location` = `<Other>` | 10% of page views |
| `transaction_id` = `(not set)` | 8% of purchases |
| Purchase sent twice | 1.5% of purchases |
| `purchase_revenue_in_usd` NULL | 2% of purchases |
| Item name and category `(not set)` | 3% of items |
| Event without `ga_session_id` | 1% of events |
| Missing `session_start` | 2% of sessions |
| Checkout step dropped in a session that still purchased | 2% per step |
| `user_first_touch_timestamp` NULL | 1% of users |

`docs/DATA_QUALITY.md` (phase 8) measures each of these with SQL and explains what it does to
the conclusions.
