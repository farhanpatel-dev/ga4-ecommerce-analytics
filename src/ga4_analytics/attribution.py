"""Multi-touch attribution: last-click, first-click, linear, Markov chain and Shapley value.

Input is a path table with one row per distinct channel path:
    path (list[str]), conversions (int), non_conversions (int), revenue_usd (float)

Every model returns credit per channel that sums to the total conversions (and revenue), so the
models can be compared side by side.

- Heuristics (last, first, linear) split each converting path's credit by a fixed rule.
- Markov: a first-order chain over START -> channels -> {CONVERSION, NULL}. A channel's removal
  effect is the relative drop in P(conversion from START) when every visit to it ends the journey.
  Credit is proportional to removal effects. It uses non-converting paths too.
- Shapley: a cooperative game where a coalition S of channels is worth the conversions of all paths
  whose channel set is a subset of S. Each channel gets its average marginal contribution over all
  join orders. It ignores order within a path and is computed exactly (2^n coalitions).
"""

from __future__ import annotations

from collections import defaultdict
from math import factorial

import numpy as np
import pandas as pd

from ga4_analytics.db import Warehouse

START, CONV, NULL = "(start)", "(conversion)", "(null)"


def load_paths(wh: Warehouse, params: dict | None = None) -> pd.DataFrame:
    """Build journeys with sql/08_attribution_paths.sql and return the distinct-path table."""
    wh.run_file("08_attribution_paths.sql", params)
    df = wh.query("SELECT path, journeys, conversions, non_conversions, revenue_usd FROM attribution_path_counts",
                  dry_run=False)
    df["path"] = df["path"].map(list)
    return df


def channels_in(paths: pd.DataFrame) -> list[str]:
    return sorted({c for p in paths["path"] for c in p})


# ---------------------------------------------------------------------------
# Heuristic models
# ---------------------------------------------------------------------------
def _heuristic(paths: pd.DataFrame, rule: str) -> pd.DataFrame:
    conv, rev = defaultdict(float), defaultdict(float)
    for path, c, r in zip(paths["path"], paths["conversions"], paths["revenue_usd"], strict=True):
        if c == 0:
            continue
        if rule == "last":
            weights = {path[-1]: 1.0}
        elif rule == "first":
            weights = {path[0]: 1.0}
        else:  # linear: equal credit per touch
            weights = defaultdict(float)
            for ch in path:
                weights[ch] += 1 / len(path)
        for ch, w in weights.items():
            conv[ch] += c * w
            rev[ch] += r * w
    return _frame(conv, rev, channels_in(paths))


def last_click(paths: pd.DataFrame) -> pd.DataFrame:
    return _heuristic(paths, "last")


def first_click(paths: pd.DataFrame) -> pd.DataFrame:
    return _heuristic(paths, "first")


def linear(paths: pd.DataFrame) -> pd.DataFrame:
    return _heuristic(paths, "linear")


