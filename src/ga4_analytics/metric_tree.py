"""Revenue metric tree and exact decomposition of period-over-period changes.

    revenue = users x conversion x AOV
    users      = new_users + returning_users                       (additive)
    conversion = sessions_per_user x transactions_per_session      (multiplicative)
    AOV        = items_per_transaction x revenue_per_item          (multiplicative)

Changes are split with LMDI (logarithmic mean Divisia index). For R = x1 * x2 * ... * xk,

    dR = sum_i L(R1, R0) * ln(x_i1 / x_i0),   L(a, b) = (a - b) / (ln a - ln b),  L(a, a) = a

which is exact (no residual), independent of factor order, and consistent across levels: a parent's
contribution equals the sum of its children's. Naive "change one factor at a time" methods leave an
interaction residual and depend on the order you change the factors in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ga4_analytics.db import Warehouse

LEAVES = ["users", "sessions_per_user", "transactions_per_session", "items_per_transaction", "revenue_per_item"]


def load_daily(wh: Warehouse, params: dict | None = None) -> pd.DataFrame:
    """Run sql/09_metric_tree.sql and return the daily tree with every node computed."""
    wh.run_file("09_metric_tree.sql", params)
    df = wh.query("SELECT * FROM metric_tree_daily ORDER BY date", dry_run=False)
    return add_nodes(df)


def add_nodes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sessions_per_user"] = df["sessions"] / df["users"]
    df["transactions_per_session"] = df["transactions"] / df["sessions"]
    df["items_per_transaction"] = df["items_sold"] / df["transactions"]
    df["revenue_per_item"] = df["revenue_usd"] / df["items_sold"]
    df["conversion"] = df["transactions"] / df["users"]
    df["aov"] = df["revenue_usd"] / df["transactions"]
    return df


def log_mean(a: float, b: float) -> float:
    """Logarithmic mean L(a, b); L(a, a) = a."""
    if a <= 0 or b <= 0:
        raise ValueError("LMDI needs positive values (a day with zero transactions has no AOV)")
    return a if np.isclose(a, b) else (a - b) / (np.log(a) - np.log(b))


def decompose(before: pd.Series, after: pd.Series) -> dict[str, float]:
    """Exact LMDI split of revenue change from `before` to `after`, at every level of the tree."""
    r0, r1 = before["revenue_usd"], after["revenue_usd"]
    w = log_mean(r1, r0)
    leaf = {k: w * np.log(after[k] / before[k]) for k in LEAVES}
    out = {
        "revenue_before": r0,
        "revenue_after": r1,
        "revenue_change": r1 - r0,
        "users": leaf["users"],
        "conversion": leaf["sessions_per_user"] + leaf["transactions_per_session"],
        "aov": leaf["items_per_transaction"] + leaf["revenue_per_item"],
        **{f"leaf_{k}": v for k, v in leaf.items()},
    }
    # Users is a sum, not a product: share its contribution by each component's change.
    d_users = after["users"] - before["users"]
    for part in ("new_users", "returning_users"):
        share = (after[part] - before[part]) / d_users if d_users else 0.5
        out[f"users_{part}"] = leaf["users"] * share
    out["check_residual"] = out["revenue_change"] - (out["users"] + out["conversion"] + out["aov"])
    return out


def daily_decomposition(daily: pd.DataFrame, lag: int = 1) -> pd.DataFrame:
    """Decompose each day against the day `lag` days earlier (lag=7 compares like weekdays)."""
    rows = []
    for i in range(lag, len(daily)):
        before, after = daily.iloc[i - lag], daily.iloc[i]
        if min(before["transactions"], after["transactions"]) == 0:
            continue
        rows.append({"date": after["date"], "compared_to": before["date"], **decompose(before, after)})
    return pd.DataFrame(rows)


def period_totals(daily: pd.DataFrame, start, end) -> pd.Series:
    """Sum a date range into one row (users summed as user-days) and recompute the nodes."""
    mask = (daily["date"] >= pd.Timestamp(start)) & (daily["date"] <= pd.Timestamp(end))
    cols = ["users", "new_users", "returning_users", "sessions", "transactions", "items_sold", "revenue_usd"]
    total = daily.loc[mask, cols].sum()
    return add_nodes(total.to_frame().T).iloc[0]


def tree_frame(before: pd.Series, after: pd.Series) -> pd.DataFrame:
    """One row per tree node: value before, after, % change and contribution to revenue change."""
    d = decompose(before, after)
    nodes = [
        ("revenue", "revenue_usd", d["revenue_change"], 0),
        ("users", "users", d["users"], 1),
        ("new users", "new_users", d["users_new_users"], 2),
        ("returning users", "returning_users", d["users_returning_users"], 2),
        ("conversion (transactions / user)", "conversion", d["conversion"], 1),
        ("sessions / user", "sessions_per_user", d["leaf_sessions_per_user"], 2),
        ("transactions / session", "transactions_per_session", d["leaf_transactions_per_session"], 2),
        ("AOV", "aov", d["aov"], 1),
        ("items / transaction", "items_per_transaction", d["leaf_items_per_transaction"], 2),
        ("revenue / item", "revenue_per_item", d["leaf_revenue_per_item"], 2),
    ]
    rows = []
    for label, col, contrib, depth in nodes:
        rows.append({"node": "    " * depth + label, "before": before[col], "after": after[col],
                     "pct_change": after[col] / before[col] - 1, "contribution_usd": contrib})
    return pd.DataFrame(rows)
