"""API journey test: workspace -> demo data -> draft -> blocked confirm -> answers ->
confirm+materialize -> analysis -> NL ask. Exercises the whole SaaS surface."""

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
    response = client.post("/api/workspaces", json={"name": "Acme Test"})
    assert response.status_code == 200
    return {"X-API-Key": response.json()["api_key"]}


def test_auth_required(client):
    assert client.get("/api/sources").status_code == 401
    assert client.get("/api/sources", headers={"X-API-Key": "piq_wrong"}).status_code == 401


def test_full_journey(client, auth):
    # 1. stage demo SAP sources
    response = client.post("/api/sources/demo", headers=auth)
    assert response.status_code == 200
    assert response.json()["staged"]["EKKO"] == 20

    # analysis is blocked before any mapping is confirmed
    assert client.get("/api/analysis/summary", headers=auth).status_code == 400

    # 2. agent drafts the mapping
    spec = client.post("/api/mapping/draft", headers=auth).json()
    assert spec["status"] == "draft"
    assert len(spec["activities"]) == 6
    qids = [q["id"] for q in spec["open_questions"]]
    assert CASE_GRANULARITY_QID in qids

    # 3. confirm is refused while the open question is unanswered (the §1.1 gate)
    refused = client.post("/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=auth)
    assert refused.status_code == 409

    # 4. answer the granularity question -> agent re-drafts
    spec = client.post(
        "/api/mapping/answers",
        json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}},
        headers=auth,
    ).json()
    assert spec["version"] == 2

    # 5. confirm with a named reviewer -> materialize
    confirmed = client.post(
        "/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=auth
    ).json()
    assert confirmed["materialized"]["events"] == 107
    assert confirmed["materialized"]["cases"] == 20

    # 5b. a confirmed mapping is locked; "start a new version" reopens it to draft,
    # bumps the version, keeps the content, and can be re-confirmed
    locked = client.get("/api/mapping", headers=auth).json()
    assert locked["status"] == "confirmed" and locked["version"] == 2
    reopened = client.post("/api/mapping/reopen", headers=auth).json()
    assert reopened["status"] == "draft" and reopened["version"] == 3
    assert reopened["reviewer"] is None
    assert len(reopened["activities"]) == len(locked["activities"])  # content kept
    # answers are retained, so it can be re-confirmed straight away
    reconfirmed = client.post(
        "/api/mapping/confirm", json={"reviewer": "Maria"}, headers=auth
    ).json()
    assert reconfirmed["materialized"]["events"] == 107
    assert client.get("/api/mapping", headers=auth).json()["reviewer"] == "Maria"
    # reopen the now-confirmed v3 -> v4 draft, then reopening a draft is refused
    assert client.post("/api/mapping/reopen", headers=auth).json()["version"] == 4
    assert client.post("/api/mapping/reopen", headers=auth).status_code == 409

    # 6. analysis endpoints
    summary = client.get("/api/analysis/summary", headers=auth).json()
    assert summary["cases"] == 20
    assert summary["rework_share"] == pytest.approx(0.2)

    variants = client.get("/api/analysis/variants", headers=auth).json()
    assert variants[0]["case_count"] == 12

    for endpoint in ("dfg", "throughput", "bottlenecks", "rework", "conformance"):
        assert client.get(f"/api/analysis/{endpoint}", headers=auth).status_code == 200

    # 7. NL self-serve with citations
    answer = client.post(
        "/api/ask", json={"question": "Where is the biggest bottleneck?"}, headers=auth
    ).json()
    assert answer["intent"] == "bottlenecks"
    assert "citations" in answer and answer["citations"]["activities_used"]

    vendor_answer = client.post(
        "/api/ask",
        json={"question": "How long does Acme Industrial take end to end?"},
        headers=auth,
    ).json()
    assert vendor_answer["intent"] == "throughput"
    assert any("Acme Industrial" in f for f in vendor_answer["citations"]["filters"])


def test_workspaces_are_isolated(client, auth):
    other = client.post("/api/workspaces", json={"name": "Other Co"}).json()
    other_auth = {"X-API-Key": other["api_key"]}
    client.post("/api/sources/demo", headers=auth)
    assert client.get("/api/sources", headers=auth).json() != {}
    assert client.get("/api/sources", headers=other_auth).json() == {}
