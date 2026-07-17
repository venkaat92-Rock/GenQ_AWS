"""L4 — Custom KPI definitions and computation.

A KPI is a small, transparent definition (no free-form SQL) computed over the filtered
event log. Types:

  cycle_time      {agg, from_activity?, to_activity?}   -> days between two milestones
                                                           (case start/end when omitted)
  transition_wait {from_activity, to_activity, agg}     -> days on the direct hand-off
  case_share      {contains_activity? | rework: true}   -> % of cases
  case_count      {}                                    -> number of cases
  attribute_agg   {attribute, agg: sum|mean|median}     -> e.g. total PO value
"""

from __future__ import annotations

import re

import pandas as pd

AGGS = {"median": "median", "mean": "mean", "p90": lambda s: s.quantile(0.9)}


def _agg(series: pd.Series, agg: str) -> float:
    if series.empty:
        return 0.0
    fn = AGGS.get(agg, "median")
    return float(series.agg(fn))


def compute_kpi(df: pd.DataFrame, definition: dict) -> dict:
    log = df.copy()
    log["timestamp"] = pd.to_datetime(log["timestamp"])
    log = log.sort_values(["case_id", "timestamp"])
    kind = definition.get("kind")
    agg = definition.get("agg", "median")

    if kind == "cycle_time":
        frm, to = definition.get("from_activity"), definition.get("to_activity")
        starts = (
            log[log["activity"] == frm].groupby("case_id")["timestamp"].min()
            if frm else log.groupby("case_id")["timestamp"].min()
        )
        ends = (
            log[log["activity"] == to].groupby("case_id")["timestamp"].max()
            if to else log.groupby("case_id")["timestamp"].max()
        )
        spans = (ends - starts).dropna()
        days = spans.dt.total_seconds() / 86400.0
        days = days[days >= 0]
        return {"value": round(_agg(days, agg), 2), "unit": "days",
                "cases_measured": int(len(days))}

    if kind == "transition_wait":
        frm, to = definition["from_activity"], definition["to_activity"]
        waits = []
        case_ids: set = set()
        for case_id, group in log.groupby("case_id"):
            acts = group["activity"].tolist()
            times = group["timestamp"].tolist()
            for i in range(len(acts) - 1):
                if acts[i] == frm and acts[i + 1] == to:
                    waits.append((times[i + 1] - times[i]).total_seconds() / 86400.0)
                    case_ids.add(case_id)
        series = pd.Series(waits, dtype=float)
        return {"value": round(_agg(series, agg), 2), "unit": "days",
                "cases_measured": len(case_ids)}

    if kind == "case_share":
        total = log["case_id"].nunique()
        if definition.get("rework"):
            counts = log.groupby(["case_id", "activity"]).size()
            hits = counts[counts > 1].reset_index()["case_id"].nunique()
        elif definition.get("contains_activity"):
            hits = log.loc[
                log["activity"] == definition["contains_activity"], "case_id"
            ].nunique()
        else:
            hits = total
        share = hits / total if total else 0.0
        return {"value": round(share * 100, 1), "unit": "%", "cases_measured": int(total)}

    if kind == "case_count":
        return {"value": int(log["case_id"].nunique()), "unit": "cases",
                "cases_measured": int(log["case_id"].nunique())}

    if kind == "attribute_agg":
        attr = definition.get("attribute", "net_value")
        if attr not in log.columns:
            return {"value": 0.0, "unit": attr, "cases_measured": 0}
        per_case = log.groupby("case_id")[attr].first().dropna()
        agg_name = definition.get("agg", "sum")
        if agg_name not in ("sum", "mean", "median"):
            raise ValueError(
                f"unsupported agg {agg_name!r} for attribute_agg (use sum, mean, or median)"
            )
        if per_case.empty:
            return {"value": 0.0, "unit": attr, "cases_measured": 0}
        value = float(per_case.agg(agg_name))
        return {"value": round(value, 2), "unit": attr, "cases_measured": int(len(per_case))}

    raise ValueError(f"unknown KPI kind {kind!r}")


# ---- series (Celonis-style representations: bar by category, trend over time) ---------

_SERIES_LIMIT = 12  # top categories shown; the rest is dropped, not silently merged


