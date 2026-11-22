# %% [markdown]
# # 05 · KPI anomaly detection and investigation
#
# **Method:** for each daily KPI, fit STL (weekly period, robust mode), then score the remainder with
# a robust z-score, 0.6745·(r − median)/MAD, and flag |z| > 3.5. Counts and revenue are modelled on the
# log scale. Flags on known calendar events are labelled rather than investigated.
#
# Then the biggest unexplained anomaly is investigated: which KPI, which device, which channel, which
# funnel step.

# %%
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ga4_analytics import anomaly as N
from ga4_analytics.db import Warehouse
from ga4_analytics.plots import HIGHLIGHT, NEUTRAL, PALETTE, save, use_style

use_style()
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
pd.set_option("display.float_format", lambda v: f"{v:,.3f}")
wh = Warehouse()
daily = N.load_daily(wh)
results = N.detect_all(daily)

# %% [markdown]
# ## Tuning: why not the STL defaults?

# %%
def alarm_count(seasonal, trend):
    r = N.detect_all(daily, seasonal=seasonal, trend=trend)
    a = r[r["is_anomaly"]]
    return {"seasonal": seasonal or "default (7)", "trend": trend or "default (~11)",
            "alarms": len(a), "alarms on non-event days": int(a["known_event"].isna().sum()),
            "non-event days flagged": a.loc[a["known_event"].isna(), "date"].nunique()}


pd.DataFrame([alarm_count(None, None), alarm_count(7, 29), alarm_count(15, 43),
              alarm_count(N.SEASONAL_WINDOW, N.TREND_WINDOW)])

# %% [markdown]
# With the default windows STL follows the series so closely that it absorbs ordinary noise. Robust
# fitting then leaves large residuals only on extreme days, the residual MAD shrinks, and every
# z-score inflates, giving dozens of alarms on ordinary days. Stiffer windows (seasonal 31, trend
# 43 days) keep the baseline from chasing noise. The cost is that a genuine level shift is absorbed
# more slowly; for a 3-month window of daily data that trade is worth it.

# %% [markdown]
# ## Alarm days

# %%
days = N.alarm_days(results)
days

# %% [markdown]
# Seven of the nine alarm days are known calendar events (Black Friday, Cyber Monday, Christmas)
# or the window's first day, and each of them fires on traffic (users and sessions). **2020-12-15 is
# the only unexplained day where several KPIs fire together**, and it fires on conversion, revenue,
# transactions and checkout completion, not on traffic. 2021-01-27 is a single revenue flag at
# z = 3.9 near the end of the series, where LOESS fits are least stable. Watch it rather than act on it.

# %%
fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
for ax, kpi, title in zip(axes, ["users", "conversion_rate", "revenue"],
                          ["Users", "Conversion (transactions / user)", "Revenue (USD)"], strict=True):
    r = results[results["kpi"] == kpi]
    ax.plot(r["date"], r["value"], color=PALETTE[0], lw=1.2, label="actual")
    ax.plot(r["date"], r["expected"], color=NEUTRAL, lw=1, ls="--", label="expected (trend + weekly)")
    flagged = r[r["is_anomaly"]]
    known = flagged["known_event"].notna()
    ax.scatter(flagged.loc[known, "date"], flagged.loc[known, "value"], color=PALETTE[1], zorder=3,
               label="flag on known event")
    ax.scatter(flagged.loc[~known, "date"], flagged.loc[~known, "value"], color=HIGHLIGHT, zorder=4, s=60,
               label="unexplained flag")
    ax.set_title(title, loc="left")
axes[0].legend(ncols=4, loc="upper right", fontsize=8)
fig.tight_layout()
save(fig, "05_anomalies")

# %%
r = results[results["date"] == pd.Timestamp("2020-12-15")]
r[["kpi", "value", "expected", "pct_vs_expected", "robust_z", "is_anomaly"]]

# %% [markdown]
# ## Investigation · 2020-12-15
#
# Baseline: the 14 days either side (28 days, the anomaly day excluded). For each segment,
# *expected* = that day's sessions × the segment's baseline conversion per session.
#
# ### 1. Was it traffic or conversion?

