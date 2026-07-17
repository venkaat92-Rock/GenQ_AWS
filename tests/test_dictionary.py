"""DiagramIQ dictionary: categories, entries, merge, bulk import/export."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

MINIMAL_BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'targetNamespace="http://processiq.local">'
    '<bpmn:process id="Process_1" isExecutable="false">'
    '<bpmn:startEvent id="Start_1" name="Start"/>'
    '<bpmn:task id="Task_1" name="Approve invoice"/>'
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
    key = client.post("/api/workspaces", json={"name": "Dict Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _cats(client, auth):
    return {c["name"]: c for c in
            client.get("/api/diagram/dictionary", headers=auth).json()["categories"]}


def test_default_categories_and_entry_lifecycle(client, auth):
    cats = _cats(client, auth)
    # Signavio's default tree, including subcategories under Organizational Units
    assert {"Organizational Units", "Documents", "Activities", "Events",
            "IT Systems", "Risks", "Controls", "Others"} <= set(cats)
    assert cats["Roles"]["parent_id"] == cats["Organizational Units"]["id"]

    acts = cats["Activities"]["id"]
    entry = client.post("/api/diagram/dictionary/entries", json={
        "category_id": acts, "title": "Approve invoice",
        "description": "Two-eyes check by AP clerk.",
        "attributes": {"System": "SAP", "RACI": "AP Clerk"}, "by": "Venkat",
    }, headers=auth).json()
    assert entry["attributes"]["System"] == "SAP"

    # duplicates per category are rejected case-insensitively
    assert client.post("/api/diagram/dictionary/entries", json={
        "category_id": acts, "title": "approve INVOICE", "by": "V",
    }, headers=auth).status_code == 409

    # filtering: category (incl. subcategories), search, A-Z letter
    roles = cats["Roles"]["id"]
    client.post("/api/diagram/dictionary/entries",
                json={"category_id": roles, "title": "AP Clerk"}, headers=auth)
    org = cats["Organizational Units"]["id"]
    by_org = client.get(f"/api/diagram/dictionary?category={org}",
                        headers=auth).json()
    assert [e["title"] for e in by_org["entries"]] == ["AP Clerk"]
    by_letter = client.get("/api/diagram/dictionary?letter=A", headers=auth).json()
    assert {e["title"] for e in by_letter["entries"]} == {"Approve invoice",
                                                          "AP Clerk"}
    by_q = client.get("/api/diagram/dictionary?q=two-eyes", headers=auth).json()
    assert [e["title"] for e in by_q["entries"]] == ["Approve invoice"]

    # update + delete
    updated = client.put(f"/api/diagram/dictionary/entries/{entry['id']}", json={
        "description": "Four-eyes check.", "attributes": {"System": "SAP S/4"},
    }, headers=auth).json()
    assert updated["description"] == "Four-eyes check."
    assert client.delete(f"/api/diagram/dictionary/entries/{entry['id']}",
                         headers=auth).status_code == 200


def test_where_used_usages(client, auth):
    cats = _cats(client, auth)
    # a governed model whose start event is "Start" and a task is "Approve invoice"
    model = client.post("/api/diagram/repo/items", json={
        "name": "Onboarding", "level": "L4", "xml": MINIMAL_BPMN, "author": "V",
    }, headers=auth).json()
    assert "id" in model

    entry = client.post("/api/diagram/dictionary/entries", json={
        "category_id": cats["Activities"]["id"], "title": "Approve invoice",
    }, headers=auth).json()

    # the listing carries a usage_count per entry
    listing = client.get("/api/diagram/dictionary", headers=auth).json()
    approve = next(e for e in listing["entries"] if e["title"] == "Approve invoice")
    assert approve["usage_count"] == 1

    # "Show usages" resolves the referencing model + element type
    usages = client.get(f"/api/diagram/dictionary/entries/{entry['id']}/usages",
                        headers=auth).json()
    assert usages["model_count"] == 1 and usages["usage_count"] == 1
    m = usages["models"][0]
    assert m["model_name"] == "Onboarding"
    assert m["elements"][0]["element_type"] == "Task"

    # a term with no matching element reports zero usages
    unused = client.post("/api/diagram/dictionary/entries", json={
        "category_id": cats["Activities"]["id"], "title": "Never modelled",
    }, headers=auth).json()
    u2 = client.get(f"/api/diagram/dictionary/entries/{unused['id']}/usages",
                    headers=auth).json()
    assert u2["usage_count"] == 0 and u2["models"] == []

    # case-insensitive match, and the start event maps to the Events-style label
    ev = client.post("/api/diagram/dictionary/entries", json={
        "category_id": cats["Events"]["id"], "title": "start",
    }, headers=auth).json()
    su = client.get(f"/api/diagram/dictionary/entries/{ev['id']}/usages",
                    headers=auth).json()
    assert su["usage_count"] == 1
    assert su["models"][0]["elements"][0]["element_type"] == "Start event"


def test_custom_categories_and_merge(client, auth):
    cats = _cats(client, auth)
    custom = client.post("/api/diagram/dictionary/categories",
                         json={"name": "KPIs", "color": "#123456"},
                         headers=auth).json()
    assert client.post("/api/diagram/dictionary/categories",
                       json={"name": "kpis"}, headers=auth).status_code == 409
    # built-ins are protected; customs only deletable when empty
    assert client.delete(
        f"/api/diagram/dictionary/categories/{cats['Activities']['id']}",
        headers=auth).status_code == 400

    a = client.post("/api/diagram/dictionary/entries", json={
        "category_id": custom["id"], "title": "Cycle time",
        "description": "Order to cash duration.",
        "attributes": {"Unit": "days"}}, headers=auth).json()
    b = client.post("/api/diagram/dictionary/entries", json={
        "category_id": custom["id"], "title": "Cycle Time (days)",
        "description": "Median throughput.",
        "attributes": {"Source": "event log"}}, headers=auth).json()
    assert client.delete(f"/api/diagram/dictionary/categories/{custom['id']}",
                         headers=auth).status_code == 400  # not empty

    merged = client.post("/api/diagram/dictionary/merge",
                         json={"ids": [a["id"], b["id"]]}, headers=auth).json()
    assert merged["id"] == a["id"]
    assert "Median throughput." in merged["description"]
    assert merged["attributes"] == {"Source": "event log", "Unit": "days"}
    listing = client.get("/api/diagram/dictionary", headers=auth).json()
    assert all(e["id"] != b["id"] for e in listing["entries"])


def test_bulk_import_excel_and_csv_and_export(client, auth):
    cats = _cats(client, auth)
    systems = cats["IT Systems"]["id"]

    wb = Workbook()
    sheet = wb.active
    sheet.append(["Title", "Description", "Vendor", "Criticality"])
    sheet.append(["SAP ECC", "Core ERP", "SAP", "high"])
    sheet.append(["Coupa", "Procurement", "Coupa", "medium"])
    sheet.append(["", "no title -> error row", "", ""])
    buf = io.BytesIO()
    wb.save(buf)

    r = client.post("/api/diagram/dictionary/import",
                    data={"category_id": systems, "mode": "create_only",
                          "by": "Venkat"},
                    files={"file": ("systems.xlsx", buf.getvalue())},
                    headers=auth).json()
    assert (r["created"], r["updated"], r["skipped"]) == (2, 0, 0)
    assert len(r["errors"]) == 1 and "row 4" in r["errors"][0]

    # create_only skips duplicates; update_existing enriches them (CSV path)
    csv_data = "title,description,Vendor\nSAP ECC,Core ERP system,SAP SE\n"
    again = client.post("/api/diagram/dictionary/import",
                        data={"category_id": systems, "mode": "create_only"},
                        files={"file": ("systems.csv", csv_data.encode())},
                        headers=auth).json()
    assert again["skipped"] == 1 and again["created"] == 0
    upd = client.post("/api/diagram/dictionary/import",
                      data={"category_id": systems, "mode": "update_existing"},
                      files={"file": ("systems.csv", csv_data.encode())},
                      headers=auth).json()
    assert upd["updated"] == 1
    sap = next(e for e in client.get("/api/diagram/dictionary",
                                     headers=auth).json()["entries"]
               if e["title"] == "SAP ECC")
    assert sap["description"] == "Core ERP system"
    assert sap["attributes"]["Vendor"] == "SAP SE"

    # a file without a title header is rejected
    bad = client.post("/api/diagram/dictionary/import",
                      data={"category_id": systems, "mode": "create_only"},
                      files={"file": ("x.csv", b"foo,bar\n1,2\n")},
                      headers=auth)
    assert bad.status_code == 400

    # export round-trips titles and attribute columns
    out = client.get("/api/diagram/dictionary/export", headers=auth)
    assert out.status_code == 200
    sheet = load_workbook(io.BytesIO(out.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:3] == ("title", "description", "category")
    assert {r[0] for r in rows[1:]} == {"SAP ECC", "Coupa"}
