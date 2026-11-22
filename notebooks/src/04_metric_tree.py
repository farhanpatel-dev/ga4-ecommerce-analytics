# %% [markdown]
# # 04 · Revenue metric tree
#
# ```
# revenue
# ├── users ─────────────── new users + returning users
# ├── conversion ────────── sessions / user × transactions / session
# └── AOV ───────────────── items / transaction × revenue / item
# ```
#
# Day-over-day revenue changes are split across the tree with **LMDI** (logarithmic mean Divisia
# index): exact (no residual), independent of factor order, and consistent between levels. See
# `src/ga4_analytics/metric_tree.py`.

# %%
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ga4_analytics import metric_tree as M
from ga4_analytics.db import Warehouse
from ga4_analytics.plots import NEUTRAL, PALETTE, save, use_style

use_style()
pd.set_option("display.width", 180)
pd.set_option("display.float_format", lambda v: f"{v:,.3f}")
wh = Warehouse()
daily = M.load_daily(wh)
dod = M.daily_decomposition(daily)
print(f"largest residual across {len(dod)} day-over-day decompositions: {dod['check_residual'].abs().max():.2e}")

# %% [markdown]
# ## The tree over time

# %%
fig, axes = plt.subplots(4, 1, figsize=(10, 9), sharex=True)
for ax, col, title in zip(axes, ["revenue_usd", "users", "conversion", "aov"],
                          ["Revenue (USD)", "Users", "Conversion (transactions / user)", "AOV (USD)"], strict=True):
    ax.plot(daily["date"], daily[col], color=PALETTE[0])
    ax.set_title(title, loc="left")
fig.tight_layout()
save(fig, "04_tree_over_time")

# %% [markdown]
# Users carry the seasonal shape (Black Friday, pre-Christmas, the January slump). Conversion is flat
# apart from one sharp dip in mid-December. AOV is noisy day to day because about 45 orders a day is a
# small sample.

# %% [markdown]
# ## Which node drives each day's change?

# %%
contrib = dod[["date", "revenue_change", "users", "conversion", "aov"]].copy()
share_abs = contrib[["users", "conversion", "aov"]].abs()
contrib["main_driver"] = share_abs.idxmax(axis=1)
contrib.loc[contrib["revenue_change"].abs().nlargest(10).index].sort_values("date")

# %%
fig, ax = plt.subplots(figsize=(11, 4.5))
bottom_pos = np.zeros(len(dod))
bottom_neg = np.zeros(len(dod))
for col, color in zip(["users", "conversion", "aov"], PALETTE, strict=False):
    vals = dod[col].to_numpy()
    ax.bar(dod["date"], np.where(vals > 0, vals, 0), bottom=bottom_pos, color=color, label=col, width=0.85)
    ax.bar(dod["date"], np.where(vals < 0, vals, 0), bottom=bottom_neg, color=color, width=0.85)
    bottom_pos += np.where(vals > 0, vals, 0)
    bottom_neg += np.where(vals < 0, vals, 0)
ax.plot(dod["date"], dod["revenue_change"], color="black", lw=1, label="revenue change")
ax.axhline(0, color="black", lw=0.6)
ax.set_title("Day-over-day revenue change, split by tree node (USD)")
ax.legend(ncols=4)
save(fig, "04_dod_contributions")

# %%
drivers = contrib.assign(abs_change=contrib["revenue_change"].abs())
summary = drivers.groupby("main_driver").agg(days=("date", "count"), mean_abs_change=("abs_change", "mean"))
summary["share_of_days"] = summary["days"] / summary["days"].sum()
summary

# %% [markdown]
# Across all days, AOV and conversion noise drive most day-over-day moves. That's the trouble with
# day-over-day comparisons on about 45 orders a day: most moves are noise. Notebook 05 adds a
# seasonal baseline and a robust threshold to separate signal from it.

# %% [markdown]
# ## Case 1 · 2020-12-15: revenue fell 58%

# %%
d = daily.set_index(daily["date"].dt.strftime("%Y-%m-%d"))
tree_1215 = M.tree_frame(d.loc["2020-12-14"], d.loc["2020-12-15"])
tree_1215

# %%
def waterfall(tree: pd.DataFrame, title: str, name: str) -> None:
    leaves = tree[tree["node"].str.startswith("        ")]
    labels = leaves["node"].str.strip().tolist()
    vals = leaves["contribution_usd"].to_numpy()
    start = tree["before"].iloc[0]
    fig, ax = plt.subplots(figsize=(10, 4))
    running = start
    ax.bar("before", start, color=NEUTRAL)
    for lab, v in zip(labels, vals, strict=True):
        ax.bar(lab.replace(" / ", "/\n"), v, bottom=running if v >= 0 else running + v,
               color=PALETTE[2] if v >= 0 else PALETTE[5])
        running += v
    ax.bar("after", tree["after"].iloc[0], color=NEUTRAL)
    ax.set_ylabel("revenue (USD)")
    ax.set_title(title)
    plt.setp(ax.get_xticklabels(), fontsize=8)
    save(fig, name)


waterfall(tree_1215, "2020-12-14 → 2020-12-15: transactions per session explain the fall", "04_waterfall_1215")

# %% [markdown]
# Traffic was normal (users +3%) and baskets were normal (AOV +6%). **Transactions per session fell
# 62%**, and that one leaf accounts for −$2,035 of the −$1,841 change. People came and shopped but did
# not complete purchases, so something broke in checkout. Notebook 05 confirms it's an anomaly and
# drills into device and funnel step.

# %% [markdown]
# ## Case 2 · Black Friday, 2020-11-27

# %%
tree_bf = M.tree_frame(d.loc["2020-11-26"], d.loc["2020-11-27"])
tree_bf

# %% [markdown]
# Black Friday more than doubled revenue, and the users node explains about 73% of it: a traffic
# event, not a conversion change. A marketing team that read it as "the site converted better on
# Black Friday" would be wrong.

# %% [markdown]
# ## Does week-over-week help?

# %%
wow = M.daily_decomposition(daily, lag=7)
pd.DataFrame({
    "day-over-day": dod[["users", "conversion", "aov"]].abs().mean(),
    "week-over-week": wow[["users", "conversion", "aov"]].abs().mean(),
}).rename_axis("mean absolute contribution (USD)")

# %% [markdown]
# No. Week-over-week moves are as large as day-over-day ones, and larger for users, because a 7-day
# gap also picks up the holiday trend. The weekday cycle here is mild, so it isn't the main source of
# noise. The noise is sampling: conversion and AOV rest on about 45 orders a day. Comparing two single
# days, whichever two you pick, can't separate that noise from a real change.

# %% [markdown]
# ## Period view: first half of December vs January

# %%
dec = M.period_totals(daily, "2020-12-01", "2020-12-14")
jan = M.period_totals(daily, "2021-01-04", "2021-01-17")
M.tree_frame(dec, jan)

# %% [markdown]
# Two comparable 14-day windows: January revenue is down mostly through users (the post-holiday
# slump), while conversion and AOV hold. The fix there is acquisition, not the site.
#
# ## Takeaways
#
# 1. The tree turns "revenue is down" into one named leaf. On Dec 15 that leaf is transactions per
#    session, which points at checkout, not marketing.
# 2. Peaks and slumps are traffic (users); conversion is stable except on the incident day.
# 3. Single-day comparisons (day over day or week over week) are mostly noise at this volume. Pair
#    the tree with anomaly detection (a seasonal baseline plus a robust threshold, notebook 05) before
#    acting on a move. Then use the tree to explain the moves that pass.
