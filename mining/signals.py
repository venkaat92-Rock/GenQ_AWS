"""L4 — Signals: process drift detection by comparing a log against a saved baseline.

Klarity's "Signals" watch for process drift and feed findings back into discovery.
The event-log equivalent is cheap to build honestly: take a snapshot of the metrics
L4 already computes, store it, and on each later run report what moved materially.

That turns GenQ from a project-based exercise ("we mined P2P in March") into a
continuous one, which is the genuinely valuable half of the idea — without a desktop
agent, screen recording, or any new data substrate.

Discipline applied here:

* A signal fires only when a metric moves beyond a stated threshold. Everything else
  is silence. A monitor that reports noise gets muted, and a muted monitor is worse
  than none.
* Direction is interpreted, not just reported: for each metric we know which way is
  bad, so "conformance fell 12 points" is `warn` while a rise is `info`.
* Structural drift (an activity that appeared or vanished) is reported separately
  from numeric drift. A new activity in the log usually means the source system
  changed, which is the one case where the MAPPING, not the process, needs review —
  so the signal says so explicitly (§1.1: silent semantic errors are the real risk).
* Every signal cites the two numbers it compared. No trend lines, no forecasting.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from mining.cases import case_table
from mining.dfg import discover_dfg

# Fraction a metric must move before it is worth telling a human about.
DEFAULT_THRESHOLD = 0.10

# metric -> (label, which direction is bad, unit)
_METRICS: dict[str, tuple[str, str, str]] = {
    "median_cycle_days": ("Median cycle time", "up", "days"),
    "rework_share": ("Rework share", "up", "share"),
    "conforming_share": ("Conformance", "down", "share"),
    "distinct_variants": ("Distinct variants", "up", "count"),
    "worst_wait_days": ("Worst hand-off wait", "up", "days"),
    "cases": ("Case volume", "none", "count"),
}


def snapshot(df: pd.DataFrame, reference: list[str] | None = None) -> dict:
    """Capture the handful of numbers worth watching over time."""
    cases = case_table(df, reference)
    traces = (df.sort_values(["case_id", "timestamp"]).groupby("case_id")["activity"]
              .apply(tuple))
    counts = df.groupby(["case_id", "activity"]).size()
    rework_cases = counts[counts > 1].reset_index()["case_id"].nunique()
    total = int(df["case_id"].nunique())

    edges = [e for e in discover_dfg(df)["edges"] if e["mean_seconds"] > 0]
    worst_wait = max((e["mean_seconds"] for e in edges), default=0.0) / 86400.0

    conforming = None
    if reference is not None and "conformant" in cases.columns and len(cases):
        conforming = float(cases["conformant"].mean())

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": total,
        "events": int(len(df)),
        "median_cycle_days": float(cases["duration_days"].median()) if len(cases) else 0.0,
        "rework_share": float(rework_cases / total) if total else 0.0,
        "conforming_share": conforming,
        "distinct_variants": int(traces.nunique()),
        "worst_wait_days": float(worst_wait),
        "activities": sorted(df["activity"].astype(str).unique().tolist()),
    }


def _severity(metric: str, change: float) -> str:
    _, bad_direction, _ = _METRICS[metric]
    if bad_direction == "none":
        return "info"
    worsened = (change > 0 and bad_direction == "up") or (change < 0 and bad_direction == "down")
    if not worsened:
        return "info"
    return "critical" if abs(change) >= 0.25 else "warn"


def _fmt(value: float, unit: str) -> str:
    if unit == "share":
        return f"{value:.0%}"
    if unit == "days":
        return f"{value:.1f} days"
    return f"{value:,.0f}"


def detect_signals(
    baseline: dict | None,
    current: dict,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Material differences between a saved baseline snapshot and the current one."""
    if not baseline:
        return []
    signals: list[dict] = []

    for metric, (label, _, unit) in _METRICS.items():
        before, after = baseline.get(metric), current.get(metric)
        if before is None or after is None:
            continue
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        if before == 0:
            continue
        change = (after - before) / abs(before)
        if abs(change) < threshold:
            continue
        direction = "up" if change > 0 else "down"
        signals.append({
            "id": f"drift-{metric}",
            "kind": "numeric",
            "title": f"{label} moved {direction} {abs(change):.0%}",
            "detail": f"{label} went from {_fmt(before, unit)} to {_fmt(after, unit)} "
                      f"since the baseline was captured.",
            "metric": metric,
            "baseline_value": before,
            "current_value": after,
            "change": round(change, 4),
            "direction": direction,
            "severity": _severity(metric, change),
            "based_on": f"baseline captured {baseline.get('captured_at', 'unknown')} "
                        f"vs current log of {current.get('cases', 0)} cases",
        })

    # Structural drift — the mapping, not the process, is the likely culprit.
    before_acts = set(baseline.get("activities") or [])
    after_acts = set(current.get("activities") or [])
    for name, acts, verb in (("appeared", after_acts - before_acts, "appeared in"),
                             ("disappeared", before_acts - after_acts, "vanished from")):
        if acts:
            listed = ", ".join(f"“{a}”" for a in sorted(acts)[:5])
            more = f" (+{len(acts) - 5} more)" if len(acts) > 5 else ""
            signals.append({
                "id": f"structure-{name}",
                "kind": "structural",
                "title": f"{len(acts)} activity/activities {name} since the baseline",
                "detail": f"{listed}{more} {verb} the event log. A change in the set of "
                          f"activities usually means the source system or the mapping "
                          f"changed — review the mapping before trusting the trend.",
                "metric": "activities",
                "baseline_value": sorted(before_acts),
                "current_value": sorted(after_acts),
                "change": None,
                "direction": "up" if name == "appeared" else "down",
                "severity": "warn",
                "based_on": "set difference of distinct activity names between snapshots",
            })

    order = {"critical": 0, "warn": 1, "info": 2}
    signals.sort(key=lambda s: (order.get(s["severity"], 3), -abs(s.get("change") or 0)))
    return signals
