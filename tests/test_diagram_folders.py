"""Explorer folders + folder-level access provisioning (Signavio-style)."""

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
    key = client.post("/api/workspaces", json={"name": "Folder Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _item_in(client, auth, folder_id, name="Model", author="Venkat"):
    return client.post("/api/diagram/repo/items", json={
        "name": name, "level": "L4", "xml": MINIMAL_BPMN,
        "author": author, "folder_id": folder_id,
    }, headers=auth)


def test_folder_tree_and_item_placement(client, auth):
    # nested folders (no members yet -> access control off, anyone may organize)
    fin = client.post("/api/diagram/repo/folders",
                      json={"name": "Finance", "by": "Venkat"}, headers=auth).json()
    p2p = client.post("/api/diagram/repo/folders",
                      json={"name": "P2P", "parent_id": fin["id"], "by": "Venkat"},
                      headers=auth).json()
    assert p2p["parent_id"] == fin["id"]

    # duplicate sibling names are rejected; unknown parents 404
    assert client.post("/api/diagram/repo/folders",
                       json={"name": "p2p", "parent_id": fin["id"], "by": "V"},
                       headers=auth).status_code == 409
    assert client.post("/api/diagram/repo/folders",
                       json={"name": "X", "parent_id": "f_missing", "by": "V"},
                       headers=auth).status_code == 404

    # a diagram created inside the subfolder
    item = _item_in(client, auth, p2p["id"], name="Invoice handling").json()
    assert item["folder_id"] == p2p["id"]
    listing = client.get("/api/diagram/repo", headers=auth).json()
    assert {f["name"] for f in listing["folders"]} == {"Finance", "P2P"}

    # non-empty folders cannot be deleted; after moving the item to root they can
    assert client.delete(f"/api/diagram/repo/folders/{p2p['id']}?by=Venkat",
                         headers=auth).status_code == 400
    moved = client.put(f"/api/diagram/repo/{item['id']}",
                       json={"clear_folder": True, "by": "Venkat"},
                       headers=auth).json()
    assert moved["folder_id"] is None
    assert client.delete(f"/api/diagram/repo/folders/{p2p['id']}?by=Venkat",
                         headers=auth).status_code == 200

    # moving into a folder again via folder_id
    back = client.put(f"/api/diagram/repo/{item['id']}",
                      json={"folder_id": fin["id"], "by": "Venkat"},
                      headers=auth).json()
    assert back["folder_id"] == fin["id"]


def test_members_and_folder_access_enforcement(client, auth):
    members = "/api/members"
    # the first member must be an admin; afterwards only admins manage members
    assert client.post(members, json={"name": "Maria", "email": "maria@acme.com",
                                      "role": "editor"},
                       headers=auth).status_code == 400
    venkat = client.post(members, json={"name": "Venkat",
                                        "email": "venkat@acme.com",
                                        "role": "admin"}, headers=auth).json()
    assert client.post(members, json={"name": "Maria", "email": "maria@acme.com",
                                      "role": "editor",
                                      "by": "Maria"}, headers=auth).status_code == 403
    client.post(members, json={"name": "Maria", "email": "maria@acme.com",
                               "role": "editor", "by": "Venkat"}, headers=auth)
    client.post(members, json={"name": "Vik", "email": "vik@acme.com",
                               "role": "viewer", "by": "Venkat"}, headers=auth)
    assert client.post(members, json={"name": "maria", "email": "m2@acme.com",
                                      "role": "viewer",
                                      "by": "Venkat"}, headers=auth).status_code == 409

    # the last admin is protected
    assert client.delete(f"{members}/{venkat['id']}?by=Venkat",
                         headers=auth).status_code == 400

    # a restricted folder: only Maria may edit, Vik gets nothing
    conf = client.post("/api/diagram/repo/folders",
                       json={"name": "Confidential", "by": "Venkat"},
                       headers=auth).json()
    assert client.put(f"/api/diagram/repo/folders/{conf['id']}/access",
                      json={"access": {"maria": "edit"}, "by": "Maria"},
                      headers=auth).status_code == 403  # manage required
    granted = client.put(f"/api/diagram/repo/folders/{conf['id']}/access",
                         json={"access": {"maria": "edit"}, "by": "Venkat"},
                         headers=auth).json()
    assert granted["access"] == {"Maria": "edit"}

    # Maria (granted) can create + version inside; Vik and unknown users cannot
    item = _item_in(client, auth, conf["id"], name="Payroll run",
                    author="Maria").json()
    assert _item_in(client, auth, conf["id"], author="Vik").status_code == 403
    assert client.post(f"/api/diagram/repo/{item['id']}/versions",
                       json={"xml": MINIMAL_BPMN, "author": "Vik"},
                       headers=auth).status_code == 403
    assert client.post(f"/api/diagram/repo/{item['id']}/versions",
                       json={"xml": MINIMAL_BPMN, "author": "Somebody"},
                       headers=auth).status_code == 403
    assert client.post(f"/api/diagram/repo/{item['id']}/versions",
                       json={"xml": MINIMAL_BPMN, "author": "Maria"},
                       headers=auth).status_code == 200
    # admins always pass
    assert client.post(f"/api/diagram/repo/{item['id']}/versions",
                       json={"xml": MINIMAL_BPMN, "author": "Venkat"},
                       headers=auth).status_code == 200

    # listing: the restricted folder and its diagrams are invisible to Vik
    for_vik = client.get("/api/diagram/repo?user=Vik", headers=auth).json()
    assert all(f["id"] != conf["id"] for f in for_vik["folders"])
    assert all(i["id"] != item["id"] for i in for_vik["items"])
    assert for_vik["my_role"] == "viewer"
    for_maria = client.get("/api/diagram/repo?user=Maria", headers=auth).json()
    assert any(i["id"] == item["id"] for i in for_maria["items"])

    # viewers keep read access to unrestricted folders but cannot write anywhere
    open_folder = client.post("/api/diagram/repo/folders",
                              json={"name": "Public", "by": "Venkat"},
                              headers=auth).json()
    assert any(f["id"] == open_folder["id"] for f in
               client.get("/api/diagram/repo?user=Vik", headers=auth).json()["folders"])
    assert _item_in(client, auth, open_folder["id"],
                    author="Vik").status_code == 403
