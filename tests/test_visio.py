"""Visio -> BPMN tests using the DiagramIQ-Lonza sample .vsdx fixtures."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diagramiq.service import DiagramError, validate_bpmn_xml, visio_to_bpmn_xml

VISIO_DIR = Path(__file__).parent.parent / "fixtures" / "visio"


def test_engine_converts_sample_vsdx_preserving_layout():
    xml = visio_to_bpmn_xml(VISIO_DIR / "ecc_workflow.vsdx", "ECC Workflow",
                            preserve_layout=True)
    assert "definitions" in xml and "BPMNDiagram" in xml
    assert xml.count("task") > 3          # real tasks came across
    assert "laneSet" in xml or "participant" in xml  # swimlanes preserved
    issues = validate_bpmn_xml(xml)
    assert isinstance(issues, list)

    # second sample too
    xml2 = visio_to_bpmn_xml(VISIO_DIR / "purchase_requisition.vsdx", "PR")
    assert "definitions" in xml2


def test_engine_rejects_binary_vsd(tmp_path):
    fake = tmp_path / "old.vsd"
    fake.write_bytes(b"\xd0\xcf\x11\xe0 binary visio")
    with pytest.raises(DiagramError, match="vsdx"):
        visio_to_bpmn_xml(fake, "x")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Visio Test"}).json()["api_key"]
    return {"X-API-Key": key}


def test_visio_endpoint_full_flow(client, auth):
    with open(VISIO_DIR / "ecc_workflow.vsdx", "rb") as f:
        response = client.post(
            "/api/diagram/visio",
            files={"file": ("ecc_workflow.vsdx", f.read(),
                            "application/vnd.ms-visio.drawing")},
            data={"preserve_layout": "true"},
            headers=auth,
        )
    assert response.status_code == 200
    result = response.json()
    assert result["bpmn"].endswith(".bpmn")
    assert result["preserve_layout"] is True
    assert result["vision_check"] is None  # no image attached

    xml = client.get(
        f"/api/diagram/artifacts/{result['bpmn']}/xml", headers=auth
    ).json()["xml"]
    assert "BPMNDiagram" in xml

    # sample shortcut works without a file
    sample = client.post("/api/diagram/visio", data={"use_sample": "true"}, headers=auth)
    assert sample.status_code == 200

    # vision check without a key reports it was skipped, not an error
    with open(VISIO_DIR / "ecc_workflow.vsdx", "rb") as f:
        with_img = client.post(
            "/api/diagram/visio",
            files={
                "file": ("ecc.vsdx", f.read(), "application/octet-stream"),
                "image": ("shot.png", b"\x89PNG\r\n\x1a\nfake", "image/png"),
            },
            headers=auth,
        )
    assert with_img.status_code == 200
    assert "skipped" in (with_img.json()["vision_check"] or {})