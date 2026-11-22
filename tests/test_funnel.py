import numpy as np
import pandas as pd
import pytest
from statsmodels.stats.proportion import proportion_confint, proportions_ztest

from ga4_analytics import funnel as F


def test_wilson_matches_statsmodels():
    low, high = F.wilson_ci(37, 412)
    sm_low, sm_high = proportion_confint(37, 412, method="wilson")
    assert low == pytest.approx(sm_low) and high == pytest.approx(sm_high)


def test_z_test_matches_statsmodels():
    t = F.two_proportion_test(120, 4000, 90, 4100)
    z, p = proportions_ztest([120, 90], [4000, 4100])
    assert t.z == pytest.approx(z) and t.p_value == pytest.approx(p)
    assert t.ci_low < t.diff < t.ci_high


def test_no_difference_is_not_significant():
    t = F.two_proportion_test(50, 1000, 50, 1000)
    assert t.diff == 0 and t.p_value == pytest.approx(1.0)


def test_chi_square_detects_a_difference():
    _, p_same, _ = F.chi_square([30, 31, 29], [1000, 1000, 1000])
    _, p_diff, dof = F.chi_square([30, 60, 29], [1000, 1000, 1000])
    assert p_same > 0.5 and p_diff < 0.001 and dof == 2


def test_bootstrap_widens_intervals_for_clustered_users():
    rng = np.random.default_rng(0)
    rows = []
    for u in range(3000):  # users with strong individual propensity and 5 sessions each
        group = "a" if u % 2 else "b"
        p = rng.choice([0.01, 0.4]) + (0.03 if group == "a" else 0.0)
        rows += [{"user_pseudo_id": u, "g": group, "y": int(rng.random() < p)} for _ in range(5)]
    out = F.cluster_bootstrap_diff(pd.DataFrame(rows), "g", "a", "b", outcome="y", n_boot=500)
    assert out["design_effect"] > 1.5  # clustering matters when users differ


def test_mde_shrinks_with_sample_size():
    assert F.minimum_detectable_effect(0.03, 100_000, 100_000) < F.minimum_detectable_effect(0.03, 5_000, 5_000)


def test_funnel_on_generated_data(wh):
    segments = F.funnel_segments(wh)
    closed = F.funnel_long(segments, "closed")
    assert set(segments["dimension"]) == {"all", "device", "channel", "user_type"}
    total = segments.query("dimension == 'all'")["sessions"].iloc[0]
    assert segments.query("dimension == 'device'")["sessions"].sum() == total
    # Closed reach can only shrink step to step.
    allr = closed[closed["dimension"] == "all"]["reached"].to_numpy()
    assert (np.diff(allr) <= 0).all()
    # The planted mobile purchase friction shows up in direction even on a small sample.
    steps = F.step_tests(closed, "device", "mobile", "desktop").set_index("step")
    assert steps.loc["purchase", "diff"] < 0
    comp = F.compare_segments(closed, "channel")
    assert {"p_holm", "significant", "cohens_h"} <= set(comp.columns)


def test_empty_group_returns_nan_instead_of_raising():
    t = F.two_proportion_test(0, 0, 5, 100)
    assert np.isnan(t.diff) and np.isnan(t.p_value) and t.p2 == 0.05
