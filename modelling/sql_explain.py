"""Plain-English explanations for the mapping's SQL fragments (§1.1: explain in
business language, not SQL).

Deterministic — parses the constrained SELECT shape the agent emits (and humans edit):
    SELECT [DISTINCT] <case expr> AS case_id, <ts expr> AS timestamp
    FROM src_<table> <alias> [JOIN src_<table2> <alias2> ON <cond>] [WHERE <conds>]
Regenerated on every edit, so the explanation can never drift from the code.
"""

from __future__ import annotations

import re


def _unwrap_timestamp(expr: str) -> str:
    """The agent wraps timestamps in COALESCE(TRY_CAST(...)) — surface the column."""
    match = re.search(r"CAST\(\s*([\w.]+)\s+AS\s+VARCHAR\s*\)", expr, re.IGNORECASE)
    return match.group(1) if match else expr.strip()


def _strip_alias(name: str) -> str:
    return name.split(".")[-1]


def _plain_condition(cond: str) -> str:
    cond = cond.strip()
    cond = re.sub(r"\b\w+\.(\w+)", r"\1", cond)  # drop table aliases
    cond = re.sub(r"\s+IS\s+NOT\s+NULL", " is present", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\s*=\s*", " equals ", cond)
    return cond


def explain_parts(sql: str) -> list[tuple[str, str]]:
    """Structured, labeled reading of a transformation — professional register.

    Returns (label, text) pairs; explain_sql() joins them with ' | ' so the UI can
    render each part as its own row.
    """
    s = " ".join(sql.split())

    select = re.search(
        r"SELECT\s+(DISTINCT\s+)?(.+?)\s+AS\s+case_id\s*,\s*(.+?)\s+AS\s+timestamp\s+FROM\s+src_(\w+)(?:\s+(\w+))?",
        s, re.IGNORECASE,
    )
    if not select:
        return [(
            "Definition",
            "Custom SQL. It must return one row per event with the columns "
            "case_id and timestamp.",
        )]
    distinct, case_expr, ts_expr, table, _alias = select.groups()
    joins = re.findall(r"JOIN\s+src_(\w+)\s+\w+\s+ON\s+([\w.]+\s*=\s*[\w.]+)", s, re.IGNORECASE)
    where = re.search(r"WHERE\s+(.+?)$", s, re.IGNORECASE)

    parts: list[tuple[str, str]] = [("Source", f"{table.upper()} — one event per qualifying row")]
    for join_table, join_cond in joins:
        left, right = [x.strip() for x in join_cond.split("=")]
        parts.append(
            ("Join", f"{join_table.upper()} on {_strip_alias(left)} = {_strip_alias(right)}")
        )
    if where:
        conditions = [
            _plain_condition(c)
            for c in re.split(r"\s+AND\s+", where.group(1), flags=re.IGNORECASE)
            if c.strip() not in ("1=1", "1 = 1")
        ]
        if conditions:
            parts.append(("Filter", "; ".join(conditions)))

    case_txt = re.sub(r"\b\w+\.(\w+)", r"\1", case_expr)
    ts_col = _strip_alias(_unwrap_timestamp(ts_expr))
    parts.append(("Case reference", case_txt))
    parts.append(("Event timestamp",
                  f"{ts_col} — ISO and SAP (YYYYMMDD) date formats are normalized"))
    if distinct:
        parts.append(("Deduplication", "identical rows are collapsed into a single event"))
    return parts


def explain_sql(sql: str) -> str:
    return " | ".join(f"{label}: {text}" for label, text in explain_parts(sql))
