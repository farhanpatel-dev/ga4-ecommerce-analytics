# SQL library

DuckDB SQL over the GA4 export. Every file reads the raw `events` view through a
`_TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix` filter (BigQuery: `@start_suffix`), and
each one notes its BigQuery equivalent where the dialects differ.

Run the model files in order with `python -m ga4_analytics.build`. Each statement is dry-run first,
and the dry run prints the daily files and columns it would read and their compressed bytes.

## Lineage

```
events (view over data/events/events_YYYYMMDD.parquet)
  ├── 01_events_flat.sql   events_flat    one row per event; params pivoted; sessions repaired
  │     ├── 03_transactions.sql  transactions   deduplicated purchases, revenue repaired
  │     ├── 04_sessions.sql      sessions       one row per session; channel; funnel flags
  │     │     ├── 05_users.sql   users          one row per user; first/last channel; purchase timing
  │     │     └── 06_daily_kpis.sql daily_kpis  users, conversion, AOV, revenue per day
  └── 02_event_items.sql  event_items    one row per (event, item)
00_macros.sql: channel_group(), param_str(), param_int()
```

## Techniques worth pointing at

| Technique | Where |
|---|---|
| `UNNEST` + conditional aggregation to pivot `event_params` | `01_events_flat.sql` (`params` CTE) |
| Correlated `UNNEST` lookups, wrapped as macros for ad-hoc work | `00_macros.sql` |
| Lateral `UNNEST` of `items` | `02_event_items.sql` |
| Gaps-and-islands session repair with `last_value/first_value ... IGNORE NULLS` | `01_events_flat.sql` |
| Dedup by `row_number()` over a fallback key | `03_transactions.sql` |
| `arg_min ... FILTER` for "first non-null value in time order" | `04_sessions.sql` |
| Open and closed (ordered) funnel flags | `04_sessions.sql` |
| `GROUPING SETS` for one-pass funnel cuts | `07_funnel.sql` (phase 4) |

## Dialect notes (DuckDB → BigQuery)

| DuckDB | BigQuery |
|---|---|
| `FROM events WHERE _TABLE_SUFFIX ...` | ``FROM `...events_*` WHERE _TABLE_SUFFIX ...`` |
| `$start_suffix` | `@start_suffix` |
| `make_timestamp(event_timestamp)` | `TIMESTAMP_MICROS(event_timestamp)` |
| `strptime(event_date, '%Y%m%d')::DATE` | `PARSE_DATE('%Y%m%d', event_date)` |
| `FROM events AS e, unnest(e.items) AS t(i)` | `FROM events AS e, UNNEST(e.items) AS i` |
| `arg_min(x, t)` | `ARRAY_AGG(x ORDER BY t LIMIT 1)[OFFSET(0)]` |
| `count(*) FILTER (WHERE c)` | `COUNTIF(c)` |
| `CREATE MACRO` | `CREATE TEMP FUNCTION` |
