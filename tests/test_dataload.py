"""Tests for the data-load canvas and the Ask co-pilot."""

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
    key = client.post("/api/workspaces", json={"name": "DL Test"}).json()["api_key"]
    headers = {"X-API-Key": key}
    client.post("/api/sources/demo", headers=headers)
    return headers


def _confirm(client, auth):
    client.post("/api/mapping/draft", headers=auth)
    client.post(
        "/api/mapping/answers",
        json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}},
        headers=auth,
    )
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=auth)


def test_dataload_before_model(client, auth):
    state = client.get("/api/dataload", headers=auth).json()
    assert state["sources"]["connections"][0]["tables"] == 8
    assert state["processing"]["jobs"]["extractions"] == 8
    assert state["processing"]["jobs"]["transformations"] == 0
    assert state["processing"]["jobs"]["model_loads"] == 0
    assert state["output"]["data_model"]["status"] == "none"
    # run-now refused without a confirmed model
    assert client.post("/api/dataload/run", headers=auth).status_code == 409


def test_dataload_after_confirm_and_run(client, auth):
    _confirm(client, auth)
    state = client.get("/api/dataload", headers=auth).json()
    jobs = state["processing"]["jobs"]
    assert jobs["transformations"] == 8  # 6 activities + 2 attributes
    assert jobs["model_loads"] == 1  # the confirm-triggered load
    model = state["output"]["data_model"]
    assert model["status"] == "working"
    assert model["confirmed_by"] == "SME"
    assert model["authored_by"].startswith("AI modelling agent")
    assert state["output"]["objects"] == 20 and state["output"]["events"] == 107
    assert state["processing"]["storage"]["size_mb"] > 0

    run = client.post("/api/dataload/run", headers=auth).json()
    assert run["materialized"]["events"] == 107
    state2 = client.get("/api/dataload", headers=auth).json()
    assert state2["processing"]["jobs"]["model_loads"] == 2
    assert state2["processing"]["last_run"]["trigger"] == "manual run"


def test_copilot_context_and_general(client, auth):
    _confirm(client, auth)

    # scoped to the data model element
    model_q = client.post(
        "/api/copilot",
        json={"question": "who created this and can I change it?", "context": "data_model"},
        headers=auth,
    ).json()
    assert "AI modelling agent" in model_q["answer"]
    assert "SME" in model_q["answer"]
    assert model_q["scope"].startswith("selected element: data model")

    # scoped to data jobs — surfaces the risky transformations for review
    jobs_q = client.post(
        "/api/copilot",
        json={"question": "which transformations should I review?", "context": "data_jobs"},
        headers=auth,
    ).json()
    assert "medium" in jobs_q["answer"]

    # general data-load question, no context
    sched_q = client.post(
        "/api/copilot", json={"question": "when does the data refresh?"}, headers=auth
    ).json()
    assert "Run now" in sched_q["answer"] or "on-confirm" in sched_q["answer"]

    # process question falls through to the NL analysis layer
    proc_q = client.post(
        "/api/copilot", json={"question": "where is the biggest bottleneck?"}, headers=auth
    ).json()
    assert proc_q["engine"] == "process-nlq"
    assert "citations" in proc_q
