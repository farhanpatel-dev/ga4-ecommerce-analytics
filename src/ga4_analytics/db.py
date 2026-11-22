"""DuckDB access that keeps the BigQuery habits from the brief.

- The raw export is exposed as a view, `events`, over the daily Parquet files, with a
  `_TABLE_SUFFIX` column derived from the file name, so SQL filters it as on `events_*`.
- Every query is dry-run first. The dry run validates the plan, works out which daily files
  survive the `_TABLE_SUFFIX` filter and which columns are read, and prints the compressed bytes
  that scan touches. Parquet is columnar, like BigQuery storage, so this is the same
  "bytes processed" idea: unread columns and pruned days cost nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from ga4_analytics.config import DATA_DIR, END_SUFFIX, EVENTS_DIR, SQL_DIR, START_SUFFIX

DEFAULT_DB = DATA_DIR / "ga4.duckdb"
SUFFIX_RE = re.compile(r"events_(\d{8})\.parquet$")


@dataclass
class DryRun:
    """What a query would scan, before running it."""

    files: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    bytes_scanned: int = 0
    reads_tables: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.files:
            parts.append(
                f"{_fmt_bytes(self.bytes_scanned)} from {len(self.files)} daily file(s) "
                f"[{self.files[0][7:15]}..{self.files[-1][7:15]}], columns: {', '.join(self.columns)}"
            )
        if self.reads_tables:
            parts.append(f"local tables: {', '.join(sorted(set(self.reads_tables)))}")
        return "dry run OK | " + (" | ".join(parts) if parts else "no table reads")


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


@cache
def _column_bytes(path: str, mtime: float) -> dict[str, int]:
    """Compressed bytes per leaf column of one Parquet file (cached per file version).

    Leaf paths are normalised to DuckDB's projection names: `.list.element` is dropped, so
    `items.list.element.price` becomes `items.price`, and struct fields stay dotted (`device.category`).
    """
    meta = pq.ParquetFile(path).metadata
    sizes: dict[str, int] = {}
    for rg in range(meta.num_row_groups):
        group = meta.row_group(rg)
        for c in range(group.num_columns):
            col = group.column(c)
            leaf = col.path_in_schema.replace(".list.element", "")
            sizes[leaf] = sizes.get(leaf, 0) + col.total_compressed_size
    return sizes


def _projection_bytes(sizes: dict[str, int], projection: str) -> int:
    """Bytes for one projected column: the leaf itself, or every leaf under a struct/list column."""
    return sum(v for k, v in sizes.items() if k == projection or k.startswith(projection + "."))


def _plan_nodes(node: dict):
    yield node
    for child in node.get("children", []):
        yield from _plan_nodes(child)


class Warehouse:
    """A DuckDB database plus the `events` view over the daily export files."""

    def __init__(self, db_path: Path | str = DEFAULT_DB, events_dir: Path = EVENTS_DIR,
                 start: str = START_SUFFIX, end: str = END_SUFFIX, verbose: bool = True):
        self.events_dir = Path(events_dir)
        self.start, self.end = start, end
        self.verbose = verbose
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(db_path))
        self._register_events()

    # -- setup ------------------------------------------------------------------
    def _register_events(self) -> None:
        files = sorted(self.events_dir.glob("events_*.parquet"))
        if not files:
            raise FileNotFoundError(
                f"No events_*.parquet in {self.events_dir}. Run: python -m ga4_analytics.generate")
        self._files = {SUFFIX_RE.search(f.name).group(1): f for f in files}
        file_list = [f.as_posix() for f in files]
        self.con.execute(f"""
            CREATE OR REPLACE VIEW events AS
            SELECT * EXCLUDE (filename),
                   regexp_extract(filename, 'events_(\\d{{8}})\\.parquet', 1) AS _TABLE_SUFFIX
            FROM read_parquet({file_list}, filename = true)
        """)

    def params(self, **extra) -> dict:
        """Default named parameters: the analysis window as `_TABLE_SUFFIX` bounds."""
        return {"start_suffix": self.start, "end_suffix": self.end, **extra}

    # -- dry run ------------------------------------------------------------------
    def dry_run(self, sql: str, params: dict | None = None) -> DryRun:
        """Validate `sql` and estimate what it scans, without executing it."""
        params = self._used_params(sql, params)
        rows = self.con.execute(f"EXPLAIN (FORMAT json) {sql}", params).fetchall()
        plan = json.loads(rows[-1][1])
        result = DryRun()
        files: set[str] = set()
        columns: set[str] = set()
        for root in plan:
            for node in _plan_nodes(root):
                info = node.get("extra_info", {})
                if info.get("Function") == "READ_PARQUET":
                    projections = info.get("Projections", [])
                    columns.update([projections] if isinstance(projections, str) else projections)
                    files.update(self._files_passing(info.get("File Filters")))
                elif node.get("name") in ("TABLE_SCAN", "SEQ_SCAN") and info.get("Table"):
                    result.reads_tables.append(info["Table"])
        sample = _column_bytes(*self._file_key(next(iter(self._files.values()))))
        known = {c for c in columns if _projection_bytes(sample, c) > 0}
        result.columns = sorted(known)
        result.files = sorted(Path(self._files[s]).name for s in files)
        result.bytes_scanned = sum(
            _projection_bytes(_column_bytes(*self._file_key(self._files[s])), c) for s in files for c in known)
        return result

    def _file_key(self, path: Path) -> tuple[str, float]:
        return str(path), path.stat().st_mtime

    def _files_passing(self, file_filter: str | None) -> list[str]:
        """Suffixes of the daily files that survive the plan's pushed-down file filter."""
        suffixes = list(self._files)
        if not file_filter:
            return suffixes
        names = [self._files[s].as_posix() for s in suffixes]
        kept = self.con.execute(
            f"SELECT filename FROM unnest(?::VARCHAR[]) AS t(filename) WHERE {file_filter}", [names]
        ).fetchall()
        return [SUFFIX_RE.search(k[0]).group(1) for k in kept]

    @staticmethod
    def _used_params(sql: str, params: dict | None) -> dict:
        """DuckDB rejects unused named parameters, so pass only the ones the SQL mentions."""
        params = params or {}
        return {k: v for k, v in params.items() if re.search(rf"\${k}\b", sql)}

    # -- execution ------------------------------------------------------------------
    def query(self, sql: str, params: dict | None = None, dry_run: bool = True) -> pd.DataFrame:
        """Dry-run (and print the scan estimate), then execute and return a DataFrame."""
        params = self._used_params(sql, self.params() | (params or {}))
        if dry_run:
            estimate = self.dry_run(sql, params)
            if self.verbose:
                print(estimate.summary())
        return self.con.execute(sql, params).df()

    def run_file(self, name: str, params: dict | None = None, dry_run: bool = True) -> pd.DataFrame:
        """Run one file from `sql/` (all statements; returns the last statement's result)."""
        text = (SQL_DIR / name).read_text(encoding="utf-8")
        statements = [s.strip() for s in split_sql(text) if s.strip()]
        if self.verbose:
            print(f"-- {name}")
        result = pd.DataFrame()
        for stmt in statements:
            result = self.query(stmt, params, dry_run=dry_run)
        return result

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def split_sql(text: str) -> list[str]:
    """Split a SQL file on semicolons that end a statement (ignores ones in quotes/comments)."""
    out, buf, quote, i = [], [], None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif text.startswith("--", i):
            j = text.find("\n", i)
            j = len(text) if j == -1 else j
            buf.append(text[i:j])
            i = j
            continue
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if _strip_comments(s).strip()]


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
