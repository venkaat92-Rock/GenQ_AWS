"""Phase 1 fixture tests: DFG, conformance, throughput/bottleneck/rework on the synthetic log."""

from pathlib import Path

import pytest

from engine.db import EventLogStore
from mining.conformance import conformance
from mining.dfg import discover_dfg
from mining.metrics import bottlenecks, rework, throughput

FIXTURE = Path(__file__).parent.parent / "fixtures" / "p2p_event_log.csv"

HAPPY_PATH = ["PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent"]


@pytest.fixture(scope="module")
def event_log():
    with EventLogStore() as store:
        store.load_event_log_csv(FIXTURE)
        return store.fetch_event_log()


def test_dfg_edge_counts(event_log):
    graph = discover_dfg(event_log)
    edges = {(e["source"], e["target"]): e["count"] for e in graph["edges"]}
    # PO Created -> PO Approved happens in variants 1 (12), 2 (4), 3 (3) = 19 cases
    assert edges[("PO Created", "PO Approved")] == 19
    assert graph["start_activities"] == {"PO Created": 20}
    assert graph["end_activities"] == {"Payment Sent": 20}
    assert all(e["mean_seconds"] > 0 for e in graph["edges"])


def test_conformance_against_happy_path(event_log):
    result = conformance(event_log, [HAPPY_PATH])
    assert result["total_cases"] == 20
    assert result["conforming_cases"] == 12
    assert result["conforming_share"] == pytest.approx(0.6)
    assert 0.5 < result["token_replay_fitness"] <= 1.0
    assert len(result["deviating_cases"]) == 8


def test_throughput_metrics(event_log):
    result = throughput(event_log)
    assert result["cases"] == 20
    assert result["median_hours"] > 0
    assert result["p90_hours"] >= result["median_hours"]
    assert len(result["slowest_cases"]) == 5


def test_bottlenecks_ranked(event_log):
    result = bottlenecks(event_log, top_n=3)
    assert len(result["edges"]) == 3
    hours = [e["mean_hours"] for e in result["edges"]]
    assert hours == sorted(hours, reverse=True)


def test_rework_detects_reapproval(event_log):
    result = rework(event_log)
    # Variant 2 (4 cases) runs PO Approved twice
    assert result["rework_cases"] == 4
    assert result["rework_share"] == pytest.approx(0.2)
    assert result["by_activity"] == {"PO Approved": 4}
