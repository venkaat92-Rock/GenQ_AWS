"""L4 — Directly-follows graph (frequency + performance), wrapping PM4Py."""

from __future__ import annotations

import pandas as pd
import pm4py

from mining.prep import to_pm4py_frame


def _edge_mean_seconds(value) -> float:
    # pm4py returns either a float (mean) or a dict of stats depending on version
    if isinstance(value, dict):
        return float(value.get("mean", 0.0))
    return float(value)


def discover_dfg(df: pd.DataFrame) -> dict:
    """Return a JSON-friendly DFG: nodes with case counts, edges with frequency and mean duration."""
    log = to_pm4py_frame(df)
    freq_dfg, start_acts, end_acts = pm4py.discover_dfg(log)
    perf_dfg, _, _ = pm4py.discover_performance_dfg(log)

    activity_counts = df.groupby("activity")["case_id"].count().to_dict()
    edges = []
    for (src, tgt), count in freq_dfg.items():
        edges.append(
            {
                "source": src,
                "target": tgt,
                "count": int(count),
                "mean_seconds": _edge_mean_seconds(perf_dfg.get((src, tgt), 0.0)),
            }
        )
    edges.sort(key=lambda e: -e["count"])

    return {
        "nodes": [
            {"activity": a, "event_count": int(c)} for a, c in sorted(activity_counts.items())
        ],
        "edges": edges,
        "start_activities": {a: int(c) for a, c in start_acts.items()},
        "end_activities": {a: int(c) for a, c in end_acts.items()},
    }
