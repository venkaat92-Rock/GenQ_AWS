"""Tests for real load schedules and the run-history endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Sched Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _confirm(client, auth):
    client.post("/api/sources/demo", headers=auth)
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=auth)


def test_schedule_gate_and_edit(client, auth):
    # default: off
    assert client.get("/api/schedule", headers=auth).json()["enabled"] is False

    # cannot enable before a confirmed model (§1.1 — schedules never bypass the gate)
    blocked = client.put(
        "/api/schedule",
        json={"enabled": True, "frequency": "hourly", "updated_by": "Venkat"},
        headers=auth,
    )
    assert blocked.status_code == 409

    _confirm(client, auth)
    # SME name required
    assert client.put(
        "/api/schedule",
        json={"enabled": True, "frequency": "hourly", "updated_by": " "},
        headers=auth,
    ).status_code == 400
    # bad frequency rejected
    assert client.put(
        "/api/schedule",
        json={"enabled": True, "frequency": "weekly", "updated_by": "V"},
        headers=auth,
    ).status_code == 400

    saved = client.put(
        "/api/schedule",
        json={"enabled": True, "frequency": "hourly", "updated_by": "Venkat"},
        headers=auth,
    ).json()
    assert saved["enabled"] is True and saved["next_run"]

    # the canvas reflects it
    state = client.get("/api/dataload", headers=auth).json()
    assert "hourly (enabled)" in state["processing"]["schedule"]["mode"]


def test_due_schedule_materializes_and_logs(client, auth, tmp_path):
    from api import storage
    from api.scheduler import load_schedule, run_due_schedules

    _confirm(client, auth)
    client.put(
        "/api/schedule",
        json={"enabled": True, "frequency": "hourly", "updated_by": "Venkat"},
        headers=auth,
    )

    # not due yet -> nothing runs
    assert run_due_schedules(datetime.now(timezone.utc)) == []

    # jump past next_run -> the load executes and is recorded
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    executed = run_due_schedules(future)
    assert len(executed) == 1 and executed[0]["events"] == 107

    runs = client.get("/api/dataload/runs", headers=auth).json()["runs"]
    assert runs[0]["trigger"] == "schedule (hourly)"
    assert runs[0]["status"] == "ok" and runs[0]["events"] == 107

    # next_run advanced beyond the executed scan time
    ws = storage.all_workspaces()[0]
    assert datetime.fromisoformat(load_schedule(ws)["next_run"]) > future - timedelta(minutes=1)


def test_disabled_or_unconfirmed_schedules_do_not_run(client, auth):
    from api.scheduler import run_due_schedules, save_schedule
    from api import storage

    # workspace with sources but NO confirmed model, schedule forced on disk
    client.post("/api/sources/demo", headers=auth)
    ws = storage.all_workspaces()[0]
    save_schedule(ws, True, "hourly", "test")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert run_due_schedules(future) == []  # skipped, never bypasses confirmation
    runs = client.get("/api/dataload/runs", headers=auth).json()["runs"]
    assert runs[0]["status"] == "skipped"


def test_runs_endpoint_orders_newest_first(client, auth):
    _confirm(client, auth)
    client.post("/api/dataload/run", headers=auth)
    runs = client.get("/api/dataload/runs", headers=auth).json()["runs"]
    assert len(runs) == 2
    assert runs[0]["trigger"] == "manual run"          # newest first
    assert "mapping confirmed" in runs[1]["trigger"]