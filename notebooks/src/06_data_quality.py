# %% [markdown]
# # 06 · Data quality: obfuscation defects and what they limit
#
# Google describes its public GA4 sample as obfuscated: it "contains placeholder values including
# `<Other>`, NULL, and `''`" and "has somewhat limited internal consistency". The generator injects the
# same kinds of defects at known rates. This notebook measures each one with SQL
# (`sql/11_data_quality.sql`), then shows what each would do to the headline numbers if the pipeline
# didn't repair it.

# %%
import pandas as pd

from ga4_analytics import quality as Q
from ga4_analytics.db import Warehouse

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 90)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
wh = Warehouse()
checks = Q.run_checks(wh)

# %% [markdown]
# ## Measured defect rates

# %%
checks[["check_name", "unit", "numerator", "denominator", "rate", "injected_rate"]]

# %% [markdown]
# Every injected defect is measured within sampling noise of its injected rate. Duplicates read
# 1.9% against 1.5% injected: 81 found against about 64 ± 8 expected, about two standard errors,
# and both dedup keys behave correctly.
#
# Two checks have no injected rate:
#
# - **`ga_session_number` out of time order (6.4% of users)** was *not* planted. QA found it: the
#   generator gives same-day repeat sessions independent random hours, so a user's "session 2" can
#   start before "session 1". It's left in place because it's exactly the "limited internal
#   consistency" the real export has, and fixing it would silently change every number reported so
#   far. It's documented instead.
# - **traffic_source vs first-session channel (8.5%)** is about 5 points from placeholders and 3.5
#   points from the session-order issue above.

# %% [markdown]
# ## How each defect is handled and what it limits

# %%
checks[["check_name", "rate", "handling", "limits"]]

# %% [markdown]
# ## What the repairs are worth: naive vs modelled numbers

# %%
naive = wh.query("""
SELECT
    count(*) FILTER (WHERE event_name = 'purchase')                       AS purchase_events,
    sum(ecommerce.purchase_revenue_in_usd) FILTER (WHERE event_name = 'purchase') AS revenue_naive,
    count(*) FILTER (WHERE event_name = 'session_start')                  AS session_start_events
FROM events
WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
""").iloc[0]
modelled = wh.query("""
SELECT (SELECT count(*) FROM transactions)              AS transactions,
       (SELECT sum(revenue_usd) FROM transactions)      AS revenue,
       (SELECT count(*) FROM sessions)                  AS sessions
""", dry_run=False).iloc[0]
pd.DataFrame({
    "naive (raw export)": [naive["purchase_events"], naive["revenue_naive"], naive["session_start_events"]],
    "modelled": [modelled["transactions"], modelled["revenue"], modelled["sessions"]],
}, index=["transactions", "revenue (USD)", "sessions"]).assign(
    naive_error=lambda d: d["naive (raw export)"] / d["modelled"] - 1)

# %% [markdown]
# - **Transactions:** counting purchase events overstates orders by about 2% (duplicates).
# - **Revenue:** two errors partly cancel. Duplicates add revenue and NULL revenue removes it, so the
#   naive total looks "about right" by accident. It would drift whenever either rate changes.
# - **Sessions:** counting `session_start` events undercounts sessions by about 2%.

# %% [markdown]
# ## The biggest trap: channel from `traffic_source`

# %%
channel_views = wh.query("""
SELECT channel AS channel, count(*) AS sessions_by_session_params
FROM sessions GROUP BY 1
""", dry_run=False).merge(wh.query("""
SELECT channel_group(first_user_source, first_user_medium) AS channel,
       count(DISTINCT session_key) AS sessions_by_traffic_source
FROM events_flat GROUP BY 1
""", dry_run=False), on="channel", how="outer").fillna(0).set_index("channel")
channel_views["difference"] = channel_views["sessions_by_traffic_source"] / channel_views["sessions_by_session_params"] - 1
channel_views.sort_values("sessions_by_session_params", ascending=False)

# %% [markdown]
# Using `traffic_source` labels every session with the user's *first* channel. Email and Direct,
# which bring people back, lose most of their sessions; Display and Social, which acquire them, gain;
# and about 5% of sessions fall into Unassigned because of the placeholders. Any channel report or
# attribution model built on `traffic_source` is first-click attribution in disguise.

# %% [markdown]
# ## How obfuscation limits conclusions on the real export
#
# | Limitation | Consequence | What still holds |
# |---|---|---|
# | Placeholder values (`<Other>`, NULL, `''`) in dimensions | Segment cuts (country, OS, source) have a blind spot of several percent; small segments can't be analysed | Large-segment comparisons, if the placeholder share is similar across segments |
# | `(not set)` transaction ids | Orders can't be joined to refunds, margins or CRM; dedup needs a fallback key | Order counts after dedup |
# | Limited internal consistency (e.g. revenue vs item sums, session numbering) | Exact reconciliations fail; identities such as revenue = Σ items can't be assumed | Relative comparisons within the dataset |
# | Values are obfuscated, not just masked | Absolute levels (revenue, AOV) are not the store's real numbers; Google says not to compare with the GA demo account | Shapes, ratios and differences between segments; the method itself |
# | Cookie-level `user_pseudo_id` | Cross-device journeys split, so attribution under-credits channels that start journeys on another device | Within-device journeys |
# | Fixed 3-month window | Journeys are cut at both edges; no year-over-year baseline for holidays | Within-window comparisons |
#
# **Bottom line:** on obfuscated data, trust *relative* findings (mobile vs desktop, which step
# breaks, which channel opens vs closes) and present absolute numbers as illustrative. Here the
# relative findings are also validated against the planted ground truth, which the real export can't
# offer.
