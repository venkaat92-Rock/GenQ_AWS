"""L4 — Advisor: ranked improvement opportunities with an explicit value estimate.

Klarity ships an "Advisor" that ranks automation opportunities by projected ROI. The
scope-disciplined version of that idea, for an event-log product, is this module: it
reuses the numbers L4 already computes (bottlenecks, rework, conformance, variant
sprawl) and turns each into a costed, ranked opportunity.

Design rules, straight from §1.6 (everything inspectable) and the Phase 3 guardrail
(answers cite what produced them):

* No ML, no opaque scoring. Every figure is a count, a median, or a multiplication
  of the two by an assumption the caller can see and change.
* Every opportunity carries `based_on` (which measurement produced it) and
  `evidence` (the raw numbers), so a sceptical CFO can re-derive it by hand.
* Labour value and cycle-time value are NEVER mixed into one number. Recovering 10
  days of calendar time is not the same thing as saving 10 days of someone's work,
  and a tool that adds them together is lying. They are reported as separate fields
  with separate bases.
* Assumptions are defaults, not facts. `Assumptions` is dataclass-with-defaults and
  is echoed back on every response so the UI can show "based on EUR 65/h" and let an
  SME correct it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from mining.cases import case_table
from mining.dfg import discover_dfg


@dataclass
class Assumptions:
    """Caller-visible inputs to every value estimate. Defaults are deliberately
    conservative placeholders — the UI surfaces them for correction."""

    hourly_rate: float = 65.0          # fully-loaded cost of one process FTE hour
    hours_per_touch: float = 0.5       # manual effort of one extra activity execution
    currency: str = "EUR"

    def as_dict(self) -> dict:
        return asdict(self)


def _opportunity(
    oid: str, title: str, detail: str, category: str, *,
    based_on: str, evidence: dict, confidence: str,
    labour_hours: float | None = None, labour_value: float | None = None,
    cycle_days: float | None = None, affected_cases: int = 0,
) -> dict:
    return {
        "id": oid,
        "title": title,
        "detail": detail,
        "category": category,
        "confidence": confidence,
        "based_on": based_on,
        "evidence": evidence,
        "affected_cases": affected_cases,
        "labour_hours": labour_hours,
        "labour_value": labour_value,
        "cycle_days": cycle_days,
    }


def _rank_key(o: dict) -> tuple:
    """Rank by monetised labour first (it is the defensible number), then by the
    calendar time recovered, then by blast radius."""
    return (-(o["labour_value"] or 0.0), -(o["cycle_days"] or 0.0), -o["affected_cases"])


def opportunities(
    df: pd.DataFrame,
    reference: list[str] | None = None,
    assumptions: Assumptions | None = None,
) -> list[dict]:
    """Ranked improvement opportunities over a canonical event log."""
    a = assumptions or Assumptions()
    cases = case_table(df, reference)
    total_cases = int(df["case_id"].nunique())
    median_days = float(cases["duration_days"].median()) if len(cases) else 0.0
    out: list[dict] = []

    # 1. The slowest hand-off — a cycle-time opportunity, not a labour one.
    graph = discover_dfg(df)
    edges = [e for e in graph["edges"] if e["mean_seconds"] > 0]
    if edges and median_days:
        worst = max(edges, key=lambda e: e["mean_seconds"])
        wait_days = worst["mean_seconds"] / 86400.0
        share = wait_days / median_days
        # Halving the worst wait is the conventional "quick win" framing; state it as such.
        out.append(_opportunity(
            "bottleneck-worst",
            f"Compress the {worst['source']} → {worst['target']} hand-off",
            f"This single hand-off averages {wait_days:.1f} days and accounts for "
            f"{share:.0%} of the {median_days:.1f}-day median cycle. Halving it would "
            f"return about {wait_days / 2:.1f} days of calendar time per case.",
            "bottleneck",
            based_on=f"mean waiting time across {worst['count']} occurrences of this transition",
            evidence={"transition": f"{worst['source']} -> {worst['target']}",
                      "mean_wait_days": round(wait_days, 2),
                      "occurrences": worst["count"],
                      "median_cycle_days": round(median_days, 2)},
            confidence="high" if worst["count"] >= 30 else "medium",
            cycle_days=round(wait_days / 2, 2),
            affected_cases=int(worst["count"]),
        ))

    # 2. Rework — the one place where labour cost is genuinely defensible, because an
    #    extra execution of an activity really is an extra human touch.
    counts = df.groupby(["case_id", "activity"]).size()
    repeated = counts[counts > 1]
    if not repeated.empty:
        extra_by_activity = (repeated - 1).reset_index(name="extra")
        worst_act = extra_by_activity.groupby("activity")["extra"].sum().idxmax()
        act_rows = extra_by_activity[extra_by_activity["activity"] == worst_act]
        extra_touches = int(act_rows["extra"].sum())
        act_cases = int(act_rows["case_id"].nunique())
        hours = extra_touches * a.hours_per_touch
        out.append(_opportunity(
            "rework-worst-activity",
            f"Eliminate repeated “{worst_act}” steps",
            f"“{worst_act}” runs more than once in {act_cases} of {total_cases} cases, "
            f"{extra_touches} extra executions in total. At {a.hours_per_touch}h of "
            f"manual effort each that is {hours:.0f} hours of avoidable work.",
            "rework",
            based_on=f"activity repetitions counted per case across {total_cases} cases",
            evidence={"activity": worst_act, "extra_executions": extra_touches,
                      "cases_affected": act_cases, "total_cases": total_cases,
                      "hours_per_touch": a.hours_per_touch},
            confidence="high",
            labour_hours=round(hours, 1),
            labour_value=round(hours * a.hourly_rate, 2),
            affected_cases=act_cases,
        ))

    # 3. Non-conformance — only costed when deviating cases are measurably slower, and
    #    reported as cycle time, because a deviation is not inherently extra labour.
    if reference and "conformant" in cases.columns:
        dev = cases[~cases["conformant"]]
        ok = cases[cases["conformant"]]
        if len(dev) and len(ok):
            dev_days = float(dev["duration_days"].median())
            ok_days = float(ok["duration_days"].median())
            gap = dev_days - ok_days
            if gap > 0:
                out.append(_opportunity(
                    "conformance-gap",
                    "Bring off-path cases back to the agreed process",
                    f"{len(dev)} of {len(cases)} cases deviate from the reference process "
                    f"and take {dev_days:.1f} days versus {ok_days:.1f} for conformant "
                    f"ones — a {gap:.1f}-day penalty ({dev_days / ok_days:.1f}x).",
                    "conformance",
                    based_on=f"median duration of {len(dev)} deviating vs {len(ok)} conformant cases",
                    evidence={"deviating_cases": int(len(dev)), "conformant_cases": int(len(ok)),
                              "deviating_median_days": round(dev_days, 2),
                              "conformant_median_days": round(ok_days, 2)},
                    confidence="medium",
                    cycle_days=round(gap, 2),
                    affected_cases=int(len(dev)),
                ))

    # 4. Variant sprawl — a standardisation opportunity. Counted, never costed: we
    #    cannot honestly price "too many ways of doing this" without an SME.
    traces = (df.sort_values(["case_id", "timestamp"]).groupby("case_id")["activity"]
              .apply(tuple))
    variant_counts = traces.value_counts()
    singletons = int((variant_counts == 1).sum())
    if len(variant_counts) > 1 and singletons:
        top_share = float(variant_counts.iloc[0] / len(traces))
        out.append(_opportunity(
            "variant-sprawl",
            "Standardise the long tail of one-off paths",
            f"{len(variant_counts)} distinct paths exist and {singletons} of them occur "
            f"exactly once. The most common path covers only {top_share:.0%} of cases, "
            f"so there is no dominant way of working to standardise on yet.",
            "standardisation",
            based_on=f"variant frequency distribution over {len(traces)} cases",
            evidence={"distinct_variants": int(len(variant_counts)),
                      "single_occurrence_variants": singletons,
                      "top_variant_share": round(top_share, 4)},
            confidence="medium" if top_share < 0.5 else "low",
            affected_cases=singletons,
        ))

    out.sort(key=_rank_key)
    return out


def advisor_report(
    df: pd.DataFrame,
    reference: list[str] | None = None,
    assumptions: Assumptions | None = None,
) -> dict:
    a = assumptions or Assumptions()
    items = opportunities(df, reference, a)
    return {
        "opportunities": items,
        "assumptions": a.as_dict(),
        "totals": {
            "labour_hours": round(sum(o["labour_hours"] or 0.0 for o in items), 1),
            "labour_value": round(sum(o["labour_value"] or 0.0 for o in items), 2),
            "cycle_days": round(sum(o["cycle_days"] or 0.0 for o in items), 2),
            "currency": a.currency,
        },
    }
