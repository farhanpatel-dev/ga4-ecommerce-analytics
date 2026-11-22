"""Funnel tables and significance tests on conversion-rate differences.

Tests used, and why:
- Two-proportion z-test with a Newcombe (Wilson-score) interval for a pairwise difference.
- Chi-square test of homogeneity across all segments of a dimension (is there *any* difference?).
- Holm correction across the pairwise tests of a dimension (family-wise error control without
  Bonferroni's loss of power).
- Cohen's h as a scale-free effect size, because with 100k+ sessions tiny gaps are "significant".
- A user-clustered bootstrap, because sessions from the same user are not independent; the
  z-test treats them as if they were and understates the standard error.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from ga4_analytics.db import Warehouse

STEPS = ["view_item", "add_to_cart", "begin_checkout", "add_shipping_info", "add_payment_info", "purchase"]
DIMENSIONS = {"device": "device_category", "channel": "channel", "user_type": "user_type"}


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def funnel_segments(wh: Warehouse, params: dict | None = None) -> pd.DataFrame:
    """Run sql/07_funnel.sql and return session counts per step for every segment."""
    wh.run_file("07_funnel.sql", params)
    return wh.query("SELECT * FROM funnel_segments ORDER BY dimension, sessions DESC", dry_run=False)


def funnel_long(segments: pd.DataFrame, kind: str = "closed") -> pd.DataFrame:
    """One row per (dimension, segment, step) with reach, step conversion and overall conversion."""
    rows = []
    for _, r in segments.iterrows():
        prev = r["sessions"]
        for step in STEPS:
            n = int(r[f"{kind}_{step}"])
            rows.append({
                "dimension": r["dimension"], "segment": r["segment"], "step": step,
                "sessions": int(r["sessions"]), "reached": n, "from_previous": int(prev),
                "step_rate": n / prev if prev else np.nan,
                "overall_rate": n / r["sessions"] if r["sessions"] else np.nan,
            })
            prev = n
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a proportion; well behaved near 0 and for small n."""
    if n == 0:
        return (np.nan, np.nan)
    z = stats.norm.ppf(1 - alpha / 2)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (centre - half, centre + half)


@dataclass
class PropTest:
    p1: float
    p2: float
    diff: float
    ci_low: float
    ci_high: float
    relative_lift: float
    z: float
    p_value: float
    cohens_h: float


def two_proportion_test(k1: int, n1: int, k2: int, n2: int, alpha: float = 0.05) -> PropTest:
    """Pooled two-proportion z-test of p1 - p2, with a Newcombe hybrid-score interval.

    An empty group (n = 0, e.g. a small segment that never reached a step) returns NaNs instead of
    raising, so tables over many segments still render.
    """
    if n1 == 0 or n2 == 0:
        nan = float("nan")
        return PropTest(k1 / n1 if n1 else nan, k2 / n2 if n2 else nan, nan, nan, nan, nan, nan, nan, nan)
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    p_value = 2 * stats.norm.sf(abs(z))
    l1, u1 = wilson_ci(k1, n1, alpha)
    l2, u2 = wilson_ci(k2, n2, alpha)
    diff = p1 - p2
    ci_low = diff - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    ci_high = diff + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    h = 2 * np.arcsin(np.sqrt(p1)) - 2 * np.arcsin(np.sqrt(p2))
    return PropTest(p1, p2, diff, ci_low, ci_high, diff / p2 if p2 else np.nan, z, p_value, h)


def chi_square(successes: list[int], totals: list[int]) -> tuple[float, float, int]:
    """Chi-square test of homogeneity across k groups. Returns (statistic, p-value, dof)."""
    table = np.array([successes, np.subtract(totals, successes)])
    stat, p, dof, _ = stats.chi2_contingency(table, correction=False)
    return float(stat), float(p), int(dof)


