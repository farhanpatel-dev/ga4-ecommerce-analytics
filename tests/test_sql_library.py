import pytest

from ga4_analytics.db import Warehouse, split_sql


def scalar(wh, sql):
    return wh.con.execute(sql).fetchone()[0]


def test_every_event_gets_a_session(wh):
    assert scalar(wh, "SELECT count(*) FROM events_flat WHERE ga_session_id IS NULL") == 0
    assert scalar(wh, "SELECT count(*) FROM events_flat") == scalar(wh, "SELECT count(*) FROM events")


def test_one_session_row_per_session_key(wh):
    assert scalar(wh, "SELECT count(*) FROM sessions") == scalar(
        wh, "SELECT count(DISTINCT session_key) FROM events_flat")


def test_session_repair_reattaches_most_untagged_events(wh):
    untagged = scalar(wh, "SELECT count(*) FROM events_flat WHERE session_id_repaired")
    orphans = scalar(wh, """
        SELECT count(*) FROM sessions WHERE events = 1 AND has_repaired_events""")
    assert untagged > 0
    assert orphans / untagged < 0.05  # almost all join a real session


def test_transactions_are_deduplicated(wh):
    purchases = scalar(wh, "SELECT count(*) FROM events_flat WHERE event_name = 'purchase'")
    txns = scalar(wh, "SELECT count(*) FROM transactions")
    assert txns < purchases  # duplicates were injected
    assert scalar(wh, """
        SELECT count(*) FROM (SELECT transaction_id FROM transactions
                              WHERE transaction_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)""") == 0


def test_null_revenue_is_repaired(wh):
    assert scalar(wh, "SELECT count(*) FROM transactions WHERE revenue_usd IS NULL") == 0
    assert scalar(wh, "SELECT count(*) FROM transactions WHERE revenue_repaired") > 0


def test_closed_funnel_never_exceeds_open_funnel(wh):
    row = wh.con.execute("""
        SELECT count(*) FILTER (WHERE funnel_depth = 6), count(*) FILTER (WHERE reached_purchase)
        FROM sessions""").fetchone()
    assert row[0] <= row[1]


def test_channels_come_from_session_params(wh):
    channels = {r[0] for r in wh.con.execute("SELECT DISTINCT channel FROM sessions").fetchall()}
    assert {"Direct", "Email", "Display", "Organic Social", "Paid Search"} <= channels


def test_revenue_identity_holds_daily(wh):
    worst = scalar(wh, """
        SELECT max(abs(revenue_usd - users * conversion_rate * aov_usd))
        FROM daily_kpis WHERE transactions > 0""")
    assert worst < 0.01


def test_users_table_matches_sessions(wh):
    assert scalar(wh, "SELECT sum(sessions) FROM users") == scalar(wh, "SELECT count(*) FROM sessions")
    assert scalar(wh, "SELECT round(sum(revenue_usd), 2) FROM users") == pytest.approx(
        scalar(wh, "SELECT round(sum(revenue_usd), 2) FROM transactions"))


def test_dry_run_prunes_days_and_columns(small_events):
    w = Warehouse(":memory:", events_dir=small_events, verbose=False)
    full = w.dry_run("SELECT count(*) FROM events WHERE _TABLE_SUFFIX BETWEEN $s AND $e",
                     {"s": "20201101", "e": "20210131"})
    one_day = w.dry_run("SELECT event_name, user_pseudo_id FROM events WHERE _TABLE_SUFFIX = '20201215'")
    assert len(full.files) == 92
    assert one_day.files == ["events_20201215.parquet"]
    assert one_day.columns == ["event_name", "user_pseudo_id"]
    assert 0 < one_day.bytes_scanned < full.bytes_scanned or full.bytes_scanned == 0


def test_split_sql_ignores_semicolons_in_strings_and_comments():
    text = "SELECT ';' AS a; -- not; a split\nSELECT 2;\n-- trailing comment only\n"
    parts = [p.strip() for p in split_sql(text)]
    assert parts[0] == "SELECT ';' AS a"
    assert parts[1].endswith("SELECT 2")
    assert len(parts) == 2
