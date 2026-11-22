import json

import duckdb
import pytest

from ga4_analytics.generate import N_DAYS, SCHEMA, generate


@pytest.fixture(scope="module")
def small_export(tmp_path_factory):
    out = tmp_path_factory.mktemp("events")
    summary = generate(n_users=3000, seed=7, out_dir=out)
    return out, summary


def test_one_file_per_day_with_ga4_suffix(small_export):
    out, _ = small_export
    files = sorted(p.name for p in out.glob("events_*.parquet"))
    assert len(files) == N_DAYS
    assert files[0] == "events_20201101.parquet" and files[-1] == "events_20210131.parquet"


def test_schema_matches_ga4_export(small_export):
    out, _ = small_export
    con = duckdb.connect()
    cols = con.sql(f"DESCRIBE SELECT * FROM read_parquet('{out.as_posix()}/events_*.parquet')").df()
    assert list(cols["column_name"]) == SCHEMA.names
    types = dict(zip(cols["column_name"], cols["column_type"], strict=True))
    assert types["event_params"].startswith("STRUCT(\"key\" VARCHAR, \"value\" STRUCT(")
    assert "item_revenue_in_usd" in types["items"]


def test_generation_is_deterministic(tmp_path):
    a = generate(n_users=300, seed=1, out_dir=tmp_path / "a")
    b = generate(n_users=300, seed=1, out_dir=tmp_path / "b")
    assert a == b


def test_ground_truth_is_written(small_export):
    out, summary = small_export
    meta = json.loads((out / "_generation.json").read_text())
    assert meta["summary"] == summary
    assert meta["planted"]["payment_incident"]["date"] == "2020-12-15"


def test_events_carry_session_ids(small_export):
    out, _ = small_export
    con = duckdb.connect()
    share = con.execute(f"""
        SELECT avg(CASE WHEN len(list_filter(event_params, lambda p: p.key = 'ga_session_id')) > 0
                        THEN 1 ELSE 0 END)
        FROM read_parquet('{out.as_posix()}/events_*.parquet')
    """).fetchone()[0]
    assert 0.97 < share < 1.0  # about 1% of events lose ga_session_id on purpose
