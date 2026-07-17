"""L4 — Conformance checking vs. a reference model, wrapping PM4Py.

The reference model is expressed as one or more allowed activity sequences (business-readable,
per §1.1 — no opaque models). We build a Petri net from them with the inductive miner and run
token-based replay; we also report the stricter exact-variant-match share.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pm4py

from mining.prep import to_pm4py_frame


def _reference_log(reference_variants: list[list[str]]) -> pd.DataFrame:
    rows = []
    base = datetime(2000, 1, 1)
    for i, variant in enumerate(reference_variants):
        for j, activity in enumerate(variant):
            rows.append(
                {"case_id": f"ref-{i}", "activity": activity, "timestamp": base + timedelta(hours=j)}
            )
    return pd.DataFrame(rows)


def conformance(df: pd.DataFrame, reference_variants: list[list[str]]) -> dict:
    """Token-replay fitness + exact-variant conformance of `df` against allowed sequences."""
    log = to_pm4py_frame(df)
    ref_log = to_pm4py_frame(_reference_log(reference_variants))

    net, im, fm = pm4py.discover_petri_net_inductive(ref_log)
    fitness = pm4py.fitness_token_based_replay(log, net, im, fm)
    avg_fitness = float(
        fitness.get("average_trace_fitness", fitness.get("log_fitness", 0.0))
    )

    # Exact-variant view: which cases follow an allowed sequence verbatim
    allowed = {tuple(v) for v in reference_variants}
    traces = (
        df.sort_values(["case_id", "timestamp"])
        .groupby("case_id")["activity"]
        .apply(tuple)
    )
    conforming = traces.isin(allowed)
    deviating_cases = sorted(str(c) for c in traces[~conforming].index.tolist())

    return {
        "token_replay_fitness": avg_fitness,
        "conforming_cases": int(conforming.sum()),
        "total_cases": int(len(traces)),
        "conforming_share": float(conforming.mean()),
        "deviating_cases": deviating_cases,
        "reference_variants": [" -> ".join(v) for v in reference_variants],
    }
