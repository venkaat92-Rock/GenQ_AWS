"""Tests for the Celonis/Signavio-parity features: global filters, case explorer,
SME reference process, and insights."""

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE

HAPPY = ["PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Feature Test"}).json()["api_key"]
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


def test_facets(client, auth):
    facets = client.get("/api/analysis/facets", headers=auth).json()
    assert len(facets["vendors"]) == 4
    assert len(facets["activities"]) == 6
    assert facets["date_min"] <= facets["date_max"]
    assert facets["duration_max_days"] > 0


def test_global_filters(client, auth):
    base = client.get("/api/analysis/summary", headers=auth).json()
    assert base["cases"] == 20 and base["filters_applied"] == []

    by_vendor = client.get(
        "/api/analysis/summary", params={"vendors": "Acme Industrial"}, headers=auth
    ).json()
    assert 0 < by_vendor["cases"] < 20
    assert any("Acme Industrial" in x for x in by_vendor["filters_applied"])

    rework_only = client.get(
        "/api/analysis/summary", params={"only_rework": "true"}, headers=auth
    ).json()
    assert rework_only["cases"] == 4  # the 4 re-approval cases

    with_change = client.get(
        "/api/analysis/cases", params={"include_activity": "PO Changed"}, headers=auth
    ).json()
    assert len(with_change["cases"]) == 4

    skip_approval = client.get(
        "/api/analysis/cases", params={"exclude_activity": "PO Approved"}, headers=auth
    ).json()
    assert len(skip_approval["cases"]) == 1  # the skip-approval variant

    assert (
        client.get("/api/analysis/summary", params={"min_days": 9999}, headers=auth).status_code
        == 400
    )

    # filters also reshape the explorer data (variants + dfg)
    variants = client.get(
        "/api/analysis/variants", params={"only_rework": "true"}, headers=auth
    ).json()
    assert len(variants) == 1 and variants[0]["case_count"] == 4


def test_case_explorer(client, auth):
    result = client.get("/api/analysis/cases", headers=auth).json()
    cases = result["cases"]
    assert len(cases) == 20
    first = cases[0]
    for field in ("case_id", "start", "end", "duration_days", "variant", "rework",
                  "vendor", "net_value", "conformant"):
        assert field in first
    assert sum(1 for c in cases if c["rework"]) == 4
    assert sum(1 for c in cases if c["conformant"]) == 12

    detail = client.get(f"/api/analysis/case/{first['case_id']}", headers=auth).json()
    assert detail["case_id"] == first["case_id"]
    assert len(detail["events"]) == first["events"]
    stamps = [e["timestamp"] for e in detail["events"]]
    assert stamps == sorted(stamps)


def test_sme_reference_process(client, auth):
    default = client.get("/api/reference", headers=auth).json()
    assert default["source"] == "most_frequent_variant"
    assert default["activities"] == HAPPY

    # SME sign-off required
    assert (
        client.put(
            "/api/reference", json={"activities": HAPPY, "updated_by": " "}, headers=auth
        ).status_code
        == 400
    )

    # SME tightens the target: approval is mandatory, invoice must follow GR
    saved = client.put(
        "/api/reference", json={"activities": HAPPY, "updated_by": "Venkat (SME)"}, headers=auth
    ).json()
    assert saved["source"] == "sme"

    conf = client.get("/api/analysis/conformance", headers=auth).json()
    assert "Venkat (SME)" in conf["note"]
    assert conf["conforming_cases"] == 12

    summary = client.get("/api/analysis/summary", headers=auth).json()
    assert "Venkat (SME)" in summary["reference_note"]


def test_insights(client, auth):
    result = client.get("/api/analysis/insights", headers=auth).json()
    insights = result["insights"]
    assert len(insights) >= 3
    for i in insights:
        assert i["title"] and i["detail"] and i["based_on"]
        assert i["severity"] in ("info", "warn")
    titles = " ".join(i["title"] for i in insights)
    assert "bottleneck" in titles.lower()
    assert "rework" in titles.lower()
