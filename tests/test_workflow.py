"""DiagramIQ workflow automation: authoring, validation, execution, from-model."""

import pytest
from fastapi.testclient import TestClient

MINIMAL_BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'targetNamespace="http://processiq.local">'
    '<bpmn:process id="Process_1" isExecutable="false">'
    '<bpmn:startEvent id="Start_1" name="Start"/>'
    '<bpmn:task id="T1" name="Capture request"/>'
    '<bpmn:task id="T2" name="Approve request"/>'
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
    key = client.post("/api/workspaces", json={"name": "WF Test"}).json()["api_key"]
    return {"X-API-Key": key}


APPROVAL_WF = {
    "name": "Invoice approval",
    "trigger": {"type": "form", "fields": [{"key": "amount", "label": "Amount",
                                            "type": "number"}]},
    "roles": ["Requester", "Approver", "Finance"],
    "start_step": "s1",
    "steps": [
        {"id": "s1", "name": "Submit invoice", "type": "task", "assignee": "Requester",
         "form": [{"key": "vendor", "label": "Vendor", "type": "text"}], "next": "s2"},
        {"id": "s2", "name": "Manager approval", "type": "approval",
         "assignee": "Approver", "on_approve": "s3", "on_reject": "s4"},
        {"id": "s3", "name": "Notify finance", "type": "notification",
         "assignee": "Finance", "next": "end"},
        {"id": "s4", "name": "Notify rejection", "type": "notification", "next": "end"},
    ],
    "by": "Venkat",
}


def test_authoring_validation_and_activation(client, auth):
    wf = client.post("/api/diagram/workflows", json=APPROVAL_WF, headers=auth).json()
    assert wf["status"] == "draft" and wf["start_step"] == "s1"

    # cannot start a draft workflow
    assert client.post(f"/api/diagram/workflows/{wf['id']}/start",
                       json={"variables": {}}, headers=auth).status_code == 409

    # dangling routing is rejected
    bad = {**APPROVAL_WF, "steps": [
        {"id": "s1", "name": "x", "type": "task", "next": "nowhere"}], "start_step": "s1"}
    assert client.post("/api/diagram/workflows", json=bad,
                       headers=auth).status_code == 400
    # approval without both paths is rejected
    bad2 = {**APPROVAL_WF, "steps": [
        {"id": "s1", "name": "x", "type": "approval", "on_approve": "end"}],
        "start_step": "s1"}
    assert client.post("/api/diagram/workflows", json=bad2,
                       headers=auth).status_code == 400

    activated = client.post(f"/api/diagram/workflows/{wf['id']}/activate",
                            headers=auth).json()
    assert activated["status"] == "active"


def test_run_case_through_approval(client, auth):
    wf = client.post("/api/diagram/workflows", json=APPROVAL_WF, headers=auth).json()
    client.post(f"/api/diagram/workflows/{wf['id']}/activate", headers=auth)

    case = client.post(f"/api/diagram/workflows/{wf['id']}/start",
                       json={"variables": {"amount": "5000"}, "by": "Venkat"},
                       headers=auth).json()
    # trigger variables captured; parked on the first human step
    assert case["status"] == "running"
    assert case["current_step"] == "s1" and case["current_step_name"] == "Submit invoice"
    assert case["current_form"][0]["key"] == "vendor"

    # complete the task with form data -> advances to the approval step
    case = client.post(f"/api/diagram/workflows/instances/{case['id']}/complete",
                       json={"data": {"vendor": "Acme"}, "by": "Rae"},
                       headers=auth).json()
    assert case["current_step"] == "s2" and case["current_step_type"] == "approval"

    # an approval needs a decision
    assert client.post(f"/api/diagram/workflows/instances/{case['id']}/complete",
                       json={"by": "Mgr"}, headers=auth).status_code == 400

    # approve -> notification runs automatically -> case completes
    case = client.post(f"/api/diagram/workflows/instances/{case['id']}/complete",
                       json={"decision": "approve", "by": "Mgr"}, headers=auth).json()
    assert case["status"] == "completed" and case["current_step"] is None
    actions = [h["action"] for h in case["history"]]
    assert "started" in actions and "approve" in actions
    assert any(h["step_name"] == "Notify finance" for h in case["history"])
    assert case["variables"]["vendor"] == "Acme"


