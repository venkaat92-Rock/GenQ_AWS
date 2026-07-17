"""Live extractors (L1): pull source tables straight into a workspace's staging area.

Genuinely executable connectors — no mocks:
  sqlite   — path to a .db/.sqlite file on the server
  postgres — libpq DSN, via DuckDB's postgres scanner
  mysql    — DSN, via DuckDB's mysql scanner
  url      — CSV fetched over HTTP(S)

Connection strings are used for the single request and never persisted (§ privacy).
Everything lands as src_<table>, exactly like a CSV upload, so the modelling agent and
the whole downstream pipeline behave identically.
"""

from __future__ import annotations

import tempfile
import urllib.request
from pathlib import Path

from engine.db import EventLogStore, safe_table_name

MAX_URL_BYTES = 100 * 1024 * 1024  # 100 MB cap for URL fetches

_ATTACH_TYPES = {"sqlite": "SQLITE", "postgres": "POSTGRES", "mysql": "MYSQL"}


class ConnectorError(Exception):
    pass


def _quote_str(value: str) -> str:
    return value.replace("'", "''")


def extract_database(store: EventLogStore, kind: str, connection: str,
                     tables: list[str], schema: str | None = None) -> dict:
    if kind not in _ATTACH_TYPES:
        raise ConnectorError(f"unsupported database kind {kind!r}")
    if not tables:
        raise ConnectorError("list at least one table to extract")
    if kind == "sqlite" and not Path(connection).exists():
        raise ConnectorError(f"sqlite file not found: {connection}")

    alias = "extsrc"
    staged: dict[str, int] = {}
    try:
        store.sql(f"ATTACH '{_quote_str(connection)}' AS {alias} (TYPE {_ATTACH_TYPES[kind]}, READ_ONLY)")
    except Exception as exc:
        raise ConnectorError(f"could not connect: {exc}")
    try:
        for table in tables:
            target = safe_table_name(table)
            source = f'"{table.replace(chr(34), chr(34) * 2)}"'
            qualified = (
                f'{alias}."{schema}".{source}' if kind == "postgres" and schema
                else f"{alias}.{source}"
            )
            try:
                store.sql(f"CREATE OR REPLACE TABLE src_{target} AS SELECT * FROM {qualified}")
            except Exception as exc:
                raise ConnectorError(f"table {table!r}: {exc}")
            staged[target] = store.count(f"src_{target}")
    finally:
        try:
            store.sql(f"DETACH {alias}")
        except Exception:
            pass
    return {"staged": staged, "kind": kind}


def extract_url(store: EventLogStore, url: str, table: str) -> dict:
    if not url.lower().startswith(("http://", "https://")):
        raise ConnectorError("only http(s) URLs are supported")
    target = safe_table_name(table)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_URL_BYTES + 1)
    except Exception as exc:
        raise ConnectorError(f"could not fetch the URL: {exc}")
    if len(data) > MAX_URL_BYTES:
        raise ConnectorError("file larger than the 100 MB URL-fetch limit")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        rows = store.stage_csv(target, tmp_path)
    except Exception as exc:
        raise ConnectorError(f"the fetched file is not a readable CSV: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"staged": {target: rows}, "kind": "url"}
