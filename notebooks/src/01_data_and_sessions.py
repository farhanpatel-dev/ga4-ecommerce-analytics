# %% [markdown]
# # 01 · Data and sessionization
#
# What the export looks like, what the dry run reports before each query, and the session- and
# user-level tables built by `sql/`. The data is **synthetic** with the GA4 export schema
# (see `docs/DATA.md`).

# %%
import pandas as pd

from ga4_analytics.db import Warehouse
from ga4_analytics.plots import save, use_style

use_style()
pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 30)
wh = Warehouse()

# %% [markdown]
# ## Dry run before every query
#
# The runner validates the plan, applies the pushed-down `_TABLE_SUFFIX` filter to find which daily
# files survive, and sums the compressed bytes of only the columns the query reads.

# %%
one_day = """
SELECT event_name, count(*) AS events
FROM events
WHERE _TABLE_SUFFIX = '20201215'
GROUP BY 1 ORDER BY 2 DESC
"""
print(wh.dry_run(one_day).summary())
wh.query(one_day, dry_run=False)

# %%
whole_window = """
SELECT event_name, count(*) AS events, count(DISTINCT user_pseudo_id) AS users
FROM events
WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix
GROUP BY 1 ORDER BY 2 DESC
"""
wh.query(whole_window)

# %% [markdown]
# ## Raw nested record
#
# One purchase event as exported: `event_params` and `items` are arrays of structs, and session
# source/medium lives in `event_params`, not in `traffic_source` (which is first touch).

# %%
raw = wh.query("""
SELECT event_name, user_pseudo_id, event_params, traffic_source, ecommerce, items
FROM events
WHERE _TABLE_SUFFIX = '20201214' AND event_name = 'purchase'
LIMIT 1
""")
for col in raw.columns:
    print(f"{col}: {raw.at[0, col]}\n")

# %% [markdown]
# ## Modelled tables

# %%
counts = wh.query("""
SELECT 'events_flat' AS table_name, count(*) AS rows FROM events_flat UNION ALL
SELECT 'event_items', count(*) FROM event_items UNION ALL
SELECT 'transactions', count(*) FROM transactions UNION ALL
SELECT 'sessions', count(*) FROM sessions UNION ALL
SELECT 'users', count(*) FROM users UNION ALL
SELECT 'daily_kpis', count(*) FROM daily_kpis
""")
counts

# %%
wh.query("""
SELECT session_key, session_date, channel, device_category, user_type, page_views,
       funnel_depth, transactions, round(revenue_usd, 2) AS revenue_usd
FROM sessions
WHERE transactions > 0
ORDER BY session_start_ts
LIMIT 5
""")

# %% [markdown]
# ## Sessions and users at a glance

# %%
overview = wh.query("""
SELECT
    count(*)                                         AS sessions,
    count(DISTINCT user_pseudo_id)                   AS users,
    round(count(*) / count(DISTINCT user_pseudo_id), 2) AS sessions_per_user,
    round(avg(page_views), 2)                        AS pages_per_session,
    round(100 * avg(reached_purchase::INT), 2)       AS session_conversion_pct,
    sum(transactions)                                AS transactions,
    round(sum(revenue_usd), 0)                       AS revenue_usd,
    round(sum(revenue_usd) / sum(transactions), 2)   AS aov_usd
FROM sessions
""")
overview.T.rename(columns={0: "value"})

# %%
import matplotlib.pyplot as plt

daily = wh.query("SELECT date, users, sessions, transactions, revenue_usd FROM daily_kpis ORDER BY date")
fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
axes[0].plot(daily["date"], daily["users"], label="users")
axes[0].plot(daily["date"], daily["sessions"], label="sessions")
axes[0].set_title("Daily traffic")
axes[0].legend()
axes[1].bar(daily["date"], daily["revenue_usd"], width=0.8)
axes[1].set_title("Daily revenue (USD)")
fig.tight_layout()
save(fig, "01_daily_traffic_revenue")

# %%
users = wh.query("""
SELECT first_session_channel AS channel,
       count(*) AS users,
       round(100 * avg(purchased::INT), 2) AS purchaser_pct,
       round(avg(sessions), 2) AS sessions_per_user,
       round(avg(sessions_to_first_purchase), 2) AS sessions_to_first_purchase
FROM users
GROUP BY 1 ORDER BY users DESC
""")
users

# %% [markdown]
# **Takeaways**
#
# - The dry run makes cost visible before anything runs: a one-day, two-column query reads a
#   fraction of a percent of what the full flatten reads.
# - Sessions convert at about 3% and AOV is about $45; traffic peaks on Black Friday and Cyber
#   Monday, and revenue dips over Christmas.
# - Purchasers need more than one session on average, so a single-session view of the funnel
#   undercounts the role of the channels that bring users back (see notebook 03).
