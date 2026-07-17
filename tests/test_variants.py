"""Phase 0 fixture test: DuckDB load + PM4Py variant analysis on the synthetic P2P log."""

from pathlib import Path

import pytest

from engine.db import EventLogStore
from mining.variants import variant_analysis

FIXTURE = Path(__file__).parent.parent / "fixtures" / "p2p_event_log.csv"

HAPPY_PATH = "PO Created -> PO Approved -> Goods Receipt -> Invoice Received -> Payment Sent"


@pytest.fixture(scope="module")
def event_log():
    with EventLogStore() as store:
        store.load_event_log_csv(FIXTURE)
        return store.fetch_event_log()


def test_fixture_loads_all_cases(event_log):
    assert event_log["case_id"].nunique() == 20


def test_top_variant_is_happy_path(event_log):
    variants = variant_analysis(event_log)
    top = variants[0]
    assert top["variant"] == HAPPY_PATH
    assert top["case_count"] == 12
    assert top["share"] == pytest.approx(0.6)


def test_variant_counts_cover_all_cases(event_log):
    variants = variant_analysis(event_log)
    assert len(variants) == 4
    assert sum(v["case_count"] for v in variants) == 20
