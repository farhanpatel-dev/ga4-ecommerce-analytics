import pandas as pd
import pytest

from ga4_analytics import attribution as A


def paths(rows):
    return pd.DataFrame(rows, columns=["path", "conversions", "non_conversions", "revenue_usd"])


TWO_STEP = paths([(["A", "B"], 10, 0, 100.0)])


def test_heuristics_follow_their_rules():
    assert A.last_click(TWO_STEP).loc["B", "conversions"] == 10
    assert A.first_click(TWO_STEP).loc["A", "conversions"] == 10
    lin = A.linear(TWO_STEP)
    assert lin.loc["A", "conversions"] == lin.loc["B", "conversions"] == 5


def test_every_model_conserves_totals():
    p = paths([
        (["A", "B", "C"], 7, 30, 350.0), (["A"], 3, 50, 90.0), (["B", "C"], 5, 20, 210.0),
        (["C", "A", "C"], 2, 10, 80.0), (["B"], 0, 40, 0.0),
    ])
    for name, fn in A.MODELS.items():
        out = fn(p)
        assert out["conversions"].sum() == pytest.approx(17), name
        assert out["revenue_usd"].sum() == pytest.approx(730), name


def test_single_touch_paths_agree_across_models():
    # Not shapley_rate: a rate game credits conversion *rates*, so even single-touch paths are not
    # credited with their own conversion counts. That is why it answers a different question.
    p = paths([(["A"], 6, 10, 60.0), (["B"], 4, 10, 40.0)])
    for name, fn in A.MODELS.items():
        if name == "shapley_rate":
            continue
        out = fn(p)
        assert out.loc["A", "conversions"] == pytest.approx(6)
        assert out.loc["B", "conversions"] == pytest.approx(4)


def test_shapley_count_is_an_equal_split_over_distinct_channels():
    # A repeated channel gets more under linear, but not under Shapley-count.
    p = paths([(["A", "B", "A"], 9, 0, 90.0)])
    assert A.linear(p).loc["A", "conversions"] == pytest.approx(6)
    sh = A.shapley(p)
    assert sh.loc["A", "conversions"] == pytest.approx(4.5)
    assert sh.loc["B", "conversions"] == pytest.approx(4.5)


def test_markov_conversion_probability_closed_form():
    # START -> A always; A converts 1 in 4.
    p = paths([(["A"], 1, 3, 10.0)])
    states, P = A.transition_matrix(p)
    assert A.conversion_probability(states, P) == pytest.approx(0.25)
    assert A.conversion_probability(states, P, removed="A") == pytest.approx(0.0)


def test_markov_removal_effect_of_a_necessary_channel_is_one():
    p = paths([(["A", "B"], 5, 5, 50.0), (["A"], 0, 10, 0.0)])
    re = A.removal_effects(p)
    assert re["A"] == pytest.approx(1.0)
    assert re["B"] == pytest.approx(1.0)  # B is the only route to conversion too


def test_markov_symmetric_channels_get_equal_credit():
    p = paths([(["A", "B"], 5, 5, 50.0), (["B", "A"], 5, 5, 50.0)])
    out = A.markov(p)
    assert out.loc["A", "conversions"] == pytest.approx(out.loc["B", "conversions"])


def test_position_profile_flags_openers():
    p = paths([(["A", "B"], 10, 0, 1.0), (["A", "C", "B"], 5, 0, 1.0)])
    prof = A.position_profile(p)
    assert prof.loc["A", "opener_ratio"] == 1.0 and prof.loc["B", "opener_ratio"] == 0.0


def test_attribution_on_generated_data_finds_channel_roles(wh):
    p = A.load_paths(wh)
    s = A.shares(A.compare(p))
    # Planted: Display and Organic Social open journeys, Email closes them.
    for opener in ("Display", "Organic Social"):
        assert s.loc[opener, "first_click"] > s.loc[opener, "last_click"]
    assert s.loc["Email", "last_click"] > s.loc["Email", "first_click"]
