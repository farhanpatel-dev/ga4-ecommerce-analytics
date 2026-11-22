import numpy as np
import pandas as pd
import pytest

from ga4_analytics import anomaly as N


def weekly_series(spike_at=None, spike=0.0, n=91, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-11-01", periods=n, freq="D")
    weekly = np.tile([1.0, 1.05, 1.08, 1.06, 1.02, 0.8, 0.82], n // 7 + 1)[:n]
    y = 1000 * weekly * (1 + 0.002 * np.arange(n)) * rng.normal(1, 0.03, n)
    if spike_at is not None:
        y[spike_at] *= 1 + spike
    return pd.Series(y, index=idx)


def test_robust_z_ignores_the_outlier_it_measures():
    x = np.r_[np.random.default_rng(1).normal(0, 1, 200), 50.0]
    z = N.robust_z(x)
    assert z[-1] > 30  # a plain z-score would be pulled down by the outlier inflating the std
    assert np.mean(np.abs(z[:-1]) > 3.5) < 0.01


def test_robust_z_handles_zero_mad():
    z = N.robust_z(np.array([1.0, 1.0, 1.0, 1.0, 5.0]))
    assert np.isfinite(z).all() and z[-1] > 0


def test_weekly_seasonality_alone_is_not_flagged():
    out = N.detect(weekly_series(), log=True)
    assert out["is_anomaly"].sum() <= 1


def test_injected_drop_is_flagged():
    out = N.detect(weekly_series(spike_at=45, spike=-0.5), log=True)
    assert bool(out["is_anomaly"].iloc[45])
    assert out["robust_z"].iloc[45] < -N.THRESHOLD
    assert out["value"].iloc[45] / out["expected"].iloc[45] - 1 < -0.3


def test_known_events_are_labelled(wh):
    res = N.detect_all(N.load_daily(wh))
    bf = res[res["date"] == pd.Timestamp("2020-11-27")]
    assert (bf["known_event"] == "Black Friday").all()
    assert set(res["kpi"]) == set(N.KPIS)


def test_drilldown_finds_the_mobile_payment_incident(wh):
    counts = N.drilldown_counts(wh, "2020-12-15", baseline_days=14)
    assert {"anomaly", "baseline"} == set(counts["period"])
    steps = N.step_comparison(counts, "device", "mobile")
    assert steps.loc["purchase", "relative_change"] < -0.5  # planted: x0.08 at purchase on mobile
    short = N.segment_shortfall(counts, "device")
    assert short["shortfall"].sum() < 0
    assert short.loc["mobile", "anomaly_rate"] < short.loc["mobile", "baseline_rate"]


def test_shortfall_shares_sum_to_one(wh):
    counts = N.drilldown_counts(wh, "2020-12-15")
    short = N.segment_shortfall(counts, "channel")
    assert short["share_of_shortfall"].sum() == pytest.approx(1.0)
