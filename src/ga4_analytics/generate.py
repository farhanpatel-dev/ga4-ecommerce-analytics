"""Generate a GA4-schema ecommerce event export with planted, known effects.

The output mirrors the BigQuery export of `bigquery-public-data.ga4_obfuscated_sample_ecommerce`:
one Parquet file per day (`events_YYYYMMDD.parquet`, the `_TABLE_SUFFIX` convention) with the same
nested columns (`event_params`, `items`, `device`, `geo`, `traffic_source`, `ecommerce`).

The data is synthetic. Effects are planted on purpose so that every analysis has a ground truth
to recover (see `PLANTED`), and obfuscation-style defects are injected at known rates (see
`DEFECT_RATES`) so the data-quality checks have something real to find.

Usage:
    python -m ga4_analytics.generate --users 80000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ga4_analytics.config import EVENTS_DIR

START = date(2020, 11, 1)
N_DAYS = 92  # 2020-11-01 .. 2021-01-31

# ---------------------------------------------------------------------------
# Ground truth: what the analyses should find
# ---------------------------------------------------------------------------
PLANTED = {
    "mobile_checkout_friction": {
        "description": "Mobile sessions convert worse at the last two checkout steps.",
        "add_payment_info_given_shipping_multiplier": 0.85,
        "purchase_given_payment_multiplier": 0.72,
    },
    "payment_incident": {
        "description": "A mobile/tablet payment bug: purchase given add_payment_info collapses for one day.",
        "date": "2020-12-15",
        "devices": ["mobile", "tablet"],
        "purchase_given_payment_multiplier": 0.08,
    },
    "returning_users_convert_better": {
        "add_to_cart_multiplier": 1.25,
        "begin_checkout_multiplier": 1.10,
    },
    "channel_roles": {
        "description": "Display and Organic Social mostly open journeys; Direct and Email mostly close them.",
        "openers": ["Display", "Organic Social"],
        "closers": ["Direct", "Email"],
    },
    "black_friday_cyber_monday": {
        "traffic": {"2020-11-27": 2.2, "2020-11-28": 1.5, "2020-11-29": 1.4, "2020-11-30": 1.8},
        "add_to_cart_multiplier": 1.15,
    },
}

DEFECT_RATES = {
    "traffic_source_other": 0.03,  # user traffic_source -> '<Other>'
    "traffic_source_deleted": 0.02,  # user traffic_source -> '(data deleted)'
    "country_other": 0.04,
    "os_other": 0.05,
    "page_location_other": 0.10,
    "transaction_id_not_set": 0.08,
    "purchase_duplicated": 0.015,
    "purchase_revenue_null": 0.02,
    "item_name_not_set": 0.03,
    "event_missing_session_id": 0.01,
    "session_start_missing": 0.02,
    "checkout_step_dropped": 0.02,  # each intermediate funnel event, in purchasing sessions
    "first_touch_null": 0.01,
}

# ---------------------------------------------------------------------------
# Marketing channels: (source, medium, campaign) per channel and their roles
# ---------------------------------------------------------------------------
CHANNELS = [
    "Organic Search", "Direct", "Paid Search", "Referral",
    "Email", "Display", "Organic Social", "Affiliates",
]
CHANNEL_SOURCES = {
    "Organic Search": [("google", "organic", "(organic)"), ("bing", "organic", "(organic)")],
    "Direct": [("(direct)", "(none)", "(direct)")],
    "Paid Search": [("google", "cpc", "brand_search"), ("google", "cpc", "generic_search")],
    "Referral": [("blog.example.com", "referral", "(referral)"), ("forum.example.org", "referral", "(referral)")],
    "Email": [("newsletter", "email", "holiday_promo"), ("newsletter", "email", "weekly_digest")],
    "Display": [("dv360", "display", "prospecting"), ("google", "cpm", "remarketing")],
    "Organic Social": [("facebook.com", "social", "(social)"), ("youtube.com", "social", "(social)")],
    "Affiliates": [("partner_network", "affiliate", "(affiliate)")],
}
FIRST_SESSION_MIX = [0.30, 0.14, 0.16, 0.10, 0.02, 0.12, 0.12, 0.04]
LATER_SESSION_MIX = [0.22, 0.30, 0.12, 0.06, 0.18, 0.04, 0.05, 0.03]
# Multiplier on product engagement (view_item and add_to_cart) by channel.
CHANNEL_QUALITY = {
    "Organic Search": 1.0, "Direct": 1.15, "Paid Search": 1.10, "Referral": 0.95,
    "Email": 1.30, "Display": 0.60, "Organic Social": 0.70, "Affiliates": 1.0,
}

FUNNEL = ["view_item", "add_to_cart", "begin_checkout", "add_shipping_info", "add_payment_info", "purchase"]
BASE_STEP_PROB = {  # P(step | previous step)
    "view_item": 0.45,
    "add_to_cart": 0.28,
    "begin_checkout": 0.55,
    "add_shipping_info": 0.75,
    "add_payment_info": 0.80,
    "purchase": 0.70,
}

DEVICES = ["desktop", "mobile", "tablet"]
DEVICE_MIX = [0.56, 0.41, 0.03]
DEVICE_OS = {
    "desktop": (["Windows", "Macintosh", "Linux", "Chrome OS"], [0.55, 0.33, 0.06, 0.06]),
    "mobile": (["iOS", "Android"], [0.55, 0.45]),
    "tablet": (["iOS", "Android"], [0.7, 0.3]),
}
BROWSERS = (["Chrome", "Safari", "Edge", "Firefox"], [0.62, 0.25, 0.07, 0.06])
COUNTRIES = (
    ["United States", "India", "Canada", "United Kingdom", "Germany", "France", "Japan", "Spain", "Taiwan", "Brazil"],
    [0.45, 0.10, 0.08, 0.07, 0.06, 0.06, 0.05, 0.05, 0.04, 0.04],
)
CONTINENT = {
    "United States": "Americas", "Canada": "Americas", "Brazil": "Americas", "India": "Asia",
    "Japan": "Asia", "Taiwan": "Asia", "United Kingdom": "Europe", "Germany": "Europe",
    "France": "Europe", "Spain": "Europe",
}
CATEGORIES = {
    "Apparel": (28.0, 0.45), "Drinkware": (16.0, 0.35), "Bags": (34.0, 0.40),
    "Office": (8.0, 0.50), "Lifestyle": (12.0, 0.50), "Stationery": (6.0, 0.40),
    "Electronics Accessories": (22.0, 0.45), "Campus Collection": (18.0, 0.40),
}
PAGES = ["/", "/shop/apparel", "/shop/drinkware", "/shop/bags", "/shop/office", "/shop/lifestyle",
         "/shop/new", "/sale", "/basket", "/help"]


# ---------------------------------------------------------------------------
# Arrow schema: the GA4 BigQuery export, trimmed to the columns the analysis uses
# ---------------------------------------------------------------------------
PARAM_VALUE = pa.struct([
    ("string_value", pa.string()), ("int_value", pa.int64()),
    ("float_value", pa.float64()), ("double_value", pa.float64()),
])
ITEM = pa.struct([
    ("item_id", pa.string()), ("item_name", pa.string()), ("item_brand", pa.string()),
    ("item_variant", pa.string()), ("item_category", pa.string()),
    ("price_in_usd", pa.float64()), ("price", pa.float64()), ("quantity", pa.int64()),
    ("item_revenue_in_usd", pa.float64()), ("item_revenue", pa.float64()),
])
SCHEMA = pa.schema([
    ("event_date", pa.string()),
    ("event_timestamp", pa.int64()),
    ("event_name", pa.string()),
    ("event_params", pa.list_(pa.struct([("key", pa.string()), ("value", PARAM_VALUE)]))),
    ("user_id", pa.string()),
    ("user_pseudo_id", pa.string()),
    ("user_first_touch_timestamp", pa.int64()),
    ("device", pa.struct([
        ("category", pa.string()), ("operating_system", pa.string()),
        ("mobile_brand_name", pa.string()), ("language", pa.string()),
        ("web_info", pa.struct([("browser", pa.string())])),
    ])),
    ("geo", pa.struct([("continent", pa.string()), ("country", pa.string()), ("city", pa.string())])),
    ("traffic_source", pa.struct([("name", pa.string()), ("medium", pa.string()), ("source", pa.string())])),
    ("stream_id", pa.string()),
    ("platform", pa.string()),
    ("ecommerce", pa.struct([
        ("total_item_quantity", pa.int64()), ("purchase_revenue_in_usd", pa.float64()),
        ("purchase_revenue", pa.float64()), ("unique_items", pa.int64()),
        ("transaction_id", pa.string()),
    ])),
    ("items", pa.list_(ITEM)),
])


def _param(key: str, value) -> dict:
    v = {"string_value": None, "int_value": None, "float_value": None, "double_value": None}
    if isinstance(value, (bool, int, np.integer)):
        v["int_value"] = int(value)
    elif isinstance(value, float):
        v["double_value"] = value
    else:
        v["string_value"] = value
    return {"key": key, "value": v}


@dataclass
class Product:
    item_id: str
    name: str
    category: str
    price: float


@dataclass
class User:
    pseudo_id: str
    first_day: int
    device: str
    os: str
    browser: str
    country: str
    first_touch_ts: int | None
    traffic_source: tuple[str, str, str]  # (name/campaign, medium, source) as GA4 stores it
    sessions: list[tuple[int, str]] = field(default_factory=list)  # (day index, channel)


def _day_weights() -> np.ndarray:
    """Relative new-user arrivals per day: weekly cycle, holiday peaks, January slump."""
    w = np.ones(N_DAYS)
    for i in range(N_DAYS):
        d = START + timedelta(days=i)
        w[i] *= {5: 0.78, 6: 0.82}.get(d.weekday(), 1.0)  # Sat, Sun
        if date(2020, 12, 1) <= d <= date(2020, 12, 17):
            w[i] *= 1.30  # pre-Christmas shopping
        if date(2020, 12, 23) <= d <= date(2020, 12, 26):
            w[i] *= 0.60
        if d >= date(2021, 1, 1):
            w[i] *= 0.85
        w[i] *= PLANTED["black_friday_cyber_monday"]["traffic"].get(d.isoformat(), 1.0)
    return w / w.sum()


def _catalog(rng: np.random.Generator, n: int = 240) -> list[Product]:
    cats = list(CATEGORIES)
    products = []
    for i in range(n):
        cat = cats[i % len(cats)]
        median, sigma = CATEGORIES[cat]
        price = round(float(rng.lognormal(math.log(median), sigma)), 2)
        products.append(Product(f"GGOE{i:05d}", f"Google {cat} Item {i:03d}", cat, max(price, 1.99)))
    return products


def _build_users(rng: np.random.Generator, n_users: int) -> list[User]:
    first_days = rng.choice(N_DAYS, size=n_users, p=_day_weights())
    devices = rng.choice(DEVICES, size=n_users, p=DEVICE_MIX)
    countries = rng.choice(COUNTRIES[0], size=n_users, p=COUNTRIES[1])
    browsers = rng.choice(BROWSERS[0], size=n_users, p=BROWSERS[1])
    extra_sessions = np.minimum(rng.geometric(0.55, size=n_users) - 1, 11)
    users: list[User] = []
    for i in range(n_users):
        dev = str(devices[i])
        oses, os_p = DEVICE_OS[dev]
        day0 = int(first_days[i])
        # Session days: first visit, then exponential gaps (mean 6 days) inside the window.
        days = [day0]
        for _ in range(int(extra_sessions[i])):
            nxt = days[-1] + int(rng.exponential(6.0))
            if nxt >= N_DAYS:
                break
            days.append(nxt)
        chans = [str(rng.choice(CHANNELS, p=FIRST_SESSION_MIX))]
        chans += [str(c) for c in rng.choice(CHANNELS, size=len(days) - 1, p=LATER_SESSION_MIX)]
        src, med, camp = CHANNEL_SOURCES[chans[0]][int(rng.integers(len(CHANNEL_SOURCES[chans[0]])))]
        ts = datetime.combine(START + timedelta(days=day0), datetime.min.time(), tzinfo=UTC)
        users.append(User(
            pseudo_id=f"{rng.integers(10**9, 10**10)}.{rng.integers(10**9, 10**10)}",
            first_day=day0, device=dev, os=str(rng.choice(oses, p=os_p)), browser=str(browsers[i]),
            country=str(countries[i]),
            first_touch_ts=int(ts.timestamp() * 1e6) + int(rng.integers(0, 86_400)) * 1_000_000,
            traffic_source=(camp, med, src), sessions=list(zip(days, chans, strict=True)),
        ))
    return users


def _step_probs(user: User, day: int, session_number: int, channel: str) -> dict[str, float]:
    p = dict(BASE_STEP_PROB)
    q = CHANNEL_QUALITY[channel]
    p["view_item"] *= q
    p["add_to_cart"] *= q
    if session_number > 1:
        r = PLANTED["returning_users_convert_better"]
        p["add_to_cart"] *= r["add_to_cart_multiplier"]
        p["begin_checkout"] *= r["begin_checkout_multiplier"]
    if user.device == "mobile":
        m = PLANTED["mobile_checkout_friction"]
        p["add_payment_info"] *= m["add_payment_info_given_shipping_multiplier"]
        p["purchase"] *= m["purchase_given_payment_multiplier"]
    elif user.device == "tablet":
        p["purchase"] *= 0.90
    d = (START + timedelta(days=day)).isoformat()
    if d in PLANTED["black_friday_cyber_monday"]["traffic"]:
        p["add_to_cart"] *= PLANTED["black_friday_cyber_monday"]["add_to_cart_multiplier"]
    inc = PLANTED["payment_incident"]
    if d == inc["date"] and user.device in inc["devices"]:
        p["purchase"] *= inc["purchase_given_payment_multiplier"]
    return {k: min(v, 0.97) for k, v in p.items()}


def _item_dict(prod: Product, qty: int, name_not_set: bool) -> dict:
    return {
        "item_id": prod.item_id,
        "item_name": "(not set)" if name_not_set else prod.name,
        "item_brand": "Google", "item_variant": "(not set)",
        "item_category": "(not set)" if name_not_set else prod.category,
        "price_in_usd": prod.price, "price": prod.price, "quantity": qty,
        "item_revenue_in_usd": round(prod.price * qty, 2), "item_revenue": round(prod.price * qty, 2),
    }


def generate(n_users: int = 80_000, seed: int = 42, out_dir: Path = EVENTS_DIR) -> dict:
    """Write one Parquet file per day to `out_dir` and return summary counts."""
    rng = np.random.default_rng(seed)
    catalog = _catalog(rng)
    users = _build_users(rng, n_users)
    dr = DEFECT_RATES
    by_day: list[list[dict]] = [[] for _ in range(N_DAYS)]
    txn_counter = 0

    for u in users:
        is_other = rng.random()
        if is_other < dr["traffic_source_other"]:
            tsrc = {"name": "<Other>", "medium": "<Other>", "source": "<Other>"}
        elif is_other < dr["traffic_source_other"] + dr["traffic_source_deleted"]:
            tsrc = {"name": "(data deleted)", "medium": "(data deleted)", "source": "(data deleted)"}
        else:
            tsrc = {"name": u.traffic_source[0], "medium": u.traffic_source[1], "source": u.traffic_source[2]}
        country = "<Other>" if rng.random() < dr["country_other"] else u.country
        device = {
            "category": u.device,
            "operating_system": "<Other>" if rng.random() < dr["os_other"] else u.os,
            "mobile_brand_name": {"iOS": "Apple", "Android": "Samsung"}.get(u.os, "<Other>"),
            "language": "en-us", "web_info": {"browser": u.browser},
        }
        geo = {"continent": CONTINENT.get(country, "(not set)"), "country": country, "city": "(not set)"}
        first_touch = None if rng.random() < dr["first_touch_null"] else u.first_touch_ts

        for s_idx, (day, channel) in enumerate(u.sessions, start=1):
            day_start = datetime.combine(START + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
            # Session start: afternoon-peaked hour of day.
            hour = float(np.clip(rng.normal(15.0, 4.5), 0, 23.9))
            t = int((day_start.timestamp() + hour * 3600 + rng.integers(0, 3600)) * 1e6)
            t = min(t, int((day_start.timestamp() + 86_399) * 1e6) - 10**8)
            if s_idx == 1 and u.first_touch_ts:
                t = max(t, u.first_touch_ts)
            session_id = t // 1_000_000
            src, med, camp = CHANNEL_SOURCES[channel][int(rng.integers(len(CHANNEL_SOURCES[channel])))]
            probs = _step_probs(u, day, s_idx, channel)

            # Which funnel steps happen (each conditional on the previous one).
            reached = []
            for step in FUNNEL:
                if rng.random() < probs[step]:
                    reached.append(step)
                else:
                    break
            cart: list[tuple[Product, int]] = []
            if "add_to_cart" in reached:
                for _ in range(1 + int(rng.poisson(0.8))):
                    cart.append((catalog[int(rng.integers(len(catalog)))], 1 + int(rng.poisson(0.3))))

            events: list[tuple[str, list[dict], dict | None]] = []
            session_params = [_param("source", src), _param("medium", med), _param("campaign", camp)]
            if rng.random() >= dr["session_start_missing"]:
                events.append(("session_start", session_params, None))
            if s_idx == 1:
                events.append(("first_visit", [], None))
            n_pages = 1 + int(rng.poisson(2.5))
            for p_i in range(n_pages):
                page = PAGES[int(rng.integers(len(PAGES)))]
                extra = session_params if p_i == 0 else []
                events.append(("page_view", extra + [_param("page_location",
                    "<Other>" if rng.random() < dr["page_location_other"]
                    else f"https://shop.googlemerchandisestore.com{page}")], None))
            purchased = "purchase" in reached
            for step in reached:
                if step == "view_item":
                    viewed = cart[:1] or [(catalog[int(rng.integers(len(catalog)))], 1)]
                    for prod, _ in viewed * (1 + int(rng.poisson(0.6))):
                        events.append(("view_item", [], {"items": [(prod, 1)]}))
                    continue
                if purchased and step != "purchase" and rng.random() < dr["checkout_step_dropped"]:
                    continue  # tracking gap: a checkout step lost in a session that still purchased
                events.append((step, [], {"items": cart}))

            for name, params, payload in events:
                t += int(rng.exponential(25.0) * 1e6) + 1_000_000
                base_params = [] if rng.random() < dr["event_missing_session_id"] else [
                    _param("ga_session_id", int(session_id)), _param("ga_session_number", s_idx)]
                ev_params = base_params + params + [_param("engagement_time_msec", int(rng.exponential(8000)))]
                items = []
                ecommerce = None
                if payload:
                    items = [_item_dict(p, q, rng.random() < dr["item_name_not_set"]) for p, q in payload["items"]]
                if name == "purchase":
                    txn_counter += 1
                    revenue = round(sum(i["item_revenue_in_usd"] for i in items), 2)
                    ecommerce = {
                        "total_item_quantity": sum(i["quantity"] for i in items),
                        "purchase_revenue_in_usd": None if rng.random() < dr["purchase_revenue_null"] else revenue,
                        "purchase_revenue": revenue,
                        "unique_items": len(items),
                        "transaction_id": "(not set)" if rng.random() < dr["transaction_id_not_set"]
                        else f"T{txn_counter:07d}",
                    }
                row = {
                    "event_date": (START + timedelta(days=day)).strftime("%Y%m%d"),
                    "event_timestamp": t, "event_name": name, "event_params": ev_params,
                    "user_id": None, "user_pseudo_id": u.pseudo_id, "user_first_touch_timestamp": first_touch,
                    "device": device, "geo": geo, "traffic_source": tsrc,
                    "stream_id": "2100450278", "platform": "WEB", "ecommerce": ecommerce, "items": items,
                }
                by_day[day].append(row)
                if name == "purchase" and rng.random() < dr["purchase_duplicated"]:
                    dup = dict(row, event_timestamp=t + int(rng.integers(1, 4)) * 1_000_000)
                    by_day[day].append(dup)  # same transaction sent twice

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("events_*.parquet"):
        old.unlink()
    total = 0
    for day, rows in enumerate(by_day):
        rows.sort(key=lambda r: r["event_timestamp"])
        suffix = (START + timedelta(days=day)).strftime("%Y%m%d")
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), out_dir / f"events_{suffix}.parquet",
                       compression="zstd")
        total += len(rows)
    summary = {"users": n_users, "sessions": sum(len(u.sessions) for u in users), "events": total,
               "days": N_DAYS, "seed": seed}
    (out_dir / "_generation.json").write_text(json.dumps(
        {"summary": summary, "planted": PLANTED, "defect_rates": DEFECT_RATES}, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--users", type=int, default=80_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=EVENTS_DIR)
    args = ap.parse_args()
    print(json.dumps(generate(args.users, args.seed, args.out), indent=2))


if __name__ == "__main__":
    main()
