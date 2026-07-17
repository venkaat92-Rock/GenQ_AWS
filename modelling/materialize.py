"""L2 — Materialize a CONFIRMED mapping spec into the event-log store.

Hard gate (CLAUDE.md §1.1): refuses drafts and refuses specs with unanswered open
questions. The full generated SQL is written back onto the spec for audit (§1.6).
"""

from __future__ import annotations

from engine.db import EVENT_LOG_TABLE, EventLogStore
from modelling.spec import MappingNotConfirmed, MappingSpec


def build_event_log_sql(spec: MappingSpec) -> str:
    """Compose the per-activity SELECTs into one inspectable event-log query."""
    if not spec.activities:
        raise ValueError("spec has no activities to materialize")

    unions = "\nUNION ALL\n".join(
        f"SELECT case_id, '{a.name.replace(chr(39), chr(39) * 2)}' AS activity, "
        f"CAST(timestamp AS TIMESTAMP) AS timestamp FROM ({a.sql}) "
        f"WHERE case_id IS NOT NULL AND timestamp IS NOT NULL"
        for a in spec.activities
    )
    sql = f"WITH events AS (\n{unions}\n)\nSELECT e.case_id, e.activity, e.timestamp"

    joins = ""
    for i, attr in enumerate(spec.attributes):
        alias = f"attr{i}"
        sql += f", {alias}.{attr.name}"
        joins += f"\nLEFT JOIN ({attr.sql}) {alias} ON {alias}.case_id = e.case_id"

    sql += f"\nFROM events e{joins}\nORDER BY e.case_id, e.timestamp"
    return sql


def materialize(store: EventLogStore, spec: MappingSpec, table: str = EVENT_LOG_TABLE) -> dict:
    if spec.status != "confirmed":
        raise MappingNotConfirmed(
            "spec is not confirmed — a human must review and confirm the mapping first"
        )
    if spec.unanswered_questions():
        raise MappingNotConfirmed("spec still has unanswered open questions")

    sql = build_event_log_sql(spec)
    spec.generated_sql = sql
    n_events = store.materialize_sql(sql, table=table)
    df = store.fetch_event_log(table)
    return {
        "events": int(n_events),
        "cases": int(df["case_id"].nunique()),
        "activities": sorted(df["activity"].unique().tolist()),
    }
