"""L5 — Natural-language self-serve over the materialized event log.

Guardrail (CLAUDE.md Phase 3): every answer cites which analysis, activities, and filters
produced it — traceable, not magic. Questions route to the L4 mining functions through a
fixed intent table; with an LLM configured, the LLM picks the intent and filters, but it
can only choose from the same auditable intents — it never writes free-form SQL.
"""

from __future__ import annotations

import json

import pandas as pd

from mining.conformance import conformance
from mining.dfg import discover_dfg
from mining.metrics import bottlenecks, rework, throughput
from mining.variants import variant_analysis
from modelling.llm import LLMProvider

INTENTS = ("variants", "bottlenecks", "rework", "throughput", "conformance", "dfg")

_KEYWORDS = {
    "variants": ("variant", "path", "flow", "how does", "process look", "sequence", "happy"),
    "bottlenecks": ("bottleneck", "slow", "waiting", "wait", "stuck", "delay"),
    "rework": ("rework", "repeat", "again", "loop", "twice", "re-approv"),
    "throughput": ("how long", "duration", "cycle time", "throughput", "lead time", "days", "take"),
    "conformance": ("conform", "deviat", "compliance", "follow the process", "off-track", "skip"),
    "dfg": ("graph", "map", "transitions", "follows"),
}

_LLM_ROUTER_PROMPT = """You route a business user's question about their procure-to-pay
process to one analysis intent. Intents: variants, bottlenecks, rework, throughput,
conformance, dfg. If the question mentions a vendor name, extract it as a filter.
Respond ONLY with JSON: {"intent": "...", "vendor": null or "name"}"""


def _route_by_keywords(question: str) -> str:
    q = question.lower()
    for intent, words in _KEYWORDS.items():
        if any(w in q for w in words):
            return intent
    return "variants"


def _route(question: str, vendors: list[str], llm: LLMProvider | None) -> tuple[str, str | None]:
    q = question.lower()
    vendor = next((v for v in vendors if v.lower() in q), None)
    if llm is not None:
        try:
            raw = llm.complete(_LLM_ROUTER_PROMPT, question).strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```")
            parsed = json.loads(raw)
            if parsed.get("intent") in INTENTS:
                llm_vendor = parsed.get("vendor")
                matched = next(
                    (v for v in vendors if llm_vendor and v.lower() == str(llm_vendor).lower()),
                    vendor,
                )
                return parsed["intent"], matched
        except Exception:
            pass  # deterministic routing still answers
    return _route_by_keywords(question), vendor


def answer_question(
    question: str,
    df: pd.DataFrame,
    activity_order: list[str],
    llm: LLMProvider | None = None,
) -> dict:
    party = next((c for c in ("vendor", "customer") if c in df.columns), None)
    vendors = sorted(df[party].dropna().unique().tolist()) if party else []
    intent, vendor = _route(question, vendors, llm)

    filters = []
    scoped = df
    if vendor and party:
        case_ids = df.loc[df[party] == vendor, "case_id"].unique()
        scoped = df[df["case_id"].isin(case_ids)]
        filters.append(f"{party} = {vendor}")

    activities_used = sorted(scoped["activity"].unique().tolist())
    n_cases = scoped["case_id"].nunique()

    if intent == "variants":
        data = variant_analysis(scoped)[:10]
        top = data[0]
        text = (
            f"Across {n_cases} cases there are {len(variant_analysis(scoped))} distinct paths. "
            f"The most common ({top['case_count']} cases, {top['share']:.0%}) is: {top['variant']}."
        )
    elif intent == "bottlenecks":
        data = bottlenecks(scoped)["edges"]
        worst = data[0]
        text = (
            f"The slowest transition is {worst['transition']} at an average of "
            f"{worst['mean_hours']:.1f} hours ({worst['mean_hours'] / 24:.1f} days), "
            f"observed {worst['count']} times."
        )
    elif intent == "rework":
        r = rework(scoped)
        data = r
        if r["rework_cases"]:
            acts = ", ".join(f"{a} ({c} cases)" for a, c in r["by_activity"].items())
            text = (
                f"{r['rework_cases']} of {r['total_cases']} cases ({r['rework_share']:.0%}) "
                f"contain rework — repeated activities: {acts}."
            )
        else:
            text = f"No rework detected across {r['total_cases']} cases."
    elif intent == "throughput":
        t = throughput(scoped)
        data = t
        text = (
            f"Median end-to-end time is {t['median_hours'] / 24:.1f} days "
            f"(mean {t['mean_hours'] / 24:.1f}, p90 {t['p90_hours'] / 24:.1f}) "
            f"over {t['cases']} cases."
        )
    elif intent == "conformance":
        all_variants = variant_analysis(scoped)
        reference = all_variants[0]["activities"]
        c = conformance(scoped, [reference])
        data = c
        text = (
            f"Taking the most frequent path as reference, {c['conforming_cases']} of "
            f"{c['total_cases']} cases ({c['conforming_share']:.0%}) conform exactly; "
            f"token-replay fitness is {c['token_replay_fitness']:.2f}."
        )
        filters.append("reference = most frequent variant")
    else:  # dfg
        g = discover_dfg(scoped)
        data = g
        busiest = g["edges"][0]
        text = (
            f"The process map has {len(g['nodes'])} activities and {len(g['edges'])} "
            f"transitions; the busiest is {busiest['source']} -> {busiest['target']} "
            f"({busiest['count']} times)."
        )

    return {
        "question": question,
        "answer": text,
        "intent": intent,
        "data": data,
        "citations": {
            "analysis": intent,
            "cases_considered": int(n_cases),
            "activities_used": activities_used,
            "filters": filters or ["none — full event log"],
            "mapped_activities": activity_order,
        },
    }
