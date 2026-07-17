"""L4 — Case-level view of the event log: one row per case with derived attributes.

This backs the case explorer and the global analysis filters. Everything is derived
from the canonical event log — no extra modelling input needed.
"""

from __future__ import annotations

import pandas as pd


def case_table(df: pd.DataFrame, reference: list[str] | None = None) -> pd.DataFrame:
    """One row per case: start/end, duration, variant, rework + conformance flags,
    plus first value of any case attributes (vendor, net_value, ...)."""
    log = df.copy()
    log["timestamp"] = pd.to_datetime(log["timestamp"])
    log = log.sort_values(["case_id", "timestamp"])
    grouped = log.groupby("case_id")

    table = pd.DataFrame(
        {
            "start": grouped["timestamp"].min(),
            "end": grouped["timestamp"].max(),
            "events": grouped.size(),
            "variant": grouped["activity"].apply(lambda s: " -> ".join(s)),
            "rework": grouped["activity"].apply(lambda s: bool(s.duplicated().any())),
        }
    )
    table["duration_days"] = (table["end"] - table["start"]).dt.total_seconds() / 86400.0

    # carry every case attribute the mapping materialized (vendor, customer, net_value, ...)
    for attr in log.columns:
        if attr not in ("case_id", "activity", "timestamp"):
            table[attr] = grouped[attr].first()

    if reference:
        table["conformant"] = table["variant"] == " -> ".join(reference)

    return table.reset_index()


def to_records(table: pd.DataFrame) -> list[dict]:
    """JSON-safe records for the API."""
    out = table.copy()
    out["case_id"] = out["case_id"].astype(str)
    out["start"] = out["start"].astype(str)
    out["end"] = out["end"].astype(str)
    out["duration_days"] = out["duration_days"].astype(float).round(2)
    if "net_value" in out:
        out["net_value"] = out["net_value"].astype(float)
    return out.where(out.notna(), None).to_dict(orient="records")
