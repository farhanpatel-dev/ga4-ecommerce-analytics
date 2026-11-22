"""Streamlit dashboard over the modelled GA4 tables.

Run:
    python -m ga4_analytics.generate && python -m ga4_analytics.build   # once
    streamlit run app/dashboard.py

Environment overrides (used by the tests): GA4_DB (DuckDB file), GA4_EVENTS_DIR (Parquet export).
"""

from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ga4_analytics import anomaly as N
from ga4_analytics import attribution as A
from ga4_analytics import funnel as F
from ga4_analytics import metric_tree as M
from ga4_analytics import quality as Q
from ga4_analytics.config import EVENTS_DIR
from ga4_analytics.db import DEFAULT_DB, Warehouse

st.set_page_config(page_title="GA4 Ecommerce Analytics", page_icon="📈", layout="wide")

DB_PATH = Path(os.environ.get("GA4_DB", DEFAULT_DB))
EVENTS = Path(os.environ.get("GA4_EVENTS_DIR", EVENTS_DIR))
COLORS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
STEP_LABELS = {s: s.replace("_", " ") for s in F.STEPS}


# ---------------------------------------------------------------------------
# Data access: one DuckDB connection, serialised with a lock; results cached per filter.
# ---------------------------------------------------------------------------
@st.cache_resource
def warehouse() -> tuple[Warehouse, threading.Lock]:
    if not DB_PATH.exists():
        st.error(f"No database at {DB_PATH}. Run `python -m ga4_analytics.generate` then "
                 "`python -m ga4_analytics.build` first.")
        st.stop()
    return Warehouse(DB_PATH, events_dir=EVENTS, verbose=False), threading.Lock()


def run(fn, *args, **kwargs):
    wh, lock = warehouse()
    with lock:
        return fn(wh, *args, **kwargs)


def params(start: date, end: date) -> dict:
    return {"start_suffix": start.strftime("%Y%m%d"), "end_suffix": end.strftime("%Y%m%d")}


@st.cache_data
def window() -> tuple[date, date]:
    d = run(lambda wh: wh.query("SELECT min(date) AS a, max(date) AS b FROM daily_kpis", dry_run=False))
    return d["a"].iloc[0].date(), d["b"].iloc[0].date()


@st.cache_data
def daily_kpis(start: date, end: date) -> pd.DataFrame:
    return run(lambda wh: wh.query(
        "SELECT * FROM daily_kpis WHERE date BETWEEN $a AND $b ORDER BY date", {"a": start, "b": end},
        dry_run=False))


@st.cache_data
def funnel(start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    seg = run(F.funnel_segments, params(start, end))
    return seg, F.funnel_long(seg, "closed")


@st.cache_data
def attribution(start: date, end: date) -> pd.DataFrame:
    return run(A.load_paths, params(start, end))


@st.cache_data
def tree_daily() -> pd.DataFrame:
    return run(M.load_daily)


@st.cache_data
def anomaly_results() -> pd.DataFrame:
    return N.detect_all(run(N.load_daily))


@st.cache_data
def drilldown(day: date) -> pd.DataFrame:
    return run(N.drilldown_counts, day, 14)


@st.cache_data
def quality() -> pd.DataFrame:
    return run(Q.run_checks)


@st.cache_data
def dry_run_note(start: date, end: date) -> str:
    sql = ("SELECT event_name, count(*) FROM events "
           "WHERE _TABLE_SUFFIX BETWEEN $start_suffix AND $end_suffix GROUP BY 1")
    return run(lambda wh: wh.dry_run(sql, params(start, end)).summary())


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
lo, hi = window()
with st.sidebar:
    st.title("GA4 Ecommerce")
    st.caption("Synthetic data with the GA4 export schema, run locally on DuckDB.")
    picked = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)
    start, end = (picked if isinstance(picked, tuple) and len(picked) == 2 else (lo, hi))
    st.caption("Applies to Overview, Funnel and Attribution. The metric tree and anomaly detection "
               "always use the full window, because they need the history.")
    with st.expander("Dry run for the selected range"):
        st.code(dry_run_note(start, end), language=None)

tabs = st.tabs(["Overview", "Funnel", "Attribution", "Metric tree", "Anomalies", "Data quality"])

