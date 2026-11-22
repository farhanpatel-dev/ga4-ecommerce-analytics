"""Shared fixtures: a small generated export and a warehouse built from it."""

import pytest

from ga4_analytics.build import build
from ga4_analytics.db import Warehouse
from ga4_analytics.generate import generate


@pytest.fixture(scope="session")
def small_events(tmp_path_factory):
    out = tmp_path_factory.mktemp("events")
    generate(n_users=4000, seed=11, out_dir=out)
    return out


@pytest.fixture(scope="session")
def wh(small_events):
    warehouse = Warehouse(":memory:", events_dir=small_events, verbose=False)
    build(warehouse)
    yield warehouse
    warehouse.close()
