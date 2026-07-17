"""L4 — Variant analysis, wrapping PM4Py (CLAUDE.md §1.4: off-the-shelf algorithms)."""

from __future__ import annotations

import pandas as pd
import pm4py


def _to_pm4py_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return pm4py.format_dataframe(
        df, case_id="case_id", activity_key="activity", timestamp_key="timestamp"
    )


def variant_analysis(df: pd.DataFrame) -> list[dict]:
    """Compute trace variants over a canonical event-log frame.

    Returns variants sorted by descending case count, each as
    {"variant": "A -> B -> C", "activities": [...], "case_count": int, "share": float}.
    """
    log = _to_pm4py_frame(df)
    raw = pm4py.get_variants(log)
    total_cases = df["case_id"].nunique()

    rows = []
    for variant, value in raw.items():
        # pm4py returns count (int) or the list of traces depending on version
        case_count = value if isinstance(value, int) else len(value)
        activities = list(variant) if isinstance(variant, tuple) else str(variant).split(",")
        rows.append(
            {
                "variant": " -> ".join(activities),
                "activities": activities,
                "case_count": case_count,
                "share": case_count / total_cases,
            }
        )
    rows.sort(key=lambda r: (-r["case_count"], r["variant"]))
    return rows
