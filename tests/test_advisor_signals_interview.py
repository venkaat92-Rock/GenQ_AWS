"""Fixture tests for the three "Improve" features: Advisor, Signals, Interviewer.

The load-bearing assertions here are the honesty ones, not the arithmetic ones:
- the Advisor must never merge labour value into cycle-time value;
- every opportunity must cite what produced it (§1.6 / Phase 3 guardrail);
- Signals must stay silent below the threshold, and must flag STRUCTURAL drift
  separately because that means the mapping, not the process, likely changed;
- the Interviewer must refuse an answer the model invented outside the offered
  options (§1.1 — escalate ambiguity, never guess silently).
"""

from pathlib import Path

import pandas as pd
import pytest

from engine.db import EventLogStore
from mining.advisor import Assumptions, advisor_report, opportunities
from mining.signals import detect_signals, snapshot
from modelling.interviewer import interview_turn, opening
from modelling.spec import CaseIdMapping, MappingSpec, OpenQuestion

FIXTURE = Path(__file__).parent.parent / "fixtures" / "p2p_event_log.csv"
HAPPY_PATH = ["PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent"]


@pytest.fixture(scope="module")
def event_log():
    with EventLogStore() as store:
        store.load_event_log_csv(FIXTURE)
        return store.fetch_event_log()


# ---- Advisor -------------------------------------------------------------------------


def test_every_opportunity_cites_its_basis(event_log):
    for o in opportunities(event_log, HAPPY_PATH):
        assert o["based_on"], f"{o['id']} has no stated basis"
        assert o["evidence"], f"{o['id']} ships no raw numbers"
        assert o["confidence"] in {"high", "medium", "low"}


def test_labour_and_cycle_time_are_never_merged(event_log):
    """Recovering calendar time is not the same as saving work. An opportunity may
    carry one, the other, or neither — but a single number meaning both is a lie."""
    for o in opportunities(event_log, HAPPY_PATH):
        if o["labour_value"] is not None:
            assert o["labour_hours"] is not None
        assert not (o["labour_value"] is not None and o["cycle_days"] is not None), (
            f"{o['id']} reports labour and cycle time as one opportunity"
        )


def test_rework_hours_follow_the_stated_assumptions(event_log):
    cheap = advisor_report(event_log, HAPPY_PATH, Assumptions(hourly_rate=10, hours_per_touch=1))
    dear = advisor_report(event_log, HAPPY_PATH, Assumptions(hourly_rate=100, hours_per_touch=1))
    assert dear["totals"]["labour_value"] == pytest.approx(cheap["totals"]["labour_value"] * 10)
    # and the assumptions are echoed back so the UI can show what the number rests on
    assert dear["assumptions"]["hourly_rate"] == 100


def test_opportunities_are_ranked_by_value(event_log):
    items = opportunities(event_log, HAPPY_PATH)
    values = [(o["labour_value"] or 0.0) for o in items]
    assert values == sorted(values, reverse=True)


# ---- Signals -------------------------------------------------------------------------


def test_no_baseline_means_no_signals(event_log):
    assert detect_signals(None, snapshot(event_log, HAPPY_PATH)) == []


def test_identical_snapshots_are_silent(event_log):
    snap = snapshot(event_log, HAPPY_PATH)
    assert detect_signals(snap, snap) == []


def test_small_movement_stays_below_the_threshold(event_log):
    base = snapshot(event_log, HAPPY_PATH)
    nudged = {**base, "median_cycle_days": base["median_cycle_days"] * 1.03}
    assert detect_signals(base, nudged) == []


def test_worsening_metric_is_flagged_with_direction_and_severity(event_log):
    base = snapshot(event_log, HAPPY_PATH)
    worse = {**base, "median_cycle_days": base["median_cycle_days"] * 1.4}
    found = [s for s in detect_signals(base, worse) if s["metric"] == "median_cycle_days"]
    assert len(found) == 1
    assert found[0]["direction"] == "up"
    assert found[0]["severity"] == "critical"      # >= 25% the wrong way
    assert found[0]["based_on"]


def test_improvement_is_reported_but_not_alarming(event_log):
    base = snapshot(event_log, HAPPY_PATH)
    better = {**base, "median_cycle_days": base["median_cycle_days"] * 0.5}
    found = [s for s in detect_signals(base, better) if s["metric"] == "median_cycle_days"]
    assert found and found[0]["severity"] == "info" and found[0]["direction"] == "down"


def test_new_activity_raises_a_structural_signal(event_log):
    base = snapshot(event_log, HAPPY_PATH)
    changed = {**base, "activities": base["activities"] + ["Invoice Blocked"]}
    structural = [s for s in detect_signals(base, changed) if s["kind"] == "structural"]
    assert len(structural) == 1
    # it must point the reviewer at the mapping, not at the business process
    assert "mapping" in structural[0]["detail"].lower()
    assert structural[0]["severity"] == "warn"


# ---- Interviewer ---------------------------------------------------------------------


def _spec() -> MappingSpec:
    return MappingSpec(
        process="procure_to_pay",
        case_id=CaseIdMapping(source="EKKO.EBELN", description="purchase order header"),
        open_questions=[OpenQuestion(
            id="case_granularity",
            question="Is a purchase order or a PO line the right case?",
            why_it_matters="It decides what one row in every dashboard means.",
            options=["Purchase order (header)", "PO line item"],
        )],
    )


