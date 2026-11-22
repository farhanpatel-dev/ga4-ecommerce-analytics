"""Run the Streamlit dashboard headlessly against a small generated build."""

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

from ga4_analytics.build import build  # noqa: E402
from ga4_analytics.config import ROOT  # noqa: E402
from ga4_analytics.db import Warehouse  # noqa: E402


@pytest.fixture(scope="module")
def dashboard_db(small_events, tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "ga4.duckdb"
    with Warehouse(db, events_dir=small_events, verbose=False) as w:
        build(w)
    return db


def test_dashboard_renders_every_tab(dashboard_db, small_events, monkeypatch):
    monkeypatch.setenv("GA4_DB", str(dashboard_db))
    monkeypatch.setenv("GA4_EVENTS_DIR", str(small_events))
    at = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=300).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.tabs) == 6
    labels = [m.label for m in at.metric]
    assert labels[:3] == ["Revenue", "Transactions", "AOV"]
    at.radio[1].set_value("channel").run()  # funnel split
    assert not at.exception, [e.value for e in at.exception]
