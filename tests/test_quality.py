import pytest

from ga4_analytics import quality as Q


@pytest.fixture(scope="module")
def checks(wh):
    return Q.run_checks(wh).set_index("check_name")


def test_every_check_is_documented(checks):
    assert set(checks.index) == set(Q.CHECKS)
    assert (checks["handling"] != "").all() and (checks["limits"] != "").all()


def test_rates_are_valid_proportions(checks):
    assert ((checks["rate"] >= 0) & (checks["rate"] <= 1)).all()


@pytest.mark.parametrize("check", [
    "traffic_source placeholder (<Other> / (data deleted))",
    "page_location = <Other>",
    "event without ga_session_id",
    "item_name = (not set)",
])
def test_measured_rates_track_injected_rates(checks, check):
    # The small test export uses a different seed, so compare against the generator's known rates.
    expected = {"traffic_source placeholder (<Other> / (data deleted))": 0.05, "page_location = <Other>": 0.10,
                "event without ga_session_id": 0.01, "item_name = (not set)": 0.03}[check]
    assert checks.loc[check, "rate"] == pytest.approx(expected, rel=0.35)


def test_repairs_leave_almost_no_orphan_sessions(checks):
    assert checks.loc["untagged event left as its own session", "rate"] < 0.001
