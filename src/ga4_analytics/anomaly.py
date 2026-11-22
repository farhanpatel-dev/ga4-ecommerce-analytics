"""Daily KPI anomaly detection (STL decomposition + robust z-score) and drill-down.

For each KPI series:
1. STL (Seasonal-Trend decomposition using LOESS) with a weekly period, in robust mode, so the
   outliers we're hunting don't drag the trend and seasonal fits towards themselves.
2. Robust z-score of the remainder: z = 0.6745 * (r - median(r)) / MAD(r). MAD-based scaling
   ignores the outliers themselves; a plain standard deviation is inflated by them (masking).
3. Flag |z| > 3.5 (Iglewicz and Hoaglin's recommended cut-off for modified z-scores).

Counts and revenue are modelled on the log scale, where weekly and holiday effects are
multiplicative; rates are modelled as they are.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from ga4_analytics.config import SQL_DIR
from ga4_analytics.db import Warehouse
from ga4_analytics.funnel import STEPS, two_proportion_test

KPIS = {
    # name: (column in daily_kpis, model on log scale?)
    "users": ("users", True),
    "sessions": ("sessions", True),
    "revenue": ("revenue_usd", True),
    "transactions": ("transactions", True),
    "conversion_rate": ("conversion_rate", False),
    "cart_rate": ("cart_rate", False),
    "checkout_completion": ("checkout_completion", False),
    "aov": ("aov_usd", False),
}
THRESHOLD = 3.5
# STL smoothing windows (days, odd). The statsmodels defaults (seasonal=7, trend ~ 11) are flexible
# enough to absorb ordinary day-to-day noise; robust fitting then leaves only extreme points with
# large residuals, the residual MAD shrinks, and z-scores inflate. On this data the defaults raise
# 39 non-holiday alarms, while these stiffer windows raise 5, four of them the 2020-12-15 incident.
# The cost: a stiffer trend reacts more slowly to genuine level shifts.
SEASONAL_WINDOW = 31
TREND_WINDOW = 43

# Calendar of known events: anomalies on these days are expected and labelled, not investigated.
KNOWN_EVENTS = {
    "2020-11-01": "first day of the window (partial journeys)",
    "2020-11-27": "Black Friday",
    "2020-11-28": "Black Friday weekend",
    "2020-11-29": "Black Friday weekend",
    "2020-11-30": "Cyber Monday",
    "2020-12-23": "Christmas",
    "2020-12-24": "Christmas Eve",
    "2020-12-25": "Christmas Day",
    "2020-12-26": "Boxing Day",
    "2020-12-31": "New Year's Eve",
    "2021-01-01": "New Year's Day",
}


def load_daily(wh: Warehouse) -> pd.DataFrame:
    df = wh.query("""
        SELECT d.*, f.checkout_sessions
        FROM daily_kpis AS d
        LEFT JOIN (SELECT session_date AS date, count(*) FILTER (WHERE reached_begin_checkout) AS checkout_sessions
                   FROM sessions GROUP BY 1) AS f USING (date)
        WHERE strftime(d.date, '%Y%m%d') BETWEEN $start_suffix AND $end_suffix
        ORDER BY d.date
    """)
    df["cart_rate"] = df["cart_sessions"] / df["sessions"]
    df["checkout_completion"] = df["purchase_sessions"] / df["checkout_sessions"]
    return df


def robust_z(x: np.ndarray) -> np.ndarray:
    """Modified z-score: 0.6745 (x - median) / MAD. Zero MAD falls back to the mean absolute deviation."""
    x = np.asarray(x, dtype=float)
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    if mad == 0:
        mad = np.mean(np.abs(x - med)) / 0.7979 or 1.0  # MeanAD of a normal = 0.7979 sigma
    return 0.6745 * (x - med) / mad


def detect(series: pd.Series, log: bool = False, period: int = 7, threshold: float = THRESHOLD,
           seasonal: int | None = SEASONAL_WINDOW, trend: int | None = TREND_WINDOW) -> pd.DataFrame:
    """STL + robust z on one daily series indexed by date. seasonal=trend=None uses statsmodels defaults."""
    y = np.log(series.clip(lower=1e-9)) if log else series.astype(float)
    fit = STL(y, period=period, robust=True, seasonal=seasonal or 7, trend=trend).fit()
    z = robust_z(fit.resid.to_numpy())
    expected = fit.trend + fit.seasonal
    return pd.DataFrame({
        "value": series,
        "expected": np.exp(expected) if log else expected,
        "trend": np.exp(fit.trend) if log else fit.trend,
        "resid": fit.resid,
        "robust_z": z,
        "is_anomaly": np.abs(z) > threshold,
    }, index=series.index)


def detect_all(daily: pd.DataFrame, kpis: dict = KPIS, threshold: float = THRESHOLD,
               seasonal: int | None = SEASONAL_WINDOW, trend: int | None = TREND_WINDOW) -> pd.DataFrame:
    """Long table: one row per (date, KPI) with expected value, robust z, anomaly flag and known event."""
    frames = []
    s = daily.set_index("date")
    for name, (col, log) in kpis.items():
        out = detect(s[col], log=log, threshold=threshold, seasonal=seasonal, trend=trend)
        out["kpi"] = name
        frames.append(out.reset_index())
    res = pd.concat(frames, ignore_index=True)
    res["pct_vs_expected"] = res["value"] / res["expected"] - 1
    res["known_event"] = pd.to_datetime(res["date"]).dt.strftime("%Y-%m-%d").map(KNOWN_EVENTS)
    return res


def anomalies(results: pd.DataFrame, unexplained_only: bool = False) -> pd.DataFrame:
    a = results[results["is_anomaly"]].copy()
    if unexplained_only:
        a = a[a["known_event"].isna()]
    a["abs_z"] = a["robust_z"].abs()
    return a.sort_values("abs_z", ascending=False).drop(columns="abs_z").reset_index(drop=True)


def alarm_days(results: pd.DataFrame) -> pd.DataFrame:
    """One row per flagged day: how many KPIs fired, the strongest one, and any known event."""
    a = anomalies(results)
    if a.empty:
        return a
    a["abs_z"] = a["robust_z"].abs()
    g = a.groupby("date").agg(kpis_flagged=("kpi", "count"), kpis=("kpi", lambda k: ", ".join(k)),
                              max_abs_z=("abs_z", "max"), known_event=("known_event", "first"))
    return g.sort_values(["kpis_flagged", "max_abs_z"], ascending=False).reset_index()


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------
def drilldown_counts(wh: Warehouse, day: date | str, baseline_days: int = 14) -> pd.DataFrame:
    sql = (SQL_DIR / "10_anomaly_drilldown.sql").read_text(encoding="utf-8")
    return wh.query(sql, {"anomaly_date": pd.Timestamp(day).date(), "baseline_days": baseline_days})


def segment_shortfall(counts: pd.DataFrame, dimension: str, metric: str = "purchase") -> pd.DataFrame:
    """Expected vs actual on the anomaly day per segment, at the baseline rate per session.

    expected = anomaly-day sessions x baseline rate. The shortfall says which segment lost the
    conversions; comparing rates separates a *rate* problem from a *mix* problem (more traffic in
    a weaker segment).
    """
    d = counts[counts["dimension"] == dimension].pivot(index="segment", columns="period")
    out = pd.DataFrame({
        "sessions": d[("sessions", "anomaly")],
        "baseline_sessions_per_day": d[("sessions", "baseline")] / d[("days", "baseline")],
        "actual": d[(metric, "anomaly")],
        "baseline_rate": d[(metric, "baseline")] / d[("sessions", "baseline")],
    })
    out["anomaly_rate"] = out["actual"] / out["sessions"]
    out["expected"] = out["sessions"] * out["baseline_rate"]
    out["shortfall"] = out["actual"] - out["expected"]
    out["share_of_shortfall"] = out["shortfall"] / out["shortfall"].sum()
    out["p_value"] = [
        two_proportion_test(int(r["actual"]), int(r["sessions"]),
                            int(d.loc[seg, (metric, "baseline")]), int(d.loc[seg, ("sessions", "baseline")])).p_value
        for seg, r in out.iterrows()
    ]
    return out.sort_values("shortfall")


def step_comparison(counts: pd.DataFrame, dimension: str, segment: str) -> pd.DataFrame:
    """Step conversion (of the previous step) on the anomaly day vs baseline, for one segment."""
    row = counts[(counts["dimension"] == dimension) & (counts["segment"] == segment)].set_index("period")
    rows = []
    for period in ("anomaly", "baseline"):
        prev = row.loc[period, "sessions"]
        for step in STEPS:
            n = row.loc[period, step]
            rows.append({"period": period, "step": step, "reached": n, "from_previous": prev})
            prev = n
    df = pd.DataFrame(rows)
    wide = df.pivot(index="step", columns="period").loc[STEPS]
    out = pd.DataFrame({
        "anomaly_rate": wide[("reached", "anomaly")] / wide[("from_previous", "anomaly")],
        "baseline_rate": wide[("reached", "baseline")] / wide[("from_previous", "baseline")],
    })
    out["relative_change"] = out["anomaly_rate"] / out["baseline_rate"] - 1
    out["p_value"] = [
        two_proportion_test(int(wide.loc[s, ("reached", "anomaly")]), int(wide.loc[s, ("from_previous", "anomaly")]),
                            int(wide.loc[s, ("reached", "baseline")]),
                            int(wide.loc[s, ("from_previous", "baseline")])).p_value
        for s in STEPS
    ]
    return out
