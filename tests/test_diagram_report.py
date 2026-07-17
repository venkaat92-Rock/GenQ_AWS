"""DiagramIQ model reporting: counts by level/status, approval funnel, activity."""

import pytest
from fastapi.testclient import TestClient

MINIMAL_BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'targetNamespace="http://processiq.local">'
    '<bpmn:process id="Process_1" isExecutable="false">'
    '<bpmn:startEvent id="Start_1" name="Start"/>'
    '</bpmn:process>'
    '<bpmndi:BPMNDiagram id="D1"/></bpmn:definitions>'
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Report Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _model(client, auth, name, level="L4"):
    return client.post("/api/diagram/repo/items", json={
        "name": name, "level": level, "xml": MINIMAL_BPMN, "author": "Venkat",
    }, headers=auth).json()


def test_model_report_counts_and_funnel(client, auth):
    _model(client, auth, "Draft one")                       # stays draft
    b = _model(client, auth, "Approved one", level="L3")    # -> approved
    client.post(f"/api/diagram/repo/{b['id']}/submit",
                json={"approver": "Maria", "by": "Venkat"}, headers=auth)
    client.post(f"/api/diagram/repo/{b['id']}/decide",
                json={"action": "approve", "by": "Maria"}, headers=auth)
    c = _model(client, auth, "Pending one")                 # -> in_review
    client.post(f"/api/diagram/repo/{c['id']}/submit",
                json={"approver": "Maria", "by": "Venkat"}, headers=auth)

    rep = client.get("/api/diagram/repo/report", headers=auth).json()
    assert rep["total"] == 3
    assert rep["by_status"]["approved"] == 1
    assert rep["by_status"]["in_review"] == 1
    assert rep["by_status"]["draft"] == 1
    assert rep["by_level"]["L4"] == 2 and rep["by_level"]["L3"] == 1
    assert rep["approval_rate"] == round(1 / 3 * 100, 1)
    assert rep["total_versions"] == 3

    assert [p["name"] for p in rep["pending_approvals"]] == ["Pending one"]
    assert rep["pending_approvals"][0]["approver"] == "Maria"

    assert [a["name"] for a in rep["recently_approved"]] == ["Approved one"]
    assert rep["recently_approved"][0]["approved_by"] == "Maria"

    actions = {a["action"] for a in rep["recent_activity"]}
    assert {"created", "submitted", "approved"} <= actions
    # each activity row is tagged with its model
    assert all("model" in a for a in rep["recent_activity"])

    # the report route is not shadowed by /repo/{item_id}
    assert client.get(f"/api/diagram/repo/{b['id']}",
                      headers=auth).json()["name"] == "Approved one"


def test_report_empty_workspace(client, auth):
    rep = client.get("/api/diagram/repo/report", headers=auth).json()
    assert rep["total"] == 0 and rep["approval_rate"] == 0.0
    assert rep["pending_approvals"] == [] and rep["recent_activity"] == []
