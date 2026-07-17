"""L4 — Root-cause-lite insights: automatic, explainable comparisons over the event log.

The Celonis/Signavio equivalent is the root-cause / correlation miner. Scope-disciplined
version: a fixed set of transparent comparisons (attribute vs duration, rework impact,
conformance impact, bottleneck concentration) narrated in business language, each with
its basis stated. No opaque ML — every number is a group median or share (§1.6).
"""

from __future__ import annotations

import pandas as pd

from mining.cases import case_table
from mining.dfg import discover_dfg


def generate_insights(df: pd.DataFrame, reference: list[str] | None = None) -> list[dict]:
    cases = case_table(df, reference)
    insights: list[dict] = []

    def add(title: str, detail: str, severity: str, based_on: str) -> None:
        insights.append(
            {"title": title, "detail": detail, "severity": severity, "based_on": based_on}
        )

    # 1. Bottleneck concentration
    graph = discover_dfg(df)
    edges = [e for e in graph["edges"] if e["mean_seconds"] > 0]
    if edges:
        worst = max(edges, key=lambda e: e["mean_seconds"])
        total_median = cases["duration_days"].median()
        worst_days = worst["mean_seconds"] / 86400.0
        share = worst_days / total_median if total_median else 0
        add(
            "Biggest bottleneck",
            f"The hand-off {worst['source']} -> {worst['target']} averages "
            f"{worst_days:.1f} days — about {share:.0%} of the median cycle time "
            f"({total_median:.1f} days).",
            "warn" if share > 0.3 else "info",
            f"mean waiting time over {worst['count']} occurrences",
        )

    # 2. Party (vendor/customer) comparison
    party = next((c for c in ("vendor", "customer") if c in cases.columns), None)
    if party and cases[party].nunique() > 1:
        by_party = cases.groupby(party).agg(
            median_days=("duration_days", "median"),
            n=("case_id", "count"),
            rework_share=("rework", "mean"),
        )
        slow = by_party["median_days"].idxmax()
        fast = by_party["median_days"].idxmin()
        if slow != fast:
            ratio = (
                by_party.loc[slow, "median_days"] / by_party.loc[fast, "median_days"]
                if by_party.loc[fast, "median_days"]
                else 0
            )
            add(
                f"{party.capitalize()} with the slowest cycle",
                f"{slow} cases take {by_party.loc[slow, 'median_days']:.1f} days median "
                f"({ratio:.1f}x {fast} at {by_party.loc[fast, 'median_days']:.1f} days).",
                "warn" if ratio >= 1.5 else "info",
                f"median duration per {party}, {int(by_party['n'].sum())} cases",
            )
        worst_rework = by_party["rework_share"].idxmax()
        if by_party.loc[worst_rework, "rework_share"] > 0:
            add(
                f"Rework concentrates on one {party}",
                f"{worst_rework}: {by_party.loc[worst_rework, 'rework_share']:.0%} of its "
                f"{int(by_party.loc[worst_rework, 'n'])} cases contain repeated activities.",
                "warn" if by_party.loc[worst_rework, "rework_share"] >= 0.5 else "info",
                f"share of cases with a repeated activity, per {party}",
            )

    # 3. Rework impact on cycle time
    if cases["rework"].any() and (~cases["rework"]).any():
        with_r = cases.loc[cases["rework"], "duration_days"].median()
        without = cases.loc[~cases["rework"], "duration_days"].median()
        if without:
            add(
                "Cost of rework",
                f"Cases with rework take {with_r:.1f} days median vs {without:.1f} days "
                f"without — {with_r / without:.1f}x longer.",
                "warn" if with_r / without >= 1.3 else "info",
                f"{int(cases['rework'].sum())} rework vs "
                f"{int((~cases['rework']).sum())} clean cases",
            )

    # 4. Conformance impact
    if "conformant" in cases.columns and cases["conformant"].any() and (~cases["conformant"]).any():
        conf = cases.loc[cases["conformant"], "duration_days"].median()
        dev = cases.loc[~cases["conformant"], "duration_days"].median()
        add(
            "Deviations are slower" if dev > conf else "Deviations are not slower",
            f"Cases off the reference path take {dev:.1f} days median vs {conf:.1f} days "
            f"on the reference path.",
            "warn" if conf and dev / conf >= 1.3 else "info",
            f"{int((~cases['conformant']).sum())} deviating vs "
            f"{int(cases['conformant'].sum())} conforming cases",
        )

    return insights
