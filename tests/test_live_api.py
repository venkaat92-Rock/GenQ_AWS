"""Live API connector tests against protocol-faithful mock servers (ServiceNow Table
API, Jira REST v2, Salesforce REST, OData v2), plus saved connections and scheduled
re-extraction."""

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE


class MockApi(BaseHTTPRequestHandler):
    """Serves the JSON shapes the real systems return."""

    csv_payload = "EBELN,AEDAT\n1,2026-01-01\n"

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        if self.path.startswith("/api/now/table/incident"):
            body = {"result": [
                {"sys_id": "a1", "number": "INC001", "state": "2",
                 "opened_at": "2026-01-01 08:00:00", "priority": {"value": "1"}},
                {"sys_id": "a2", "number": "INC002", "state": "6",
                 "opened_at": "2026-01-02 09:00:00", "priority": {"value": "3"}},
            ]}
        elif self.path.startswith("/rest/api/2/search"):
            body = {"total": 1, "issues": [{
                "key": "OPS-1",
                "fields": {"project": {"key": "OPS"}, "issuetype": {"name": "Task"},
                           "status": {"name": "Done"}, "priority": {"name": "High"},
                           "created": "2026-01-01T08:00:00.000+0000",
                           "resolutiondate": "2026-01-03T10:00:00.000+0000",
                           "assignee": {"displayName": "Maria"},
                           "reporter": {"displayName": "Venkat"}},
                "changelog": {"histories": [{
                    "created": "2026-01-02T09:00:00.000+0000",
                    "author": {"displayName": "Maria"},
                    "items": [{"field": "status", "fromString": "To Do",
                               "toString": "Done"}],
                }]},
            }]}
        elif self.path.startswith("/services/data/"):
            body = {"done": True, "records": [
                {"attributes": {"type": "Opportunity"}, "Id": "006A", "Name": "Big deal",
                 "StageName": "Closed Won", "CreatedDate": "2026-01-01T08:00:00Z"},
            ]}
        elif self.path.startswith("/odata/PurchaseOrderSet"):
            body = {"d": {"results": [
                {"__metadata": {"type": "PO"}, "Ebeln": "4500000001",
                 "Aedat": "2026-01-05T00:00:00"},
            ]}}
        elif self.path.endswith(".csv"):
            data = self.csv_payload.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.end_headers()
            self.wfile.write(data)
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), MockApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Live Test"}).json()["api_key"]
    return {"X-API-Key": key}


def test_servicenow_jira_salesforce_odata(client, auth, mock_server):
    r = client.post("/api/connectors/extract", json={
        "kind": "servicenow",
        "config": {"instance_url": mock_server, "user": "u", "password": "p",
                   "tables": ["incident"]},
    }, headers=auth).json()
    assert r["staged"]["incident"] == 2

    r = client.post("/api/connectors/extract", json={
        "kind": "jira",
        "config": {"base_url": mock_server, "email": "e@x.com", "token": "t"},
    }, headers=auth).json()
    assert r["staged"]["issues"] == 1 and r["staged"]["changelog"] == 1

    r = client.post("/api/connectors/extract", json={
        "kind": "salesforce",
        "config": {"instance_url": mock_server, "access_token": "tok",
                   "objects": ["Opportunity"]},
    }, headers=auth).json()
    assert r["staged"]["Opportunity"] == 1

    r = client.post("/api/connectors/extract", json={
        "kind": "odata",
        "config": {"service_url": f"{mock_server}/odata",
                   "entities": ["PurchaseOrderSet"], "user": "u", "password": "p"},
    }, headers=auth).json()
    assert r["staged"]["PurchaseOrderSet"] == 1

    sources = client.get("/api/sources", headers=auth).json()
    assert {"INCIDENT", "ISSUES", "CHANGELOG", "OPPORTUNITY", "PURCHASEORDERSET"} <= set(sources)
    # attributes flattened, metadata stripped
    assert "priority" in [c["name"] for c in sources["INCIDENT"]["columns"]]
    assert "__metadata" not in [c["name"] for c in sources["PURCHASEORDERSET"]["columns"]]

    bad = client.post("/api/connectors/extract", json={
        "kind": "servicenow",
        "config": {"instance_url": "http://127.0.0.1:1", "user": "u", "password": "p",
                   "tables": ["incident"]},
    }, headers=auth)
    assert bad.status_code == 400


def test_saved_connection_masking_and_rerun(client, auth, mock_server):
    saved = client.post("/api/connectors/extract", json={
        "kind": "servicenow",
        "config": {"instance_url": mock_server, "user": "u", "password": "secret",
                   "tables": ["incident"]},
        "name": "Prod ServiceNow", "auto_refresh": True,
    }, headers=auth).json()
    assert saved["saved_as"] == "Prod ServiceNow"

    listing = client.get("/api/connections", headers=auth).json()["connections"]
    assert listing[0]["name"] == "Prod ServiceNow"
    assert listing[0]["config"]["password"] == "•••"  # secrets masked in the API

    rerun = client.post("/api/connections/Prod%20ServiceNow/run", headers=auth).json()
    assert rerun["staged"]["incident"] == 2

    assert client.delete("/api/connections/Prod%20ServiceNow", headers=auth).status_code == 200
    assert client.get("/api/connections", headers=auth).json()["connections"] == []


def test_schedule_reextracts_saved_connections(client, auth, mock_server, tmp_path):
    """The full Celonis loop: due schedule -> re-extract from source -> reload model."""
    from api.scheduler import run_due_schedules

    # stage P2P demo (for a confirmable model), then save an auto-refresh URL connection
    client.post("/api/sources/demo", headers=auth)
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=auth)
    client.post("/api/connectors/extract", json={
        "kind": "url",
        "config": {"connection": f"{mock_server}/extra.csv", "table": "extra_feed"},
        "name": "Nightly extra feed", "auto_refresh": True,
    }, headers=auth)
    client.put("/api/schedule",
               json={"enabled": True, "frequency": "hourly", "updated_by": "SME"},
               headers=auth)

    # source system data CHANGES, then the schedule fires
    MockApi.csv_payload = "EBELN,AEDAT\n1,2026-01-01\n2,2026-01-02\n3,2026-01-03\n"
    executed = run_due_schedules(datetime.now(timezone.utc) + timedelta(hours=2))
    assert len(executed) == 1

    sources = client.get("/api/sources", headers=auth).json()
    assert sources["EXTRA_FEED"]["row_count"] == 3  # fresh data pulled before the load

    runs = client.get("/api/dataload/runs", headers=auth).json()["runs"]
    assert runs[0]["refreshed_connections"] == ["Nightly extra feed"]