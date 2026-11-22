"""Build the modelled tables from the raw export by running `sql/` in order.

Every statement is dry-run first and its scan estimate printed, then executed.

Usage:
    python -m ga4_analytics.build                 # full window
    python -m ga4_analytics.build --start 20201201 --end 20201231
"""

from __future__ import annotations

import argparse
import re
import time

from ga4_analytics.config import END_SUFFIX, START_SUFFIX
from ga4_analytics.db import DEFAULT_DB, Warehouse, split_sql

MODEL_FILES = [
    "00_macros.sql",
    "01_events_flat.sql",
    "02_event_items.sql",
    "03_transactions.sql",
    "04_sessions.sql",
    "05_users.sql",
    "06_daily_kpis.sql",
]
TABLES = ["events_flat", "event_items", "transactions", "sessions", "users", "daily_kpis"]
_DDL_ONLY = re.compile(r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(TEMP\s+)?MACRO\b", re.IGNORECASE)


def build(wh: Warehouse, files: list[str] = MODEL_FILES) -> dict[str, int]:
    """Run each model file. Macros are DDL with nothing to scan, so they skip the dry run."""
    from ga4_analytics.config import SQL_DIR

    for name in files:
        started = time.perf_counter()
        if wh.verbose:
            print(f"-- {name}")
        for stmt in split_sql((SQL_DIR / name).read_text(encoding="utf-8")):
            wh.query(stmt, dry_run=not _DDL_ONLY.match(_first_code_line(stmt)))
        if wh.verbose:
            print(f"   done in {time.perf_counter() - started:.1f}s")
    return {t: wh.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def _first_code_line(stmt: str) -> str:
    lines = [ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build modelled tables from the raw export.")
    ap.add_argument("--start", default=START_SUFFIX, help="first _TABLE_SUFFIX (YYYYMMDD)")
    ap.add_argument("--end", default=END_SUFFIX, help="last _TABLE_SUFFIX (YYYYMMDD)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    with Warehouse(args.db, start=args.start, end=args.end) as wh:
        counts = build(wh)
    print("\nrow counts:")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>10,}")


if __name__ == "__main__":
    main()
