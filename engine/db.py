"""L3 — Event-log store backed by DuckDB.

DuckDB is infrastructure, not the moat (CLAUDE.md §1.3). Keep every query ANSI-ish
and route all access through EventLogStore so a ClickHouse backend can sit behind
the same interface for larger tenants later.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

EVENT_LOG_TABLE = "event_log"

# Canonical event-log columns every materialized log must expose.
REQUIRED_COLUMNS = ("case_id", "activity", "timestamp")

_VALID_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_table_name(name: str) -> str:
    name = name.strip().replace("-", "_").replace(" ", "_")
    if not _VALID_TABLE.match(name):
        raise ValueError(f"invalid table name: {name!r}")
    return name.lower()


class EventLogStore:
    """Embedded analytical store holding materialized event logs."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self._con = duckdb.connect(str(db_path))

    def load_event_log_csv(self, csv_path: str | Path, table: str = EVENT_LOG_TABLE) -> int:
        """Load a CSV event log into `table`, validate shape, return row count."""
        self._con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true)",
            [str(csv_path)],
        )
        cols = {r[0] for r in self._con.execute(f"DESCRIBE {table}").fetchall()}
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise ValueError(f"event log {csv_path} is missing required columns: {missing}")
        return self.count(table)

    def count(self, table: str = EVENT_LOG_TABLE) -> int:
        return self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def fetch_event_log(self, table: str = EVENT_LOG_TABLE) -> pd.DataFrame:
        """Return the canonical (case_id, activity, timestamp) frame, event-ordered."""
        return self._con.execute(
            f"SELECT case_id, activity, timestamp FROM {table} ORDER BY case_id, timestamp"
        ).fetchdf()

    def sql(self, query: str) -> pd.DataFrame:
        return self._con.execute(query).fetchdf()

    # ---- source staging (what the L2 agent reads) ----------------------------------

    def stage_csv(self, table: str, csv_path: str | Path) -> int:
        """Stage a raw source table (e.g. EKKO) for the modelling agent to inspect."""
        table = safe_table_name(table)
        self._con.execute(
            f"CREATE OR REPLACE TABLE src_{table} AS SELECT * FROM read_csv_auto(?, header=true)",
            [str(csv_path)],
        )
        return self.count(f"src_{table}")

    def stage_rows(self, table: str, rows: list[dict]) -> int:
        """Stage API-extracted records (list of dicts) as a source table."""
        if not rows:
            raise ValueError(f"no rows returned for table {table!r}")
        table = safe_table_name(table)
        df = pd.DataFrame(rows)
        # flatten nested structures the way a CSV export would
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, (dict, list))).any():
                df[col] = df[col].map(
                    lambda v: str(v) if isinstance(v, (dict, list)) else v
                )
        self._con.register("_incoming_rows", df)
        try:
            self._con.execute(
                f"CREATE OR REPLACE TABLE src_{table} AS SELECT * FROM _incoming_rows"
            )
        finally:
            self._con.unregister("_incoming_rows")
        return self.count(f"src_{table}")

    def source_tables(self) -> list[str]:
        rows = self._con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'src_%' ORDER BY table_name"
        ).fetchall()
        return [r[0].removeprefix("src_") for r in rows]

    def schema_summary(self, sample_rows: int = 3) -> dict:
        """Schema + sample rows per staged source table — the agent's raw material."""
        summary = {}
        for table in self.source_tables():
            cols = self._con.execute(f"DESCRIBE src_{table}").fetchall()
            sample = self._con.execute(
                f"SELECT * FROM src_{table} LIMIT {int(sample_rows)}"
            ).fetchdf()
            # NaN/NaT must not leak into API responses (strict JSON forbids NaN)
            records = [
                {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}
                for row in sample.to_dict(orient="records")
            ]
            summary[table.upper()] = {
                "columns": [{"name": c[0], "type": c[1]} for c in cols],
                "row_count": self.count(f"src_{table}"),
                "sample": records,
            }
        return summary

    def materialize_sql(self, sql: str, table: str = EVENT_LOG_TABLE) -> int:
        """Materialize a generated event-log query into `table` and validate its shape."""
        self._con.execute(f"CREATE OR REPLACE TABLE {safe_table_name(table)} AS {sql}")
        cols = {r[0] for r in self._con.execute(f"DESCRIBE {table}").fetchall()}
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise ValueError(f"materialized log is missing required columns: {missing}")
        return self.count(table)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "EventLogStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
