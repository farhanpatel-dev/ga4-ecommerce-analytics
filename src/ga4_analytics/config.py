"""Paths and the analysis window, in one place."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EVENTS_DIR = DATA_DIR / "events"  # one Parquet file per day: events_YYYYMMDD.parquet
SQL_DIR = ROOT / "sql"
DOCS_DIR = ROOT / "docs"
FIGURES_DIR = DOCS_DIR / "figures"

# Same window as the public GA4 sample (bigquery-public-data.ga4_obfuscated_sample_ecommerce).
START_SUFFIX = "20201101"
END_SUFFIX = "20210131"