# %%
counts = N.drilldown_counts(wh, "2020-12-15", baseline_days=14)
allrow = counts[counts["dimension"] == "all"].set_index("period")
pd.DataFrame({
    "anomaly day": allrow.loc["anomaly", ["sessions", "add_to_cart", "begin_checkout", "purchase"]],
    "baseline per day": allrow.loc["baseline", ["sessions", "add_to_cart", "begin_checkout", "purchase"]]
    / allrow.loc["baseline", "days"],
})

# %% [markdown]
# Sessions, carts and checkout starts were normal or slightly above normal. Purchases halved. People
# arrived and shopped as usual; they didn't finish.
#
# ### 2. Which device?

# %%
dev = N.segment_shortfall(counts, "device")
dev

# %%
fig, ax = plt.subplots(figsize=(8, 3.5))
x = np.arange(len(dev))
ax.bar(x - 0.2, dev["baseline_rate"] * 100, 0.4, label="baseline", color=NEUTRAL)
ax.bar(x + 0.2, dev["anomaly_rate"] * 100, 0.4, label="2020-12-15", color=HIGHLIGHT)
ax.set_xticks(x, dev.index)
ax.set_ylabel("sessions with a purchase (%)")
ax.set_title("Mobile and tablet purchases almost stopped")
ax.legend()
save(fig, "05_incident_by_device")

# %% [markdown]
# Mobile made **2 purchases from 835 sessions** against about 19 expected (−90%), and tablet made 0
# against 2 expected. Desktop is also below baseline (28 vs 43), which is borderline (p = 0.02).
# Step 3 checks whether desktop's dip has the same shape.
#
# ### 3. Which funnel step?

# %%
mobile = N.step_comparison(counts, "device", "mobile")
desktop = N.step_comparison(counts, "device", "desktop")
pd.concat({"mobile": mobile, "desktop": desktop}, axis=1)

# %% [markdown]
# On mobile everything up to `add_payment_info` is normal, then **purchase given payment fell from
# 50% to 6%** (p < 0.001). Users entered payment details and the order did not go through.
#
# Desktop's dip has a different shape. Every step is slightly lower and none is significant on its
# own: diffuse noise, not a broken step. With three devices tested, one borderline p = 0.02 is what
# chance produces about one time in eight, so desktop isn't part of the incident.
#
# ### 4. Channel? (ruling out a marketing cause)

# %%
N.segment_shortfall(counts, "channel")[["sessions", "actual", "expected", "shortfall", "share_of_shortfall", "p_value"]]

# %% [markdown]
# Every channel is below expected, and no single channel or campaign accounts for most of the
# shortfall. Organic and Paid Search show the largest gaps (together about half). A marketing cause,
# such as a broken campaign link or a bad promo, would concentrate in one channel and leave the
# others normal. The device split explains the day far better than the channel split.
#
# ### 5. Operating system

# %%
N.segment_shortfall(counts, "operating_system")[["sessions", "actual", "expected", "shortfall", "p_value"]]

# %% [markdown]
# iOS and Android both collapse, to 1 purchase each against about 10 expected, so it isn't one
# platform or browser engine. It points to something shared by all mobile web checkouts, such as the
# payment provider's mobile flow. Windows is also below baseline: that's the diffuse desktop dip from
# step 3, while macOS, the other big desktop OS, is normal.
#
# ## Finding
#
# **On 2020-12-15 mobile and tablet orders failed at the final payment step all day.** Purchase given
# `add_payment_info` fell from about 50% to 6% on mobile; traffic, carts, checkout starts and desktop
# checkout were normal. The impact was about 19 lost mobile and tablet orders (about $900 at the day's
# AOV). Total revenue was **about $1,100 below** the detector's expected value, during peak
# pre-Christmas trading. On top of that, customers who reached payment and failed may not come back.
#
# *Ground truth check:* the generator planted exactly this: P(purchase | payment) × 0.08 on mobile and
# tablet for 2020-12-15. Detection, the device split and the step location are all recovered, and
# the detector stayed quiet on desktop, which was not affected.
#
# ## Recommendations
#
# 1. **Alert on checkout completion by device**, not just on revenue. Revenue only reached z = −4.1
#    on the day, while mobile purchase-given-payment fell 88%, a far stronger signal that an hourly
#    check could catch early.
# 2. **Add a synthetic mobile checkout test** against the payment provider in production every
#    hour.
# 3. **Keep a calendar of known events** in the detector so holiday traffic doesn't bury real
#    incidents. Seven of nine alarm days here were calendar events.
