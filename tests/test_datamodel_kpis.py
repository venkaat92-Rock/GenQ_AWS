"""Tests for the agent-derived data model (ERD/FKs/identifiers) and custom KPIs."""

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "DM Test"}).json()["api_key"]
    headers = {"X-API-Key": key}
    client.post("/api/sources/demo", headers=headers)
    client.post("/api/mapping/draft", headers=headers)
    client.post(
        "/api/mapping/answers",
        json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}},
        headers=headers,
    )
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=headers)
    return headers


def _rels(model):
    return {
        (r["from_table"], r["from_column"], r["to_table"], r["to_column"])
        for r in model["relationships"]
    }


def test_agent_derives_datamodel(client, auth):
    # a table with no unique column -> must be flagged like Celonis' "Missing Identifiers"
    dup_csv = b"STATUS,REGION\nOPEN,US\nOPEN,US\nCLOSED,US\n"
    client.post(
        "/api/sources/upload",
        files={"file": ("dup_status.csv", dup_csv, "text/csv")},
        headers=auth,
    )
    model = client.get("/api/datamodel", headers=auth).json()
    names = {t["name"] for t in model["tables"]}
    assert "EKKO" in names and "EVENT_LOG" in names
    rels = _rels(model)
    # joins straight out of the mapping SQL
    assert ("RBKP", "BELNR", "RSEG", "BELNR") in rels or ("RSEG", "BELNR", "RBKP", "BELNR") in rels
    # shared-key-column proposals (Celonis-FK style)
    assert any({"EKKO", "EKPO"} == {a, c} and b == d == "EBELN"
               for a, b, c, d in rels)
    # event log tied back to the case table
    assert any("EVENT_LOG" in (a, c) for a, b, c, d in rels)
    # provenance
    assert all(r["origin"] == "agent" for r in model["relationships"])
    # identifier probing found unique keys for doc tables; the all-duplicates table is flagged
    by_name = {t["name"]: t for t in model["tables"]}
    assert by_name["RBKP"]["identifier"] == "BELNR"
    assert "DUP_STATUS" in model["missing_identifiers"]


def test_datamodel_human_edits_and_rederive(client, auth):
    model = client.get("/api/datamodel", headers=auth).json()
    rels = model["relationships"]
    # WAERS is not a key hint, so this pair can only come from a human
    added = {"from_table": "EKKO", "from_column": "WAERS",
             "to_table": "RBKP", "to_column": "WAERS"}
    # SME sign-off required
    assert client.put(
        "/api/datamodel",
        json={"relationships": rels + [added], "updated_by": " "},
        headers=auth,
    ).status_code == 400

    edited = client.put(
        "/api/datamodel",
        json={"relationships": rels + [added],
              "identifiers": {"EKPO": "MATNR"},
              "updated_by": "Venkat"},
        headers=auth,
    ).json()
    assert ("EKKO", "WAERS", "RBKP", "WAERS") in _rels(edited)
    new_rel = next(r for r in edited["relationships"]
                   if r["from_column"] == "WAERS")
    assert new_rel["origin"] == "human"
    ekpo = next(t for t in edited["tables"] if t["name"] == "EKPO")
    assert ekpo["identifier"] == "MATNR" and ekpo["identifier_origin"] == "human"

    # rederive throws away the human layer
    fresh = client.post("/api/datamodel/rederive", headers=auth).json()
    assert ("EKKO", "WAERS", "RBKP", "WAERS") not in _rels(fresh)


def test_kpi_lifecycle(client, auth):
    created = client.post(
        "/api/kpis",
        json={"name": "Median cycle time",
              "definition": {"kind": "cycle_time", "agg": "median"}},
        headers=auth,
    ).json()
    assert created["unit"] == "days" and created["value"] > 0

    rework = client.post(
        "/api/kpis",
        json={"name": "Rework rate", "definition": {"kind": "case_share", "rework": True}},
        headers=auth,
    ).json()
    assert rework["value"] == 20.0  # 4 of 20 demo cases

    listed = client.get("/api/kpis", headers=auth).json()
    assert len(listed) == 2
    # KPIs respect the global filters
    filtered = client.get("/api/kpis", params={"only_rework": "true"}, headers=auth).json()
    rework_filtered = next(k for k in filtered if k["name"] == "Rework rate")
    assert rework_filtered["value"] == 100.0

    bad = client.post(
        "/api/kpis", json={"name": "x", "definition": {"kind": "nope"}}, headers=auth
    )
    assert bad.status_code == 400

    deleted = client.delete(f"/api/kpis/{created['id']}", headers=auth).json()
    assert deleted["deleted"] == created["id"]
    assert len(client.get("/api/kpis", headers=auth).json()) == 1


def test_copilot_creates_kpi(client, auth):
    proposal = client.post(
        "/api/copilot",
        json={"question": "create a KPI: average days from Goods Receipt to Payment Sent"},
        headers=auth,
    ).json()
    assert proposal["engine"] == "kpi-parser"
    kpi = proposal["kpi_proposal"]
    assert kpi["definition"]["kind"] == "cycle_time"
    assert kpi["definition"]["from_activity"] == "Goods Receipt"
    assert kpi["definition"]["to_activity"] == "Payment Sent"
    assert kpi["definition"]["agg"] == "mean"
    assert kpi["preview"]["value"] > 0

    saved = client.post(
        "/api/kpis", json={"name": kpi["name"], "definition": kpi["definition"]},
        headers=auth,
    ).json()
    assert saved["value"] == kpi["preview"]["value"]

    rework_kpi = client.post(
        "/api/copilot", json={"question": "add a kpi for rework"}, headers=auth
    ).json()
    assert rework_kpi["kpi_proposal"]["definition"] == {"kind": "case_share", "rework": True}
