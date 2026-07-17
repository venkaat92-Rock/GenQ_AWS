"""L4 — Throughput, bottleneck, and rework metrics over a canonical event log."""

from __future__ import annotations

import pandas as pd

from mining.dfg import discover_dfg


def throughput(df: pd.DataFrame) -> dict:
    """Case durations (first event -> last event), in hours."""
    ts = df.copy()
    ts["timestamp"] = pd.to_datetime(ts["timestamp"])
    spans = ts.groupby("case_id")["timestamp"].agg(["min", "max"])
    hours = (spans["max"] - spans["min"]).dt.total_seconds() / 3600.0
    slowest = hours.sort_values(ascending=False).head(5)
    return {
        "cases": int(len(hours)),
        "mean_hours": float(hours.mean()),
        "median_hours": float(hours.median()),
        "p90_hours": float(hours.quantile(0.9)),
        "slowest_cases": [
            {"case_id": str(cid), "hours": float(h)} for cid, h in slowest.items()
        ],
    }


def bottlenecks(df: pd.DataFrame, top_n: int = 5) -> dict:
    """Slowest directly-follows transitions by mean waiting time."""
    graph = discover_dfg(df)
    edges = sorted(graph["edges"], key=lambda e: -e["mean_seconds"])[:top_n]
    return {
        "edges": [
            {
                "transition": f"{e['source']} -> {e['target']}",
                "mean_hours": e["mean_seconds"] / 3600.0,
                "count": e["count"],
            }
            for e in edges
        ]
    }


def rework(df: pd.DataFrame) -> dict:
    """Activities executed more than once within a case."""
    counts = df.groupby(["case_id", "activity"]).size()
    repeated = counts[counts > 1]
    by_activity = (
        repeated.reset_index(name="n").groupby("activity")["case_id"].nunique().to_dict()
    )
    rework_cases = repeated.reset_index()["case_id"].nunique()
    total_cases = df["case_id"].nunique()
    return {
        "rework_cases": int(rework_cases),
        "total_cases": int(total_cases),
        "rework_share": float(rework_cases / total_cases) if total_cases else 0.0,
        "by_activity": {a: int(c) for a, c in sorted(by_activity.items())},
    }