def compute_kpi_series(df: pd.DataFrame, definition: dict) -> dict:
    """The same KPI, broken down for a chart:

      group_by: <case attribute>  -> one value per category (bar / table)
      trend: "month" | "week"     -> one value per case-start bucket (line / column)

    Categories are ranked by case volume; at most 12 are returned."""
    base = {k: v for k, v in definition.items()
            if k not in ("group_by", "trend", "viz", "name")}
    log = df.copy()
    log["timestamp"] = pd.to_datetime(log["timestamp"])

    group_by = definition.get("group_by")
    trend = definition.get("trend")
    series: list[dict] = []

    if group_by:
        if group_by not in log.columns:
            raise ValueError(f"no attribute {group_by!r} in the event log")
        per_case = log.groupby("case_id")[group_by].first()
        top = per_case.value_counts().head(_SERIES_LIMIT).index
        unit = "—"
        for value in top:
            cases = per_case[per_case == value].index
            slice_df = log[log["case_id"].isin(cases)]
            computed = compute_kpi(slice_df, base)
            unit = computed["unit"]
            series.append({"label": str(value), "value": computed["value"],
                           "cases": computed["cases_measured"]})
        series.sort(key=lambda s: s["value"], reverse=True)
        dropped = int(per_case.nunique()) - len(series)
        return {"series": series, "unit": unit,
                "note": f"top {len(series)} of {per_case.nunique()} categories"
                        if dropped > 0 else ""}

    if trend:
        freq = "W" if trend == "week" else "M"
        case_start = log.groupby("case_id")["timestamp"].min()
        buckets = case_start.dt.to_period(freq)
        unit = "—"
        for period in sorted(buckets.unique()):
            cases = buckets[buckets == period].index
            slice_df = log[log["case_id"].isin(cases)]
            computed = compute_kpi(slice_df, base)
            unit = computed["unit"]
            series.append({"label": str(period), "value": computed["value"],
                           "cases": computed["cases_measured"]})
        return {"series": series, "unit": unit, "note": ""}

    raise ValueError("a series needs either group_by or trend")


# ---- NL -> KPI definition (deterministic parser used by the co-pilot) -----------------


def parse_kpi_request(question: str, activities: list[str]) -> dict | None:
    """Turn 'create a KPI: median days from Invoice Received to Payment Sent' into a
    definition. Returns None when the request doesn't look like a KPI creation."""
    q = question.lower()
    if "kpi" not in q and "metric" not in q:
        return None

    agg = "median"
    if any(w in q for w in ("average", "mean", "avg")):
        agg = "mean"
    if "p90" in q or "90th" in q:
        agg = "p90"

    def find_activity(fragment: str) -> str | None:
        fragment = fragment.strip().lower()
        for a in activities:
            if a.lower() in fragment:
                return a
        return None

    # Explicit activity-to-activity requests always win — generic keyword branches run
    # last and only on whole words, so phrasing like "accounts payable" or "high spend"
    # can't hijack the intent (review finding 2026-07-05).
    match = re.search(r"from\s+(.+?)\s+to\s+(.+?)(?:\.|$|,)", question, re.IGNORECASE)
    if match:
        frm = find_activity(match.group(1))
        to = find_activity(match.group(2))
        if frm and to:
            direct = re.search(r"\b(wait|hand-?off|transition)\b", q) is not None
            return {
                "name": f"{agg.capitalize()} {'wait' if direct else 'days'} {frm} -> {to}",
                "kind": "transition_wait" if direct else "cycle_time",
                "from_activity": frm, "to_activity": to, "agg": agg,
            }

    contains = re.search(r"(?:contain|include|with|reach)\w*\s+['\"]?(.+?)['\"]?(?:\.|$|,)",
                         question, re.IGNORECASE)
    if contains:
        activity = find_activity(contains.group(1))
        if activity:
            return {"name": f"Share of cases with {activity}", "kind": "case_share",
                    "contains_activity": activity}

    if re.search(r"\brework\b", q):
        return {"name": "Rework rate", "kind": "case_share", "rework": True}
    if re.search(r"\b(case count|count of cases|number of cases|how many cases)\b", q):
        return {"name": "Case count", "kind": "case_count"}
    if re.search(r"\b(total|sum|spend)\b", q) and re.search(
        r"\b(net.?value|spend|po value|order value)\b", q
    ):
        return {"name": "Total PO value", "kind": "attribute_agg",
                "attribute": "net_value", "agg": "sum"}

    if re.search(r"\b(cycle|end to end|throughput|days)\b", q):
        return {"name": f"{agg.capitalize()} cycle time", "kind": "cycle_time", "agg": agg}

    return None
