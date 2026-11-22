# %% [markdown]
# # 02 · Ecommerce funnel and significance tests
#
# `view_item → add_to_cart → begin_checkout → add_shipping_info → add_payment_info → purchase`,
# by device, channel and new vs returning users.
#
# **Method**
# - Unit: the session. *Closed* funnel = steps happened in order; *open* = the event happened at all.
# - Pairwise: two-proportion z-test, Newcombe interval, Holm correction within each dimension.
# - Omnibus: chi-square test of homogeneity across all segments of a dimension.
# - Effect size: Cohen's h and relative lift, because with 139k sessions small gaps are "significant".
# - Dependence: a user-clustered (Poisson) bootstrap, since one user can contribute several sessions.

# %%
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ga4_analytics import funnel as F
from ga4_analytics.db import Warehouse
from ga4_analytics.plots import HIGHLIGHT, NEUTRAL, PALETTE, save, use_style

use_style()
pd.set_option("display.width", 180)
pd.set_option("display.max_columns", 30)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
wh = Warehouse()
segments = F.funnel_segments(wh)
closed = F.funnel_long(segments, "closed")
opened = F.funnel_long(segments, "open")

# %% [markdown]
# ## Overall funnel

# %%
overall = closed[closed["dimension"] == "all"][["step", "reached", "step_rate", "overall_rate"]]
overall_open = opened[opened["dimension"] == "all"][["step", "reached"]].rename(columns={"reached": "reached_open"})
overall = overall.merge(overall_open, on="step")
overall["lost_to_ordering_or_tracking"] = overall["reached_open"] - overall["reached"]
overall

# %%
fig, ax = plt.subplots(figsize=(9, 4))
ax.barh(overall["step"][::-1], overall["reached"][::-1], color=PALETTE[0])
for y, (n, r) in enumerate(zip(overall["reached"][::-1], overall["step_rate"][::-1], strict=True)):
    ax.text(n, y, f"  {n:,}  ({r:.0%} of previous)", va="center", fontsize=9)
ax.set_title("Closed funnel, all sessions")
ax.set_xlabel("sessions")
ax.set_xlim(0, overall["reached"].max() * 1.35)
save(fig, "02_funnel_overall")

# %% [markdown]
# The open funnel counts more purchases than the closed one: some purchasing sessions are missing
# an earlier checkout event. That is a tracking gap (an injected defect), not customer behaviour, so
# the closed funnel is used for step rates and both are reported.

# %% [markdown]
# ## By device

# %%
def plot_step_rates(dim: str, title: str, name: str) -> None:
    d = closed[closed["dimension"] == dim]
    order = d.groupby("segment")["sessions"].max().sort_values(ascending=False).index
    fig, ax = plt.subplots(figsize=(10, 4))
    width = 0.8 / len(order)
    x = np.arange(len(F.STEPS))
    for i, seg in enumerate(order):
        s = d[d["segment"] == seg].set_index("step").loc[F.STEPS]
        ax.bar(x + i * width, s["step_rate"], width, label=seg, color=PALETTE[i % len(PALETTE)])
    ax.set_xticks(x + width * (len(order) - 1) / 2, [s.replace("_", "\n") for s in F.STEPS])
    ax.set_ylabel("step conversion (of previous step)")
    ax.set_title(title)
    ax.legend(ncols=min(len(order), 4))
    save(fig, name)


plot_step_rates("device", "Step conversion by device", "02_steps_by_device")

# %%
F.compare_segments(closed, "device")

# %%
dev = closed[(closed["dimension"] == "device") & (closed["step"] == "purchase")]
stat, p, dof = F.chi_square(dev["reached"].tolist(), dev["sessions"].tolist())
print(f"chi-square across devices: X2 = {stat:.1f}, dof = {dof}, p = {p:.2e}")

# %% [markdown]
# ### Where does mobile lose conversion?
#
# Test each step's rate (of the previous step) for mobile against desktop.

# %%
mobile_steps = F.step_tests(closed, "device", "mobile", "desktop")
mobile_steps

# %%
fig, ax = plt.subplots(figsize=(9, 4))
colors = [HIGHLIGHT if s else NEUTRAL for s in mobile_steps["significant"]]
ax.errorbar(mobile_steps["relative_lift"] * 100, np.arange(len(F.STEPS)), fmt="none", ecolor=NEUTRAL,
            xerr=[(mobile_steps["diff"] - mobile_steps["ci_low"]) / mobile_steps["desktop_rate"] * 100,
                  (mobile_steps["ci_high"] - mobile_steps["diff"]) / mobile_steps["desktop_rate"] * 100])
