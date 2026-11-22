import numpy as np
import pandas as pd
import pytest

from ga4_analytics import metric_tree as M


def day(users, new, sessions, txns, items, revenue):
    row = pd.DataFrame([{"date": pd.Timestamp("2020-12-01"), "users": users, "new_users": new,
                         "returning_users": users - new, "sessions": sessions, "transactions": txns,
                         "items_sold": items, "revenue_usd": revenue}])
    return M.add_nodes(row).iloc[0]


def test_log_mean():
    assert M.log_mean(5.0, 5.0) == 5.0
    assert M.log_mean(np.e, 1.0) == pytest.approx(np.e - 1)
    with pytest.raises(ValueError):
        M.log_mean(0.0, 1.0)


def test_decomposition_is_exact():
    before = day(1000, 600, 1200, 30, 60, 1500.0)
    after = day(1300, 700, 1500, 33, 80, 1700.0)
    d = M.decompose(before, after)
    assert d["revenue_change"] == pytest.approx(200.0)
    assert d["check_residual"] == pytest.approx(0.0, abs=1e-9)
    assert d["users_new_users"] + d["users_returning_users"] == pytest.approx(d["users"])
    assert d["leaf_sessions_per_user"] + d["leaf_transactions_per_session"] == pytest.approx(d["conversion"])


def test_only_conversion_changes():
    before = day(1000, 600, 1000, 30, 60, 1500.0)
    after = day(1000, 600, 1000, 15, 30, 750.0)  # half the transactions, same basket
    d = M.decompose(before, after)
    assert d["conversion"] == pytest.approx(-750.0)
    assert d["users"] == pytest.approx(0.0, abs=1e-9)
    assert d["aov"] == pytest.approx(0.0, abs=1e-9)


def test_daily_decomposition_on_generated_data(wh):
    daily = M.load_daily(wh)
    dd = M.daily_decomposition(daily)
    assert len(dd) > 0
    assert dd["check_residual"].abs().max() < 1e-6
    wow = M.daily_decomposition(daily, lag=7)
    assert (wow["date"] - wow["compared_to"]).dt.days.eq(7).all()


def test_period_totals_rebuild_nodes(wh):
    daily = M.load_daily(wh)
    p = M.period_totals(daily, "2020-12-01", "2020-12-07")
    assert p["revenue_usd"] == pytest.approx(p["users"] * p["conversion"] * p["aov"])