# -- Overview ------------------------------------------------------------------
with tabs[0]:
    d = daily_kpis(start, end)
    tx, rev = d["transactions"].sum(), d["revenue_usd"].sum()
    c = st.columns(5)
    c[0].metric("Revenue", f"${rev:,.0f}")
    c[1].metric("Transactions", f"{tx:,.0f}")
    c[2].metric("AOV", f"${rev / tx:,.2f}" if tx else "–")
    c[3].metric("Sessions", f"{d['sessions'].sum():,.0f}")
    c[4].metric("Session conversion", f"{d['purchase_sessions'].sum() / d['sessions'].sum():.2%}")
    metric = st.radio("Series", ["revenue_usd", "users", "sessions", "transactions", "conversion_rate", "aov_usd"],
                      horizontal=True, format_func=lambda m: m.replace("_usd", "").replace("_", " "))
    fig = px.line(d, x="date", y=metric, color_discrete_sequence=COLORS)
    fig.update_layout(height=360, margin=dict(t=20, b=20), yaxis_title=None, xaxis_title=None)
    st.plotly_chart(fig, width="stretch")

# -- Funnel ------------------------------------------------------------------
with tabs[1]:
    seg, long = funnel(start, end)
    dim = st.radio("Split by", ["device", "channel", "user_type"], horizontal=True,
                   format_func=lambda s: {"user_type": "new vs returning"}.get(s, s))
    view = long[long["dimension"] == dim]
    left, right = st.columns([3, 2])
    with left:
        fig = px.bar(view, x="step", y="step_rate", color="segment", barmode="group",
                     color_discrete_sequence=COLORS, labels={"step_rate": "step conversion", "step": ""})
        fig.update_layout(height=380, margin=dict(t=20), yaxis_tickformat=".0%")
        fig.update_xaxes(categoryorder="array", categoryarray=F.STEPS)
        st.plotly_chart(fig, width="stretch")
    with right:
        overall = long[(long["dimension"] == "all")][["step", "reached", "step_rate"]]
        fig = go.Figure(go.Funnel(y=[STEP_LABELS[s] for s in overall["step"]], x=overall["reached"],
                                  textinfo="value+percent previous", marker_color=COLORS[0]))
        fig.update_layout(height=380, margin=dict(t=20, l=10))
        st.plotly_chart(fig, width="stretch")

    segs = view.groupby("segment")["sessions"].max().sort_values(ascending=False).index.tolist()
    ref = st.selectbox("Reference segment", segs, index=0)
    st.subheader("Purchase conversion vs reference (Holm-corrected)")
    comp = F.compare_segments(long, dim, reference=ref)
    st.dataframe(comp[["segment", "rate", "reference_rate", "relative_lift", "ci_low", "ci_high", "p_holm",
                       "significant", "n"]].style.format({
        "rate": "{:.2%}", "reference_rate": "{:.2%}", "relative_lift": "{:+.1%}", "ci_low": "{:+.2%}",
        "ci_high": "{:+.2%}", "p_holm": "{:.2g}"}), width="stretch", hide_index=True)
    target = st.selectbox("Where does a segment lose conversion? (step-by-step vs reference)",
                          [s for s in segs if s != ref])
    steps = F.step_tests(long, dim, target, ref)
    fig = go.Figure(go.Bar(
        x=steps["relative_lift"], y=[STEP_LABELS[s] for s in steps["step"]], orientation="h",
        marker_color=[COLORS[5] if s else "#9a9a9a" for s in steps["significant"]],
        error_x=dict(type="data", symmetric=False,
                     array=(steps["ci_high"] - steps["diff"]) / steps[f"{ref}_rate"],
                     arrayminus=(steps["diff"] - steps["ci_low"]) / steps[f"{ref}_rate"])))
    fig.update_layout(height=320, margin=dict(t=20), xaxis_tickformat="+.0%", yaxis_autorange="reversed",
                      xaxis_title=f"{target} vs {ref}, relative difference in step conversion (orange = significant)")
    st.plotly_chart(fig, width="stretch")

# -- Attribution ------------------------------------------------------------------
with tabs[2]:
    paths = attribution(start, end)
    metric = st.radio("Credit", ["conversions", "revenue_usd"], horizontal=True,
                      format_func=lambda m: "revenue" if m == "revenue_usd" else m)
    models = {k: v for k, v in A.MODELS.items() if k != "shapley_rate"}
    share = A.shares(A.compare(paths, metric=metric, models=models))
    melted = share.reset_index().melt(id_vars="channel", var_name="model", value_name="share")
    fig = px.bar(melted, x="share", y="channel", color="model", barmode="group", orientation="h",
                 color_discrete_sequence=COLORS)
    fig.update_layout(height=520, margin=dict(t=20), xaxis_tickformat=".0%", yaxis_title=None)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(share.style.format("{:.1%}"), width="stretch")
    st.caption(f"{int(paths['journeys'].sum()):,} journeys, {int(paths['conversions'].sum()):,} converting. "
               "Markov is the recommended default: it uses non-converting journeys. None of these models is "
               "causal; validate budget moves with a holdout.")
    with st.expander("Where channels sit in converting journeys"):
        st.dataframe(A.position_profile(paths).style.format({"opener_ratio": "{:.0%}"}, precision=0),
                     width="stretch")