def compare_segments(long: pd.DataFrame, dimension: str, step: str = "purchase",
                     rate: str = "overall", reference: str | None = None, alpha: float = 0.05) -> pd.DataFrame:
    """Every segment of `dimension` against a reference segment, Holm-corrected.

    rate="overall": share of sessions reaching `step`.
    rate="step":    share of sessions at the previous step that reached `step` (where the drop is).
    """
    df = long[(long["dimension"] == dimension) & (long["step"] == step)].copy()
    denom = "sessions" if rate == "overall" else "from_previous"
    if reference is None:
        reference = df.sort_values("sessions", ascending=False)["segment"].iloc[0]
    ref = df[df["segment"] == reference].iloc[0]
    rows = []
    for _, r in df[df["segment"] != reference].iterrows():
        t = two_proportion_test(int(r["reached"]), int(r[denom]), int(ref["reached"]), int(ref[denom]), alpha)
        rows.append({"segment": r["segment"], "reference": reference, "rate": t.p1, "reference_rate": t.p2,
                     "diff": t.diff, "ci_low": t.ci_low, "ci_high": t.ci_high, "relative_lift": t.relative_lift,
                     "z": t.z, "p_value": t.p_value, "cohens_h": t.cohens_h, "n": int(r[denom])})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    reject, p_adj, _, _ = multipletests(out["p_value"], alpha=alpha, method="holm")
    out["p_holm"] = p_adj
    out["significant"] = reject
    return out.sort_values("diff").reset_index(drop=True)


def step_tests(long: pd.DataFrame, dimension: str, segment: str, reference: str) -> pd.DataFrame:
    """Step-by-step test of one segment against a reference: locates *where* conversion is lost."""
    rows = []
    for step in STEPS:
        a = long[(long["dimension"] == dimension) & (long["segment"] == segment) & (long["step"] == step)].iloc[0]
        b = long[(long["dimension"] == dimension) & (long["segment"] == reference) & (long["step"] == step)].iloc[0]
        t = two_proportion_test(int(a["reached"]), int(a["from_previous"]), int(b["reached"]), int(b["from_previous"]))
        rows.append({"step": step, f"{segment}_rate": t.p1, f"{reference}_rate": t.p2, "diff": t.diff,
                     "ci_low": t.ci_low, "ci_high": t.ci_high, "relative_lift": t.relative_lift,
                     "p_value": t.p_value})
    out = pd.DataFrame(rows)
    out["p_holm"] = multipletests(out["p_value"], method="holm")[1]
    out["significant"] = out["p_holm"] < 0.05
    return out


def cluster_bootstrap_diff(sessions: pd.DataFrame, column: str, a: str, b: str, outcome: str = "reached_purchase",
                           n_boot: int = 2000, seed: int = 0) -> dict:
    """Difference in session conversion (a - b), resampling *users* rather than sessions.

    Uses the Poisson bootstrap (each user gets a Poisson(1) weight per replicate), which handles users
    who appear in both groups (e.g. new and returning) without any special casing.
    """
    df = sessions[sessions[column].isin([a, b])]
    g = df.groupby(["user_pseudo_id", column])[outcome].agg(["sum", "count"]).unstack(fill_value=0)
    conv = g["sum"].reindex(columns=[a, b], fill_value=0).to_numpy(float)
    n = g["count"].reindex(columns=[a, b], fill_value=0).to_numpy(float)
    rng = np.random.default_rng(seed)
    w = rng.poisson(1.0, size=(n_boot, len(g)))
    rate_a = (w @ conv[:, 0]) / (w @ n[:, 0])
    rate_b = (w @ conv[:, 1]) / (w @ n[:, 1])
    diffs = rate_a - rate_b
    point = conv[:, 0].sum() / n[:, 0].sum() - conv[:, 1].sum() / n[:, 1].sum()
    naive = two_proportion_test(int(conv[:, 0].sum()), int(n[:, 0].sum()), int(conv[:, 1].sum()), int(n[:, 1].sum()))
    naive_se = (naive.ci_high - naive.ci_low) / (2 * 1.96)
    return {
        "diff": point,
        "ci_low": float(np.percentile(diffs, 2.5)),
        "ci_high": float(np.percentile(diffs, 97.5)),
        "boot_se": float(diffs.std(ddof=1)),
        "naive_se": float(naive_se),
        "design_effect": float((diffs.std(ddof=1) / naive_se) ** 2) if naive_se else np.nan,
        "users": len(g),
    }


def minimum_detectable_effect(p: float, n1: int, n2: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest absolute difference from baseline rate p detectable with these group sizes."""
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    return float(z * np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)))
