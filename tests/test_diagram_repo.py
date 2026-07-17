"""BPMN repository tests: auto-registration, L1-L4 hierarchy, versioning, approvals."""

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
    key = client.post("/api/workspaces", json={"name": "Repo Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _generate_l4(client, auth) -> str:
    """notes sample -> excel -> BPMN; returns the auto-registered repo id."""
    discovered = client.post("/api/diagram/notes", data={"use_sample": "true"},
                             headers=auth).json()
    converted = client.post("/api/diagram/excel-to-bpmn",
                            data={"artifact": discovered["excel"]}, headers=auth).json()
    assert "repo_id" in converted
    return converted["repo_id"]


def test_generated_bpmn_is_registered_as_l4_draft(client, auth):
    repo_id = _generate_l4(client, auth)
    listing = client.get("/api/diagram/repo", headers=auth).json()["items"]
    item = next(i for i in listing if i["id"] == repo_id)
    assert item["level"] == "L4"
    assert item["status"] == "draft"
    assert item["version_count"] == 1
    assert item["owner"] == "DiagramIQ agent"
    assert item["latest_file"].endswith(".bpmn")


def test_adopting_a_pre_repository_artifact(client, auth):
    """BPMN files converted before the repository existed can be brought under
    governance via /repo/adopt, idempotently."""
    repo_id = _generate_l4(client, auth)
    listing = client.get("/api/diagram/repo", headers=auth).json()["items"]
    file_name = next(i for i in listing if i["id"] == repo_id)["latest_file"]

    # wipe the repo index to simulate an artifact created before the repository
    import json as _json
    from pathlib import Path as _Path
    import os
    repo_json = next(_Path(os.environ["PROCESSIQ_DATA"]).rglob("repo.json"))
    repo_json.write_text("[]", encoding="utf-8")

    adopted = client.post("/api/diagram/repo/adopt", json={"file": file_name},
                          headers=auth).json()
    assert adopted["level"] == "L4" and adopted["status"] == "draft"
    assert adopted["latest_file"] == file_name
    assert adopted["audit"][0]["comment"] == "adopted from artifacts"

    # idempotent: adopting again returns the same item, no duplicate
    again = client.post("/api/diagram/repo/adopt", json={"file": file_name},
                        headers=auth).json()
    assert again["id"] == adopted["id"]
    assert len(client.get("/api/diagram/repo", headers=auth).json()["items"]) == 1

    # non-BPMN artifacts are refused
    bad = client.post("/api/diagram/repo/adopt", json={"file": "missing.bpmn"},
                      headers=auth)
    assert bad.status_code == 404


def test_hierarchy_versioning_and_approval_flow(client, auth):
    l4_id = _generate_l4(client, auth)

    # an L2 process group drawn in the editor
    l2 = client.post("/api/diagram/repo/items", json={
        "name": "Source to Pay", "level": "L2", "xml": MINIMAL_BPMN, "author": "Maria",
    }, headers=auth).json()
    assert l2["level"] == "L2" and l2["status"] == "draft"

    # hierarchy: the generated L4 hangs under the L2 group
    updated = client.put(f"/api/diagram/repo/{l4_id}",
                         json={"parent_id": l2["id"], "by": "Maria"}, headers=auth).json()
    assert updated["parent_id"] == l2["id"]
    # a parent on the same/lower level is rejected
    bad = client.put(f"/api/diagram/repo/{l2['id']}",
                     json={"parent_id": l4_id, "by": "Maria"}, headers=auth)
    assert bad.status_code == 400

    # editing: save a new version with element attributes
    v2 = client.post(f"/api/diagram/repo/{l4_id}/versions", json={
        "xml": MINIMAL_BPMN, "author": "Maria",
        "comment": "simplified after workshop",
        "attributes": {"Start_1": {"System": "SAP", "RACI": "Procurement"}},
    }, headers=auth).json()
    assert len(v2["versions"]) == 2
    assert v2["attributes"]["Start_1"]["System"] == "SAP"
    assert v2["status"] == "draft"

    # invalid XML is rejected as a version
    assert client.post(f"/api/diagram/repo/{l4_id}/versions", json={
        "xml": "not xml", "author": "Maria",
    }, headers=auth).status_code == 400

    # approval workflow: submit -> approve, with audit trail
    assert client.post(f"/api/diagram/repo/{l4_id}/decide", json={
        "action": "approve", "by": "Venkat",
    }, headers=auth).status_code == 409  # not submitted yet
    submitted = client.post(f"/api/diagram/repo/{l4_id}/submit", json={
        "approver": "Venkat", "by": "Maria",
    }, headers=auth).json()
    assert submitted["status"] == "in_review" and submitted["approver"] == "Venkat"
    approved = client.post(f"/api/diagram/repo/{l4_id}/decide", json={
        "action": "approve", "by": "Venkat", "comment": "looks correct",
    }, headers=auth).json()
    assert approved["status"] == "approved"
    actions = [a["action"] for a in approved["audit"]]
    assert actions == ["created", "metadata", "new_version", "submitted", "approved"]

    # a new version after approval drops back to draft (re-approval required)
    v3 = client.post(f"/api/diagram/repo/{l4_id}/versions", json={
        "xml": MINIMAL_BPMN, "author": "Maria", "comment": "post-approval tweak",
    }, headers=auth).json()
    assert v3["status"] == "draft"

    # reject path
    client.post(f"/api/diagram/repo/{l4_id}/submit",
                json={"approver": "Venkat", "by": "Maria"}, headers=auth)
    rejected = client.post(f"/api/diagram/repo/{l4_id}/decide", json={
        "action": "reject", "by": "Venkat", "comment": "missing gateway labels",
    }, headers=auth).json()
    assert rejected["status"] == "rejected"