def test_reject_path_and_condition_branch(client, auth):
    # reject routes down the rejection path
    wf = client.post("/api/diagram/workflows", json=APPROVAL_WF, headers=auth).json()
    client.post(f"/api/diagram/workflows/{wf['id']}/activate", headers=auth)
    case = client.post(f"/api/diagram/workflows/{wf['id']}/start",
                       json={"variables": {}}, headers=auth).json()
    client.post(f"/api/diagram/workflows/instances/{case['id']}/complete",
                json={"data": {"vendor": "X"}}, headers=auth)
    done = client.post(f"/api/diagram/workflows/instances/{case['id']}/complete",
                       json={"decision": "reject", "by": "Mgr"}, headers=auth).json()
    assert done["status"] == "completed"
    assert any(h["step_name"] == "Notify rejection" for h in done["history"])

    # a condition step routes on a variable set by the trigger
    cond_wf = {
        "name": "Routing", "start_step": "s1",
        "steps": [
            {"id": "s1", "name": "Route", "type": "condition",
             "conditions": [{"var": "amount", "op": ">", "value": "1000",
                             "next": "s2"}], "default_next": "s3"},
            {"id": "s2", "name": "Senior approval", "type": "approval",
             "on_approve": "end", "on_reject": "end"},
            {"id": "s3", "name": "Auto-approve", "type": "end"},
        ], "by": "V"}
    w2 = client.post("/api/diagram/workflows", json=cond_wf, headers=auth).json()
    client.post(f"/api/diagram/workflows/{w2['id']}/activate", headers=auth)
    big = client.post(f"/api/diagram/workflows/{w2['id']}/start",
                      json={"variables": {"amount": "5000"}}, headers=auth).json()
    assert big["current_step"] == "s2"  # >1000 -> senior approval
    small = client.post(f"/api/diagram/workflows/{w2['id']}/start",
                        json={"variables": {"amount": "50"}}, headers=auth).json()
    assert small["status"] == "completed"  # default -> auto-approve end


def test_generate_from_model_and_delete_guard(client, auth):
    model = client.post("/api/diagram/repo/items", json={
        "name": "Onboarding", "level": "L4", "xml": MINIMAL_BPMN, "author": "V",
    }, headers=auth).json()
    wf = client.post(f"/api/diagram/workflows/from-model/{model['id']}",
                     json={"by": "V"}, headers=auth).json()
    assert wf["model_id"] == model["id"]
    names = [s["name"] for s in wf["steps"]]
    assert names == ["Capture request", "Approve request"]
    assert wf["steps"][0]["type"] == "task" and wf["steps"][-1]["next"] == "end"

    # a running case blocks deletion; editing drops back to draft
    client.post(f"/api/diagram/workflows/{wf['id']}/activate", headers=auth)
    client.post(f"/api/diagram/workflows/{wf['id']}/start",
                json={"variables": {}}, headers=auth)
    assert client.delete(f"/api/diagram/workflows/{wf['id']}",
                         headers=auth).status_code == 400

    edited = client.put(f"/api/diagram/workflows/{wf['id']}", json={
        **APPROVAL_WF, "name": "Onboarding v2"}, headers=auth).json()
    assert edited["status"] == "draft" and edited["name"] == "Onboarding v2"

    listing = client.get("/api/diagram/workflows", headers=auth).json()["workflows"]
    row = next(w for w in listing if w["id"] == wf["id"])
    assert row["running"] == 1 and row["step_count"] == 4
