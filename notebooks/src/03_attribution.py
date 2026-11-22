# %% [markdown]
# # 03 · Multi-touch attribution
#
# Five models on the same journeys: **last-click, first-click, linear, Markov chain (removal
# effect) and Shapley value**, plus a Shapley variant to show how much the game definition matters.
#
# A journey is a user's session channels in time order, up to and including their first purchase
# (`sql/08_attribution_paths.sql`). The unit of credit is the first conversion per user.

# %%
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ga4_analytics import attribution as A
from ga4_analytics.db import Warehouse
from ga4_analytics.plots import PALETTE, save, use_style

use_style()
pd.set_option("display.width", 180)
pd.set_option("display.float_format", lambda v: f"{v:,.3f}")
wh = Warehouse()
paths = A.load_paths(wh)

# %% [markdown]
# ## The journeys

# %%
summary = pd.Series({
    "journeys": int(paths["journeys"].sum()),
    "converting journeys": int(paths["conversions"].sum()),
    "distinct paths": len(paths),
    "multi-touch share of conversions": paths.loc[paths["path"].map(len) > 1, "conversions"].sum()
    / paths["conversions"].sum(),
})
summary

# %%
top = paths.assign(path=paths["path"].map(" > ".join), conv_rate=paths["conversions"] / paths["journeys"])
cols = ["path", "journeys", "conversions", "conv_rate", "revenue_usd"]
top.sort_values("conversions", ascending=False).head(12)[cols]

# %% [markdown]
# ## Five models side by side

# %%
core = {k: v for k, v in A.MODELS.items() if k != "shapley_rate"}
credit = A.compare(paths, models=core)
share = A.shares(credit)
share

# %%
fig, ax = plt.subplots(figsize=(10, 4.8))
order = share.sort_values("last_click").index
y = np.arange(len(order))
h = 0.16
for i, model in enumerate(core):
    ax.barh(y + i * h, share.loc[order, model] * 100, h, label=model.replace("_", " "), color=PALETTE[i])
ax.set_yticks(y + 2 * h, order)
ax.set_xlabel("share of conversions credited (%)")
ax.set_title("Same journeys, five answers")
ax.legend(loc="lower right")
save(fig, "03_attribution_models")

# %% [markdown]
# ## Why the models disagree
#
# ### 1. Channels play different roles in a journey

# %%
profile = A.position_profile(paths)
profile

# %% [markdown]
# In converting multi-touch journeys, when Display or Organic Social is the first or last touch, it
# is the first touch 84-88% of the time. Email is the last touch 88% of the time and Direct 69%. So:
#
# - **Last-click** gives everything to the closer and starves openers. Display looks worthless.
# - **First-click** does the opposite: Email, which closes, looks small.
# - **Linear** splits each journey evenly, so it lands in the middle by construction.
#
# All three heuristics are *rules about position*. None of them look at non-converting journeys, so
# none can tell whether a channel's presence changes the odds of converting.
#
# *Ground truth check:* the generator draws first sessions from an acquisition mix heavy in Display
# and Social and return sessions from a mix heavy in Direct and Email. The position profile and the
# first-vs-last gap recover exactly that.

# %% [markdown]
# ### 2. Markov uses the journeys that did not convert

# %%
re = A.removal_effects(paths).sort_values(ascending=False)
re.to_frame().assign(credit_share=re / re.sum())

# %% [markdown]
# A channel's removal effect is how much P(conversion) falls if every visit to it ended the journey.
# Organic Search and Direct are on so many converting routes that removing either cuts conversions by
# about 40%. The effects sum to well over 100% because routes overlap, so credit is the normalised
# share. Markov sits close to linear here because most journeys are one or two touches. With longer
# journeys and real channel interactions the two diverge more.

# %% [markdown]
# ### 3. Shapley depends entirely on how you define a coalition's value

# %%
variants = A.shares(A.compare(paths, models={"shapley": A.shapley, "shapley_rate": A.shapley_rate,
                                             "linear": A.linear}))
variants

# %% [markdown]
# - **Shapley, count game** (v(S) = conversions of journeys using only channels in S): every journey
#   is a unanimity game, so the Shapley value is *exactly* an equal split over the journey's distinct
#   channels. It equals linear except where a channel repeats in a path, which is why the two columns
#   match to two decimals.
# - **Shapley, rate game** (v(S) = conversion rate of those journeys): Display and Social go
#   negative. Their journeys convert below average, so adding them lowers a coalition's rate. That
#   answers "does this channel raise conversion rate?" rather than "how many conversions does it
#   drive?". It's the wrong question for budget, because prospecting channels bring many
#   low-intent users *by design*.
#
# So "Shapley" isn't one answer. The characteristic function is a modelling choice and should be
# stated alongside the result.

# %% [markdown]
# ## How certain are the shares?

# %%
boot = A.bootstrap_shares(paths, A.markov, n_boot=200)
boot.sort_values("share", ascending=False)

# %% [markdown]
# Bootstrap intervals are about ±0.5-1 percentage point, so the model-to-model gaps (Email: 4% to
# 16%) are far larger than the sampling noise. **The disagreement is about assumptions, not data.**

# %% [markdown]
# ## Revenue view

# %%
A.shares(A.compare(paths, metric="revenue_usd", models=core))

# %% [markdown]
# ## What to tell the marketing team
#
# 1. **Don't cut Display or Organic Social on last-click numbers.** They mostly open journeys
#    (first touch 84-88% of the time), so last-click credits them about 4% each against 6-9% under
#    every model that sees the whole journey.
# 2. **Email's last-click credit (16%) overstates it,** because it mostly closes journeys other
#    channels started. Markov puts it at 13%, linear at 10%.
# 3. **Use Markov as the default** for channel credit: it is the only model here that uses
#    non-converting journeys and stays stable under resampling. Report last and first click beside it
#    as bounds.
# 4. **None of these models are causal.** They split observed conversions; they don't say what would
#    happen without a channel. Validate budget moves with a geo or holdout experiment before shifting
#    spend.