# ---------------------------------------------------------------------------
# Markov chain (removal effect)
# ---------------------------------------------------------------------------
def transition_matrix(paths: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    """Row-stochastic first-order transition matrix over START, channels, CONV, NULL."""
    states = [START, *channels_in(paths), CONV, NULL]
    idx = {s: i for i, s in enumerate(states)}
    counts = np.zeros((len(states), len(states)))
    for path, c, nc in zip(paths["path"], paths["conversions"], paths["non_conversions"], strict=True):
        for outcome, n in ((CONV, c), (NULL, nc)):
            if n == 0:
                continue
            seq = [START, *path, outcome]
            for a, b in zip(seq[:-1], seq[1:], strict=True):
                counts[idx[a], idx[b]] += n
    counts[idx[CONV], idx[CONV]] = counts[idx[NULL], idx[NULL]] = 1  # absorbing
    rows = counts.sum(axis=1, keepdims=True)
    return states, np.divide(counts, rows, out=np.zeros_like(counts), where=rows > 0)


def conversion_probability(states: list[str], P: np.ndarray, removed: str | None = None) -> float:
    """P(absorbed in CONV | start at START), optionally with one channel removed.

    Removing a channel sends every transition into it to NULL (the journey ends there).
    Solved exactly from the absorbing-chain fundamental matrix: B = (I - Q)^-1 R.
    """
    P = P.copy()
    idx = {s: i for i, s in enumerate(states)}
    if removed is not None:
        r = idx[removed]
        P[:, idx[NULL]] += P[:, r]
        P[:, r] = 0.0
        P[r, :] = 0.0
        P[r, idx[NULL]] = 1.0
    transient = [i for i, s in enumerate(states) if s not in (CONV, NULL)]
    Q = P[np.ix_(transient, transient)]
    R = P[np.ix_(transient, [idx[CONV]])]
    B = np.linalg.solve(np.eye(len(transient)) - Q, R)
    return float(B[transient.index(idx[START]), 0])


def removal_effects(paths: pd.DataFrame) -> pd.Series:
    states, P = transition_matrix(paths)
    base = conversion_probability(states, P)
    effects = {ch: 1 - conversion_probability(states, P, removed=ch) / base for ch in channels_in(paths)}
    return pd.Series(effects, name="removal_effect")


def markov(paths: pd.DataFrame) -> pd.DataFrame:
    re = removal_effects(paths)
    share = re / re.sum()
    total_c, total_r = paths["conversions"].sum(), paths["revenue_usd"].sum()
    return _frame((share * total_c).to_dict(), (share * total_r).to_dict(), channels_in(paths))


# ---------------------------------------------------------------------------
# Shapley value
# ---------------------------------------------------------------------------
def shapley(paths: pd.DataFrame, value: str = "count") -> pd.DataFrame:
    """Exact Shapley values over channel sets.

    value="count": v(S) = conversions of paths whose channel set is a subset of S. Each path is then a
        unanimity game of its channels, so the Shapley value is exactly an equal split of the path's
        conversions among its *distinct* channels. It ignores non-converting paths entirely.
    value="rate":  v(S) = conversion rate of journeys whose channel set is a subset of S. It uses
        non-converting journeys, so a channel that shows up in many dead-end journeys earns less.
        Values are rescaled to total conversions; individual values can be negative.
    """
    chans = channels_in(paths)
    n = len(chans)
    bit = {c: 1 << i for i, c in enumerate(chans)}
    conv_by_set, rev_by_set, jour_by_set = np.zeros(1 << n), np.zeros(1 << n), np.zeros(1 << n)
    for path, c, nc, r in zip(paths["path"], paths["conversions"], paths["non_conversions"],
                              paths["revenue_usd"], strict=True):
        mask = 0
        for ch in path:
            mask |= bit[ch]
        conv_by_set[mask] += c
        rev_by_set[mask] += r
        jour_by_set[mask] += c + nc
    v_conv, v_rev = _subset_sums(conv_by_set, n), _subset_sums(rev_by_set, n)
    if value == "rate":
        journeys = _subset_sums(jour_by_set, n)
        v_conv = np.divide(v_conv, journeys, out=np.zeros_like(v_conv), where=journeys > 0)
        v_rev = np.divide(v_rev, journeys, out=np.zeros_like(v_rev), where=journeys > 0)
    elif value != "count":
        raise ValueError("value must be 'count' or 'rate'")
    weights = [factorial(k) * factorial(n - k - 1) / factorial(n) for k in range(n)]
    conv, rev = {}, {}
    for i, ch in enumerate(chans):
        b = 1 << i
        others = np.array([m for m in range(1 << n) if not m & b])
        w = np.array([weights[bin(m).count("1")] for m in others])
        conv[ch] = float(np.sum(w * (v_conv[others | b] - v_conv[others])))
        rev[ch] = float(np.sum(w * (v_rev[others | b] - v_rev[others])))
    if value == "rate":  # rescale so credit sums to the observed totals, like the other models
        conv = _rescale(conv, paths["conversions"].sum())
        rev = _rescale(rev, paths["revenue_usd"].sum())
    return _frame(conv, rev, chans)


def shapley_rate(paths: pd.DataFrame) -> pd.DataFrame:
    return shapley(paths, value="rate")


def _rescale(values: dict, total: float) -> dict:
    s = sum(values.values())
    return {k: v / s * total for k, v in values.items()} if s else values


def _subset_sums(values: np.ndarray, n: int) -> np.ndarray:
    """f(S) = sum of values[T] over all T subset of S (the zeta transform), in O(n 2^n)."""
    f = values.copy()
    for i in range(n):
        b = 1 << i
        for m in range(1 << n):
            if m & b:
                f[m] += f[m ^ b]
    return f


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
MODELS = {
    "last_click": last_click,
    "first_click": first_click,
    "linear": linear,
    "markov": markov,
    "shapley": shapley,
    "shapley_rate": shapley_rate,
}


def compare(paths: pd.DataFrame, metric: str = "conversions", models: dict | None = None) -> pd.DataFrame:
    """Channel x model table of credited conversions (or revenue)."""
    models = models or MODELS
    out = pd.DataFrame({name: fn(paths)[metric] for name, fn in models.items()})
    return out.sort_values(next(iter(models)) if "markov" not in out else "markov", ascending=False)


def shares(table: pd.DataFrame) -> pd.DataFrame:
    return table / table.sum()


def bootstrap_shares(paths: pd.DataFrame, model=markov, n_boot: int = 200, seed: int = 0) -> pd.DataFrame:
    """95% intervals for a model's credit shares, resampling journeys.

    Each distinct path's converting and non-converting counts are redrawn as Poisson(count), which is
    the Poisson bootstrap of the underlying journeys.
    """
    rng = np.random.default_rng(seed)
    point = shares(model(paths)[["conversions"]])["conversions"]
    draws = []
    for _ in range(n_boot):
        b = paths.copy()
        b["conversions"] = rng.poisson(paths["conversions"])
        b["non_conversions"] = rng.poisson(paths["non_conversions"])
        scale = np.divide(b["conversions"], paths["conversions"], out=np.zeros(len(b)),
                          where=paths["conversions"] > 0)
        b["revenue_usd"] = paths["revenue_usd"] * scale
        b = b[(b["conversions"] + b["non_conversions"]) > 0]
        draws.append(shares(model(b)[["conversions"]])["conversions"])
    d = pd.DataFrame(draws)
    return pd.DataFrame({"share": point, "ci_low": d.quantile(0.025), "ci_high": d.quantile(0.975)})


def position_profile(paths: pd.DataFrame) -> pd.DataFrame:
    """Where each channel sits in converting multi-touch journeys: first, middle or last touch."""
    rows = defaultdict(lambda: {"first": 0.0, "middle": 0.0, "last": 0.0, "only": 0.0})
    for path, c in zip(paths["path"], paths["conversions"], strict=True):
        if c == 0:
            continue
        if len(path) == 1:
            rows[path[0]]["only"] += c
            continue
        rows[path[0]]["first"] += c
        rows[path[-1]]["last"] += c
        for ch in path[1:-1]:
            rows[ch]["middle"] += c
    df = pd.DataFrame(rows).T.fillna(0)
    df["opener_ratio"] = df["first"] / (df["first"] + df["last"]).replace(0, np.nan)
    return df.sort_values("opener_ratio", ascending=False)


def _frame(conv: dict, rev: dict, chans: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "conversions": [conv.get(c, 0.0) for c in chans],
        "revenue_usd": [rev.get(c, 0.0) for c in chans],
    }, index=pd.Index(chans, name="channel"))


__all__ = ["MODELS", "compare", "load_paths", "markov", "shapley", "removal_effects"]