ax.scatter(mobile_steps["relative_lift"] * 100, np.arange(len(F.STEPS)), color=colors, zorder=3, s=50)
ax.axvline(0, color="black", lw=0.8)
ax.set_yticks(np.arange(len(F.STEPS)), F.STEPS)
ax.invert_yaxis()
ax.set_xlabel("mobile vs desktop, relative difference in step conversion (%), 95% CI")
ax.set_title("Mobile loses conversion only at payment and purchase")
save(fig, "02_mobile_step_gap")

# %% [markdown]
# The gap sits entirely in the last two steps: about −14% at `add_payment_info` and −29% at
# `purchase`, with nothing significant upstream. Mobile users browse and add to cart like desktop
# users, then fail at payment. **That points to the mobile payment form, not to traffic quality.**
#
# *Ground truth check:* the generator planted ×0.85 at payment and ×0.72 at purchase on mobile
# (−15% and −28%). The test recovers both and correctly finds nothing at the other four steps.

# %% [markdown]
# ## By channel

# %%
plot_step_rates("channel", "Step conversion by channel", "02_steps_by_channel")
F.compare_segments(closed, "channel")

# %%
ch = closed[(closed["dimension"] == "channel") & (closed["step"] == "purchase")]
stat, p, dof = F.chi_square(ch["reached"].tolist(), ch["sessions"].tolist())
print(f"chi-square across channels: X2 = {stat:.1f}, dof = {dof}, p = {p:.2e}")
F.compare_segments(closed, "channel", step="add_to_cart", rate="step")

# %% [markdown]
# Channel differences are concentrated at the top of the funnel (product views and add to cart):
# Email and Direct sessions engage far more than Display and Organic Social. Once a session reaches
# checkout, channels convert alike. Channel is a traffic-quality question; device is a checkout
# question.
#
# Session-level channel conversion is **not** channel value: Display and Social sessions convert
# poorly but start journeys that convert later. Notebook 03 handles that with attribution.

# %% [markdown]
# ## New vs returning

# %%
plot_step_rates("user_type", "Step conversion, new vs returning", "02_steps_by_user_type")
F.compare_segments(closed, "user_type", reference="new")

# %%
F.step_tests(closed, "user_type", "returning", "new")

# %% [markdown]
# ## Do repeat sessions break the z-test?
#
# The z-test assumes independent sessions. A user-clustered bootstrap resamples users instead.

# %%
s = wh.query("SELECT user_pseudo_id, user_type, device_category, reached_purchase::INT AS reached_purchase "
             "FROM sessions", dry_run=False)
boot = pd.DataFrame({
    "returning vs new": F.cluster_bootstrap_diff(s, "user_type", "returning", "new"),
    "mobile vs desktop": F.cluster_bootstrap_diff(s, "device_category", "mobile", "desktop"),
}).T
boot

# %% [markdown]
# The design effect is about 1: in this data a user's sessions convert independently, so clustering
# barely widens the intervals. On real data users differ in purchase intent, the design effect is
# usually above 1, and the naive z-test overstates significance; the bootstrap is the safer default.

# %% [markdown]
# ## What could we detect at all?

# %%
base = closed[(closed["dimension"] == "all") & (closed["step"] == "purchase")]["overall_rate"].iloc[0]
mde = pd.DataFrame([
    {"comparison": name, "n1": n1, "n2": n2,
     "mde_abs_pp": 100 * F.minimum_detectable_effect(base, n1, n2),
     "mde_relative": F.minimum_detectable_effect(base, n1, n2) / base}
    for name, n1, n2 in [
        ("mobile vs desktop", int(segments.query("segment == 'mobile'")["sessions"].iloc[0]),
         int(segments.query("segment == 'desktop'")["sessions"].iloc[0])),
        ("tablet vs desktop", int(segments.query("segment == 'tablet'")["sessions"].iloc[0]),
         int(segments.query("segment == 'desktop'")["sessions"].iloc[0])),
        ("Affiliates vs Organic Search", int(segments.query("segment == 'Affiliates'")["sessions"].iloc[0]),
         int(segments.query("segment == 'Organic Search'")["sessions"].iloc[0])),
    ]
])
mde

# %% [markdown]
# Tablet (about 4k sessions) can only detect a gap of roughly a quarter of the baseline, so "tablet is
# not significantly different" means "too small to tell", not "the same".
#
# ## Conclusions
#
# 1. **Mobile checkout is the biggest fixable leak.** Mobile converts about 38% worse than desktop,
#    and all of it is lost at payment and purchase (Holm-adjusted p < 1e-10 at both).
# 2. **Channel differences are traffic quality,** located at product views and add to cart, not
#    at checkout.
# 3. **Returning sessions convert better** at the first three steps (product views +12%, add to cart
#    +38%, checkout starts +10%). Once in checkout, new and returning complete at the same rate. Part
#    of the top-of-funnel gap is channel mix: return visits come more from Email and Direct.
# 4. **Tablet is underpowered;** no conclusion either way.
