"""Workspace members: roles, emails, licenses, admin-only management, migration."""

import json

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
    key = client.post("/api/workspaces", json={"name": "Members Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _add(client, auth, name, email, role="editor", by="Venkat", licenses=None):
    body = {"name": name, "email": email, "role": role, "by": by}
    if licenses is not None:
        body["licenses"] = licenses
    return client.post("/api/members", json=body, headers=auth)


def test_bootstrap_validation_and_admin_only_management(client, auth):
    # first member must be an admin, with a valid email
    assert _add(client, auth, "Maria", "maria@acme.com").status_code == 400
    assert _add(client, auth, "Venkat", "not-an-email",
                role="admin").status_code == 400
    venkat = _add(client, auth, "Venkat", "Venkat@Acme.com", role="admin").json()
    assert venkat["email"] == "venkat@acme.com"  # normalized lowercase
    assert venkat["licenses"] == ["diagramiq", "processiq"]
    assert venkat["added_by"] == "bootstrap"

    # duplicates rejected: by name (case-insensitive) and by email
    assert _add(client, auth, "venkat", "v2@acme.com").status_code == 409
    assert _add(client, auth, "Somebody", "VENKAT@acme.com").status_code == 409

    # bad role / bad license / empty licenses rejected
    assert _add(client, auth, "Maria", "maria@acme.com",
                role="owner").status_code == 400
    assert _add(client, auth, "Maria", "maria@acme.com",
                licenses=["excel"]).status_code == 400
    assert _add(client, auth, "Maria", "maria@acme.com",
                licenses=[]).status_code == 400

    # only admins manage; the acting user resolves by name OR email
    assert _add(client, auth, "Maria", "maria@acme.com",
                by="Nobody").status_code == 403
    maria = _add(client, auth, "Maria", "maria@acme.com",
                 by="venkat@acme.com", licenses=["processiq"]).json()
    assert maria["licenses"] == ["processiq"]
    assert maria["added_by"] == "Venkat"

    # editors cannot manage members
    assert _add(client, auth, "Vik", "vik@acme.com",
                by="Maria").status_code == 403

    # audit trail records mutations
    listing = client.get("/api/members", headers=auth).json()
    assert listing["enabled"] is True
    assert [a["action"] for a in listing["audit"]] == ["added", "added"]


def test_updates_and_last_admin_protection(client, auth):
    venkat = _add(client, auth, "Venkat", "venkat@acme.com", role="admin").json()
    maria = _add(client, auth, "Maria", "maria@acme.com").json()

    # role + license + email updates, admin-signed
    updated = client.put(f"/api/members/{maria['id']}", json={
        "role": "viewer", "licenses": ["diagramiq"],
        "email": "maria.g@acme.com", "by": "Venkat",
    }, headers=auth).json()
    assert (updated["role"], updated["licenses"]) == ("viewer", ["diagramiq"])
    assert updated["email"] == "maria.g@acme.com"

    # non-admin cannot update; email collisions rejected
    assert client.put(f"/api/members/{maria['id']}", json={
        "role": "editor", "by": "Maria"}, headers=auth).status_code == 403
    assert client.put(f"/api/members/{maria['id']}", json={
        "email": "venkat@acme.com", "by": "Venkat"},
        headers=auth).status_code == 409

    # the last admin can be neither demoted nor removed
    assert client.put(f"/api/members/{venkat['id']}", json={
        "role": "editor", "by": "Venkat"}, headers=auth).status_code == 400
    assert client.delete(f"/api/members/{venkat['id']}?by=Venkat",
                         headers=auth).status_code == 400

    # promote Maria to admin -> now Venkat can step down
    client.put(f"/api/members/{maria['id']}", json={"role": "admin", "by": "Venkat"},
               headers=auth)
    assert client.put(f"/api/members/{venkat['id']}", json={
        "role": "editor", "by": "Venkat"}, headers=auth).status_code == 200

    # removal works once another admin exists
    assert client.delete(f"/api/members/{venkat['id']}?by=Maria",
                         headers=auth).status_code == 200


def test_diagramiq_license_gates_that_offering(client, auth):
    _add(client, auth, "Venkat", "venkat@acme.com", role="admin")
    # Paul holds only a ProcessIQ seat
    _add(client, auth, "Paul", "paul@acme.com", licenses=["processiq"])

    listing = client.get("/api/diagram/repo?user=Paul", headers=auth).json()
    assert listing["my_role"] == "editor"
    assert listing["items"] == [] and listing["folders"] == []

    # any DiagramIQ write is refused for the unlicensed member
    assert client.post("/api/diagram/repo/folders",
                       json={"name": "X", "by": "Paul"},
                       headers=auth).status_code == 403


def test_legacy_diagramiq_members_migrate(client, auth, tmp_path):
    # simulate a workspace that managed members inside DiagramIQ before
    ws_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    (ws_dir / "diagrams").mkdir(exist_ok=True)
    (ws_dir / "diagrams" / "members.json").write_text(json.dumps(
        [{"name": "Venkat", "role": "admin"}, {"name": "Maria", "role": "editor"}]
    ), encoding="utf-8")

    listing = client.get("/api/members", headers=auth).json()
    assert {m["name"] for m in listing["members"]} == {"Venkat", "Maria"}
    migrated = next(m for m in listing["members"] if m["name"] == "Venkat")
    assert migrated["email"] == ""  # admin fills it in afterwards
    assert migrated["licenses"] == ["diagramiq", "processiq"]
    assert not (ws_dir / "diagrams" / "members.json").exists()
