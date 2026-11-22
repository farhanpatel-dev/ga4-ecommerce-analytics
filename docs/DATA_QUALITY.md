# Data quality: obfuscation defects and how they limit conclusions

Google describes its public GA4 sample (`bigquery-public-data.ga4_obfuscated_sample_ecommerce`) as
obfuscated: it contains placeholder values including `<Other>`, NULL and `''`, and it has
"somewhat limited internal consistency". It also warns that the data can't be compared with the
Google Analytics demo account. The generator here injects the same kinds of defects at known rates
(`DEFECT_RATES` in `src/ga4_analytics/generate.py`). `sql/11_data_quality.sql` measures them, and
`notebooks/06_data_quality.ipynb` shows the full analysis.

## Measured defects (full build)

| Check | Unit | Measured | Injected | Handling | What it limits |
|---|---|---|---|---|---|
| `traffic_source` = `<Other>` / `(data deleted)` | users | 5.07% | 5.0% | Channel from session `event_params`; first-touch channel set to Unassigned | Acquisition reports on `traffic_source` lose ~5% of users |
| `geo.country` = `<Other>` | users | 3.92% | 4.0% | Own bucket | Every country undercounted; small countries unusable |
| `device.operating_system` = `<Other>` | users | 5.03% | 5.0% | Own bucket | 5% blind spot in OS diagnosis |
| `user_first_touch_timestamp` NULL | users | 0.95% | 1.0% | First session derived from events | Cohorts must use event-derived first session |
| `page_location` = `<Other>` | page views | 10.03% | 10.0% | Not used | Page-level funnels lose 10% of views |
| Event without `ga_session_id` | events | 1.00% | 1.0% | Reattached to nearest tagged event within 30 min | Without repair, ~1% of events (funnel events too) fall out of sessions |
| `transaction_id` = `(not set)` | purchases | 7.93% | 8.0% | Dedup falls back to session + basket value | Orders can't join refunds, margin or CRM data |
| `purchase_revenue_in_usd` NULL | purchases | 2.02% | 2.0% | Repaired from item revenue | Without repair, revenue understated ~2% |
| Duplicate purchase events | purchases | 1.88% | 1.5% | Deduplicated | Without dedup, orders overstated ~2% |
| `item_name` = `(not set)` | items | 2.99% | 3.0% | NULL in `event_items` | Product rankings lose 3% of items |
| Session without `session_start` | sessions | 1.99% | 2.0% | Sessions built from any event | Counting `session_start` undercounts sessions by 2% |
| Purchase session missing an earlier checkout step | purchase sessions | 8.16% | 7.8%* | Open and closed funnels both reported | Closed-funnel late-step rates biased down a few percent |
| `ga_session_number` out of time order | users | 6.39% | not planted | Journeys ordered by timestamp | New vs returning labels noisy for ~6% of users |
| `traffic_source` channel ≠ first-session channel | users | 8.50% | n/a | Session channel is the source of truth | Placeholders plus session-order noise |

\* 2% per step across 4 intermediate checkout events: 1 − 0.98⁴ = 7.8%.

Duplicates measure 81 against about 64 ± 8 expected, which is within sampling noise; both dedup keys
behave correctly.

### Found by QA, not planted

`ga_session_number` runs out of time order for 6.4% of users. The generator gives same-day repeat
sessions independent random hours, so a "session 2" can start before "session 1". It was left in
place because it's exactly the kind of internal inconsistency the real export has, and fixing it
would change every number reported in earlier phases. The pipeline orders journeys by timestamp, so
attribution is unaffected. New vs returning labels follow the exported `ga_session_number`, as they
would on the real data.

## What the repairs are worth

| Metric | Naive (raw export) | Modelled | Naive error |
|---|---|---|---|
| Transactions | 4,299 purchase events | 4,218 | +1.9% |
| Revenue | $188,668 | $189,306 | −0.3% (two errors cancelling) |
| Sessions | 136,427 `session_start` events | 139,187 | −2.0% |

The naive revenue looks right by accident: duplicates add revenue and NULL revenue removes it. It
would drift whenever either rate changes.

**The biggest trap is channel from `traffic_source`.** It labels every session with the user's
*first* channel. Email loses 79% of its sessions and Direct 36%, Display gains 32% and Organic Social
26%, and 7,091 sessions fall into Unassigned. A channel report built on `traffic_source` is
first-click attribution in disguise.

## How obfuscation limits conclusions on the real export

| Limitation | Consequence | What still holds |
|---|---|---|
| Placeholder values in dimensions | Segment cuts have blind spots of several percent; small segments can't be analysed | Large-segment comparisons, if placeholder shares are similar across segments |
| `(not set)` transaction ids | No joins to refunds, margin or CRM; dedup needs a fallback key | Order counts after dedup |
| Limited internal consistency | Exact reconciliations (revenue = Σ items, session numbering) can't be assumed | Relative comparisons within the dataset |
| Values obfuscated, not just masked | Absolute revenue and AOV aren't the store's real numbers | Shapes, ratios and segment differences; the methods |
| Cookie-level `user_pseudo_id` | Cross-device journeys split; attribution under-credits channels that start journeys on another device | Within-device journeys |
| Fixed 3-month window | Journeys cut at both edges; no year-over-year holiday baseline | Within-window comparisons |

**Bottom line:** on obfuscated data, trust *relative* findings (mobile vs desktop, which step breaks,
which channels open vs close journeys) and present absolute numbers as illustrative. In this project
the relative findings are also checked against the planted ground truth, which the real export
can't offer.