class FakeLLM:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.payload


def test_opening_needs_no_model_and_explains_why_it_matters():
    turn = opening(_spec(), "case_granularity")
    assert "right case" in turn.reply
    assert "decides what one row" in turn.reply
    assert turn.proposed_answer is None          # it only ASKS
    assert turn.options == ["Purchase order (header)", "PO line item"]


def test_unknown_question_id_is_rejected():
    with pytest.raises(KeyError):
        opening(_spec(), "no-such-question")


def test_without_a_model_free_text_is_refused_but_an_exact_option_passes():
    loose = interview_turn(_spec(), "case_granularity", [], "it depends on the vendor", None)
    assert loose.proposed_answer is None
    exact = interview_turn(_spec(), "case_granularity", [], "PO line item", None)
    assert exact.proposed_answer == "PO line item"


def test_model_answer_is_mapped_to_an_offered_option():
    llm = FakeLLM('{"reply": "Got it.", "proposed_answer": "PO line item",'
                  ' "rationale": "They track approvals per line."}')
    turn = interview_turn(_spec(), "case_granularity", [], "we approve each line separately", llm)
    assert turn.proposed_answer == "PO line item"
    assert turn.rationale
    assert llm.calls == 1


def test_an_invented_option_is_refused():
    """The guard that makes this safe: a model that answers outside the offered set
    must yield NO proposal rather than a confident wrong one."""
    llm = FakeLLM('{"reply": "Sure.", "proposed_answer": "Per delivery note", "rationale": "x"}')
    turn = interview_turn(_spec(), "case_granularity", [], "by delivery", llm)
    assert turn.proposed_answer is None
    assert turn.reply == "Sure."


def test_prose_wrapped_json_is_still_parsed():
    llm = FakeLLM('```json\n{"reply": "Noted.", "proposed_answer": "PO line item",'
                  ' "rationale": null}\n```')
    assert interview_turn(_spec(), "case_granularity", [], "lines", llm).proposed_answer == "PO line item"


def test_unparseable_model_output_degrades_to_a_question():
    turn = interview_turn(_spec(), "case_granularity", [], "lines", FakeLLM("I think lines."))
    assert turn.proposed_answer is None
    assert turn.reply


# ---- end-to-end over the HTTP surface ------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


@pytest.fixture()
def live(client):
    """A workspace with a confirmed mapping and a materialized log."""
    from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE

    auth = {"X-API-Key": client.post("/api/workspaces", json={"name": "Improve Co"}).json()["api_key"]}
    client.post("/api/sources/demo", headers=auth)
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    confirmed = client.post("/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=auth)
    assert confirmed.status_code == 200, confirmed.text
    return client, auth


def test_interview_endpoint_opens_without_a_model(client):
    from modelling.priors import CASE_GRANULARITY_QID

    auth = {"X-API-Key": client.post("/api/workspaces", json={"name": "IV Co"}).json()["api_key"]}
    client.post("/api/sources/demo", headers=auth)
    client.post("/api/mapping/draft", headers=auth)

    opened = client.post("/api/mapping/interview",
                         json={"question_id": CASE_GRANULARITY_QID}, headers=auth)
    assert opened.status_code == 200
    body = opened.json()
    assert body["reply"] and body["proposed_answer"] is None
    assert body["ai_available"] is False          # no key configured in this test env
    assert body["options"]

    bad = client.post("/api/mapping/interview", json={"question_id": "nope"}, headers=auth)
    assert bad.status_code == 400


def test_interview_is_closed_once_the_mapping_is_confirmed(live):
    from modelling.priors import CASE_GRANULARITY_QID

    client, auth = live
    refused = client.post("/api/mapping/interview",
                          json={"question_id": CASE_GRANULARITY_QID}, headers=auth)
    assert refused.status_code == 409


def test_advisor_endpoint_returns_ranked_costed_opportunities(live):
    client, auth = live
    body = client.get("/api/analysis/advisor?hourly_rate=80&hours_per_touch=1", headers=auth).json()
    assert body["assumptions"]["hourly_rate"] == 80
    assert "labour_value" in body["totals"] and "cycle_days" in body["totals"]
    for o in body["opportunities"]:
        assert o["based_on"] and o["evidence"]


def test_signals_need_a_baseline_then_report_against_it(live):
    client, auth = live

    first = client.get("/api/signals", headers=auth).json()
    assert first["baseline"] is None and first["signals"] == []
    assert first["current"]["cases"] > 0

    assert client.post("/api/signals/baseline", json={"captured_by": "  "},
                       headers=auth).status_code == 400

    saved = client.post("/api/signals/baseline", json={"captured_by": "Venkat"}, headers=auth)
    assert saved.status_code == 200
    assert saved.json()["baseline"]["captured_by"] == "Venkat"

    # Nothing has changed since the baseline was taken a moment ago.
    again = client.get("/api/signals", headers=auth).json()
    assert again["baseline"]["captured_by"] == "Venkat"
    assert again["signals"] == []
