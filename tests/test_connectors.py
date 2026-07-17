"""Tests for the process-connector catalog and the live extractors."""

import io
import sqlite3
import threading
import zipfile
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

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
    key = client.post("/api/workspaces", json={"name": "Conn Test"}).json()["api_key"]
    return {"X-API-Key": key}


def test_catalog_and_templates(client):
    listing = client.get("/api/connectors").json()
    ids = {c["id"] for c in listing["connectors"]}
    assert {"sap-p2p", "sap-o2c", "sap-ap", "sap-ar", "oracle-ebs-p2p", "oracle-ebs-o2c",
            "salesforce-opportunity", "salesforce-case", "servicenow-incident",
            "jira-issues", "dynamics365-fo-p2p", "coupa-p2p"} <= ids
    assert {l["kind"] for l in listing["live"]} == {"sqlite", "postgres", "mysql", "url"}

    sap = client.get("/api/connectors/sap-p2p").json()
    assert sap["agent_support"] == "full"
    ekko = next(t for t in sap["tables"] if t["name"] == "EKKO")
    assert ekko["required"] and "EBELN" in ekko["columns"]

    zip_response = client.get("/api/connectors/sap-p2p/templates")
    assert zip_response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(zip_response.content))
    assert "EKKO.csv" in archive.namelist() and "README.txt" in archive.namelist()
    assert archive.read("EKKO.csv").decode().startswith("EBELN,")

    assert client.get("/api/connectors/nope").status_code == 404


def test_sqlite_live_extraction_feeds_the_agent(client, auth, tmp_path):
    """Real end-to-end: sqlite DB -> live extract -> agent drafts P2P from it."""
    db = tmp_path / "erp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE EKKO (EBELN TEXT, AEDAT TEXT, LIFNR TEXT)")
    con.execute("INSERT INTO EKKO VALUES ('4500000001','2026-01-05 09:00:00','V1')")
    con.execute("CREATE TABLE EKPO (EBELN TEXT, EBELP TEXT, NETWR REAL)")
    con.execute("INSERT INTO EKPO VALUES ('4500000001','00010',100.0)")
    con.commit()
    con.close()

    result = client.post(
        "/api/connectors/extract",
        json={"kind": "sqlite",
              "config": {"connection": str(db), "tables": ["EKKO", "EKPO"]}},
        headers=auth,
    ).json()
    assert result["staged"] == {"ekko": 1, "ekpo": 1}

    sources = client.get("/api/sources", headers=auth).json()
    assert {"EKKO", "EKPO"} <= set(sources)

    spec = client.post("/api/mapping/draft", headers=auth).json()
    assert spec["process"] == "procure_to_pay"
    assert any(a["name"] == "PO Created" for a in spec["activities"])

    # bad table -> clean 400, good tables stay staged
    bad = client.post(
        "/api/connectors/extract",
        json={"kind": "sqlite", "config": {"connection": str(db), "tables": ["NOPE"]}},
        headers=auth,
    )
    assert bad.status_code == 400


def test_url_extraction(client, auth, tmp_path):
    (tmp_path / "orders.csv").write_text("VBELN,ERDAT\n1,2026-01-01\n2,2026-01-02\n",
                                         encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = client.post(
            "/api/connectors/extract",
            json={"kind": "url",
                  "config": {"connection": f"http://127.0.0.1:{port}/orders.csv",
                             "table": "vbak"}},
            headers=auth,
        ).json()
        assert result["staged"] == {"vbak": 2}
    finally:
        server.shutdown()

    bad = client.post(
        "/api/connectors/extract",
        json={"kind": "url", "config": {"connection": "ftp://x/y.csv", "table": "t"}},
        headers=auth,
    )
    assert bad.status_code == 400