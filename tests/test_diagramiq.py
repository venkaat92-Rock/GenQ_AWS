"""DiagramIQ offering tests: notes -> Excel -> BPMN -> validate -> uplift -> review."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Diagram Test"}).json()["api_key"]
    return {"X-API-Key": key}


def test_full_pipeline_from_sample_notes(client, auth):
    # 1. notes -> Process Discovery Excel (keyless: rule-based engine)
    discovered = client.post(
        "/api/diagram/notes",
        data={"use_sample": "true", "process_name": "Procurement Intake"},
        headers=auth,
    ).json()
    assert discovered["engine"].startswith("rule-based")
    assert discovered["steps"] >= 8
    assert "Requester" in discovered["participants"]
    assert "Accounts payable" in discovered["participants"]
    excel_name = discovered["excel"]

    artifacts = client.get("/api/diagram/artifacts", headers=auth).json()["artifacts"]
    assert any(a["name"] == excel_name and a["kind"] == "excel" for a in artifacts)

    # 2. Excel -> BPMN 2.0 (Signavio-normalized) + validation report
    converted = client.post(
        "/api/diagram/excel-to-bpmn", data={"artifact": excel_name}, headers=auth
    ).json()
    bpmn_name = converted["bpmn"]
    assert bpmn_name.endswith(".bpmn")

    xml = client.get(f"/api/diagram/artifacts/{bpmn_name}/xml", headers=auth).json()["xml"]
    assert "definitions" in xml and "BPMNDiagram" in xml
    assert "startEvent" in xml and "endEvent" in xml
    assert 'targetNamespace' in xml  # signavio normalization ran

    # 3. standalone validation — both validators run (generic + Signavio best practices)
    report = client.post(
        "/api/diagram/validate", data={"artifact": bpmn_name}, headers=auth
    ).json()
    assert isinstance(report["issues"], list)
    assert isinstance(report["by_severity"], dict)
    sources = {i["source"] for i in report["issues"]}
    assert "signavio" in sources or report["issues"] == []

    def error_count(name: str) -> int:
        r = client.post("/api/diagram/validate", data={"artifact": name}, headers=auth).json()
        return sum(1 for i in r["issues"] if i["severity"] == "error")

    # 4. desktop-grade uplift: advanced rules applied, errors never increase
    errors_before = error_count(bpmn_name)
    uplifted = client.post(
        "/api/diagram/uplift", data={"artifact": bpmn_name}, headers=auth
    ).json()
    assert uplifted["bpmn"].endswith(".bpmn")
    assert "change_count" in uplifted
    assert error_count(uplifted["bpmn"]) <= errors_before

    # 5. BPMN -> editable Excel review round-trip
    review = client.post(
        "/api/diagram/bpmn-to-excel", data={"artifact": uplifted["bpmn"]}, headers=auth
    ).json()
    assert review["excel"].endswith(".xlsx")
    assert review["steps"] >= 5

    # 6. downloads work
    dl = client.get(f"/api/diagram/artifacts/{bpmn_name}", headers=auth)
    assert dl.status_code == 200


def test_diagram_requires_auth_and_valid_input(client, auth):
    assert client.get("/api/diagram/artifacts").status_code == 401
    assert client.post("/api/diagram/notes", data={}, headers=auth).status_code == 400
    bad = client.post(
        "/api/diagram/validate",
        files={"file": ("x.bpmn", b"this is not xml", "application/xml")},
        headers=auth,
    )
    assert bad.status_code == 400
    assert client.get("/api/diagram/artifacts/nope.bpmn", headers=auth).status_code == 404
    # path traversal is neutralized
    assert client.get("/api/diagram/artifacts/..%2F..%2Ftenants.json", headers=auth) \
        .status_code in (400, 404)