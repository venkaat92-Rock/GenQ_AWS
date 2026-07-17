"""Phase 2 tests — the §4 first milestone:

Given a fixture SAP P2P schema + sample rows, the agent emits a fully-explained mapping
spec, raises the case-granularity ambiguity as a blocking open question, refuses to
materialize before human confirmation, and — once confirmed — materializes an event log
identical to the hand-built reference log.
"""

from pathlib import Path

import pytest

from engine.db import EventLogStore
from mining.variants import variant_analysis
from modelling.agent import ModellingAgent
from modelling.materialize import materialize
from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE, PO_LINE_CASE
from modelling.spec import MappingNotConfirmed, MappingSpec

SAP_DIR = Path(__file__).parent.parent / "fixtures" / "sap"
REFERENCE_LOG = Path(__file__).parent.parent / "fixtures" / "p2p_event_log.csv"

EXPECTED_ACTIVITIES = {
    "PO Created", "PO Approved", "PO Changed", "Goods Receipt",
    "Invoice Received", "Payment Sent",
}


@pytest.fixture()
def staged_store():
    with EventLogStore() as store:
        for csv in SAP_DIR.glob("*.csv"):
            store.stage_csv(csv.stem, csv)
        yield store


def test_agent_drafts_fully_explained_spec(staged_store):
    spec = ModellingAgent(staged_store).draft()
    assert spec.status == "draft"
    assert {a.name for a in spec.activities} == EXPECTED_ACTIVITIES
    # Every semantic choice must be justified and confidence-flagged (§4)
    for a in spec.activities:
        assert a.rationale, f"{a.name} has no rationale"
        assert a.confidence in ("high", "medium", "low")
        assert a.sql.lower().startswith("select")
    # The known-risky choices are NOT high confidence
    by_name = {a.name: a for a in spec.activities}
    assert by_name["PO Approved"].confidence == "medium"
    assert {a.name for a in spec.attributes} == {"vendor", "net_value"}


def test_case_granularity_is_escalated_not_guessed(staged_store):
    spec = ModellingAgent(staged_store).draft()
    qids = [q.id for q in spec.open_questions]
    assert CASE_GRANULARITY_QID in qids
    question = next(q for q in spec.open_questions if q.id == CASE_GRANULARITY_QID)
    assert question.answer is None
    assert len(question.options) == 2


def test_materialization_blocked_until_confirmed(staged_store):
    spec = ModellingAgent(staged_store).draft()
    with pytest.raises(MappingNotConfirmed):
        materialize(staged_store, spec)  # still a draft
    with pytest.raises(MappingNotConfirmed):
        spec.confirm("Venkat")  # open question unanswered
    spec.answer_question(CASE_GRANULARITY_QID, PO_HEADER_CASE)
    with pytest.raises(MappingNotConfirmed):
        spec.confirm("")  # a named reviewer is required


def test_confirmed_spec_reproduces_reference_log(staged_store):
    agent = ModellingAgent(staged_store)
    spec = agent.draft()
    spec.answer_question(CASE_GRANULARITY_QID, PO_HEADER_CASE)
    spec = agent.redraft_with_answers(spec)
    spec.confirm("Venkat")

    result = materialize(staged_store, spec)
    assert result == {
        "events": 107,
        "cases": 20,
        "activities": sorted(EXPECTED_ACTIVITIES),
    }
    assert spec.generated_sql  # audit trail (§1.6)

    # The materialized log must match the hand-built reference log event-for-event
    materialized = staged_store.fetch_event_log()
    with EventLogStore() as ref_store:
        ref_store.load_event_log_csv(REFERENCE_LOG)
        reference = ref_store.fetch_event_log()
    # The reference log carries a cosmetic "PO-" display prefix on case ids; real EBELNs don't
    m = materialized.assign(
        case_id=materialized["case_id"].astype(str),
        timestamp=materialized["timestamp"].astype("datetime64[ns]"),
    )
    r = reference.assign(
        case_id=reference["case_id"].astype(str).str.removeprefix("PO-"),
        timestamp=reference["timestamp"].astype("datetime64[ns]"),
    )
    assert m.reset_index(drop=True).equals(r.reset_index(drop=True))

    # And therefore the same variant picture
    top = variant_analysis(materialized)[0]
    assert top["case_count"] == 12


def test_line_granularity_redraft_materializes(staged_store):
    agent = ModellingAgent(staged_store)
    spec = agent.draft()
    spec.answer_question(CASE_GRANULARITY_QID, PO_LINE_CASE)
    spec = agent.redraft_with_answers(spec)
    assert spec.version == 2
    spec.confirm("Venkat")
    result = materialize(staged_store, spec)
    # 42 EKPO lines -> 42 cases; header events fan out to every line
    assert result["cases"] == 42


def test_spec_yaml_roundtrip(staged_store):
    spec = ModellingAgent(staged_store).draft()
    restored = MappingSpec.from_yaml(spec.to_yaml())
    assert restored.to_dict() == spec.to_dict()