# -- Metric tree ------------------------------------------------------------------
with tabs[3]:
    td = tree_daily()
    dates = td["date"].dt.date.tolist()
    c1, c2 = st.columns(2)
    after = c1.selectbox("Day", dates[1:], index=dates.index(date(2020, 12, 15)) - 1
                         if date(2020, 12, 15) in dates else len(dates) - 2)
    lag = c2.radio("Compare with", [1, 7], horizontal=True,
                   format_func=lambda n: "previous day" if n == 1 else "same weekday last week")
    i = dates.index(after)
    if i - lag < 0:
        st.info("Not enough history for that comparison.")
    else:
        before_row, after_row = td.iloc[i - lag], td.iloc[i]
        tree = M.tree_frame(before_row, after_row)
        leaves = tree[tree["node"].str.startswith("        ")]
        fig = go.Figure(go.Waterfall(
            x=["before", *leaves["node"].str.strip(), "after"],
            measure=["absolute", *["relative"] * len(leaves), "total"],
            y=[before_row["revenue_usd"], *leaves["contribution_usd"], 0],
            decreasing=dict(marker_color=COLORS[5]), increasing=dict(marker_color=COLORS[2]),
            totals=dict(marker_color="#9a9a9a")))
        fig.update_layout(height=380, margin=dict(t=30), yaxis_title="revenue (USD)",
                          title=f"{before_row['date']:%Y-%m-%d} → {after_row['date']:%Y-%m-%d}")
        st.plotly_chart(fig, width="stretch")
        st.dataframe(tree.style.format({"before": "{:,.3f}", "after": "{:,.3f}", "pct_change": "{:+.1%}",
                                        "contribution_usd": "{:+,.0f}"}), width="stretch", hide_index=True)
        st.caption("LMDI decomposition: exact (no residual) and independent of factor order.")

# -- Anomalies ------------------------------------------------------------------
with tabs[4]:
    res = anomaly_results()
    kpi = st.selectbox("KPI", list(N.KPIS), index=list(N.KPIS).index("conversion_rate"))
    r = res[res["kpi"] == kpi]
    fig = go.Figure()
    fig.add_scatter(x=r["date"], y=r["value"], name="actual", line=dict(color=COLORS[0]))
    fig.add_scatter(x=r["date"], y=r["expected"], name="expected", line=dict(color="#9a9a9a", dash="dash"))
    flag = r[r["is_anomaly"]]
    fig.add_scatter(x=flag["date"], y=flag["value"], mode="markers", name="anomaly",
                    marker=dict(size=11, color=[COLORS[1] if e else COLORS[5] for e in flag["known_event"].notna()]),
                    text=flag["known_event"].fillna("unexplained"), hovertemplate="%{x|%Y-%m-%d}: %{text}")
    fig.update_layout(height=380, margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    days = N.alarm_days(res)
    st.subheader("Alarm days")
    st.dataframe(days.style.format({"max_abs_z": "{:.1f}"}), width="stretch", hide_index=True)
    unexplained = days[days["known_event"].isna()]["date"].dt.date.tolist()
    if unexplained:
        day = st.selectbox("Investigate", unexplained)
        counts = drilldown(day)
        dim = st.radio("Segment", ["device", "channel", "operating_system", "user_type"], horizontal=True)
        st.dataframe(N.segment_shortfall(counts, dim).style.format({
            "baseline_sessions_per_day": "{:.0f}", "baseline_rate": "{:.2%}", "anomaly_rate": "{:.2%}",
            "expected": "{:.1f}", "shortfall": "{:+.1f}", "share_of_shortfall": "{:.0%}", "p_value": "{:.2g}"}),
            width="stretch")
        seg_choice = st.selectbox("Funnel steps for segment", counts[counts["dimension"] == dim]["segment"].unique())
        st.dataframe(N.step_comparison(counts, dim, seg_choice).style.format({
            "anomaly_rate": "{:.1%}", "baseline_rate": "{:.1%}", "relative_change": "{:+.0%}", "p_value": "{:.2g}"}),
            width="stretch")

# -- Data quality ------------------------------------------------------------------
with tabs[5]:
    q = quality()
    st.dataframe(q[["check_name", "unit", "numerator", "denominator", "rate", "injected_rate", "handling", "limits"]]
                 .style.format({"rate": "{:.2%}", "injected_rate": "{:.2%}"}, na_rep="–"),
                 width="stretch", hide_index=True)
    st.caption("Defects mimic the obfuscated GA4 sample's placeholders and inconsistencies. See docs/DATA_QUALITY.md.")
