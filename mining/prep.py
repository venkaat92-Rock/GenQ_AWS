"""Shared event-log preparation for the mining layer (L4)."""

from __future__ import annotations

import pandas as pd
import pm4py


def to_pm4py_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Convert a canonical (case_id, activity, timestamp) frame into PM4Py format."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return pm4py.format_dataframe(
        df, case_id="case_id", activity_key="activity", timestamp_key="timestamp"
    )
