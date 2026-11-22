"""Data-quality checks: measured defect rates, how each is handled, and what it limits."""

from __future__ import annotations

import json

import pandas as pd

from ga4_analytics.config import EVENTS_DIR
from ga4_analytics.db import Warehouse

# check_name -> (DEFECT_RATES key in the generator, handling in the pipeline, what it limits)
CHECKS = {
    "traffic_source placeholder (<Other> / (data deleted))": (
        ("traffic_source_other", "traffic_source_deleted"),
        "Channel taken from session event_params instead; first-touch channel marked Unassigned",
        "Any report built on traffic_source (user acquisition) loses ~5% of users to Unassigned",
    ),
    "geo.country = <Other>": (
        ("country_other",), "Kept as its own bucket",
        "Country cuts undercount every country; small countries can't be analysed",
    ),
    "device.operating_system = <Other>": (
        ("os_other",), "Kept as its own bucket",
        "OS-level diagnosis (e.g. iOS vs Android) has a 5% blind spot",
    ),
    "user_first_touch_timestamp NULL": (
        ("first_touch_null",), "Not used; first session derived from events",
        "Acquisition-date cohorts need the event-derived first session",
    ),
    "page_location = <Other>": (
        ("page_location_other",), "Not used in this analysis",
        "Page-level funnels (landing page, PDP) would lose 10% of views",
    ),
    "event without ga_session_id": (
        ("event_missing_session_id",), "Reattached to nearest tagged event within 30 min (01_events_flat.sql)",
        "Without repair: ~1% of events, including funnel events, drop out of sessions",
    ),
    "transaction_id = (not set)": (
        ("transaction_id_not_set",), "Dedup falls back to session + basket value",
        "Order-level joins (refunds, CRM, back office) impossible for 8% of orders",
    ),
    "purchase_revenue_in_usd NULL": (
        ("purchase_revenue_null",), "Repaired from the sum of item revenue",
        "Without repair: revenue understated ~2%, AOV biased",
    ),
    "duplicate purchase events": (
        ("purchase_duplicated",), "Deduplicated in 03_transactions.sql",
        "Without dedup: transactions and revenue overstated ~1.5%",
    ),
    "item_name = (not set)": (
        ("item_name_not_set",), "Kept, set to NULL in event_items",
        "Product/category analysis loses 3% of items; can't rank products precisely",
    ),
    "session without session_start": (
        ("session_start_missing",), "Session built from any event; channel from first page_view",
        "Counting sessions by session_start events undercounts by ~2%",
    ),
    "purchase session missing an earlier checkout step": (
        ("checkout_step_dropped",), "Closed funnel reported alongside open funnel",
        "Closed-funnel step rates are biased down at the late steps by a few percent",
    ),
    "untagged event left as its own session": (
        (), "Counted as sessions; flagged by has_repaired_events",
        "Tiny inflation of session counts",
    ),
    "ga_session_number out of time order": (
        (), "Journeys ordered by session timestamp; new vs returning uses ga_session_number as exported",
        "Found by QA, not planted: the generator gives same-day repeat sessions random hours. About 6% of "
        "users have a 'session 2' that starts before 'session 1', so new vs returning labels are noisy",
    ),
    "traffic_source channel <> first-session channel": (
        (), "Session channel is the source of truth for attribution",
        "About 5 points from traffic_source placeholders and 3.5 from sessions out of time order",
    ),
}

# Checks whose injected rate applies per opportunity rather than per unit.
PER_STEP = {"purchase session missing an earlier checkout step": 4}  # 4 intermediate checkout events


def run_checks(wh: Warehouse, params: dict | None = None) -> pd.DataFrame:
    df = wh.run_file("11_data_quality.sql", params)
    df["rate"] = df["numerator"] / df["denominator"]
    injected = _injected_rates()
    rates = []
    for c in df["check_name"]:
        r = sum(injected.get(k, 0.0) for k in CHECKS.get(c, ((),))[0]) or None
        if r is not None and c in PER_STEP:
            r = 1 - (1 - r) ** PER_STEP[c]  # P(at least one of n steps dropped)
        rates.append(r)
    df["injected_rate"] = rates
    df["handling"] = df["check_name"].map(lambda c: CHECKS.get(c, ((), "", ""))[1])
    df["limits"] = df["check_name"].map(lambda c: CHECKS.get(c, ((), "", ""))[2])
    return df


def _injected_rates() -> dict:
    meta = EVENTS_DIR / "_generation.json"
    if not meta.exists():
        return {}
    return json.loads(meta.read_text())["defect_rates"]
