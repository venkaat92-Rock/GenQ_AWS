"""Tests for human-editable activity SQL + auto plain-English explanations."""

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE
from modelling.sql_explain import explain_sql


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "SQL Test"}).json()["api_key"]
    headers = {"X-API-Key": key}
    client.post("/api/sources/demo", headers=headers)
    client.post("/api/mapping/draft", headers=headers)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=headers)
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=headers)
    return headers


def test_every_activity_has_transformation_summary(client, auth):
    spec = client.get("/api/mapping", headers=auth).json()
    for a in spec["activities"]:
        assert a["sql_explanation"], f"{a['name']} has no summary"
        assert a["sql_explanation"].startswith("Source:")
        assert "Case reference:" in a["sql_explanation"]
    approved = next(a for a in spec["activities"] if a["name"] == "PO Approved")
    text = approved["sql_explanation"]
    assert "CDHDR" in text and "TCODE equals 'ME29N'" in text and "UDATE" in text


def test_explainer_reads_joins_and_distinct():
    sql = ("SELECT DISTINCT r.EBELN AS case_id, "
           "COALESCE(TRY_CAST(CAST(h.CPUDT AS VARCHAR) AS TIMESTAMP), "
           "TRY_STRPTIME(CAST(h.CPUDT AS VARCHAR), '%Y%m%d')) AS timestamp "
           "FROM src_rbkp h JOIN src_rseg r ON h.BELNR = r.BELNR")
    text = explain_sql(sql)
    assert "RBKP" in text and "RSEG" in text
    assert "Join: RSEG on BELNR = BELNR" in text
    assert "CPUDT" in text
    assert "Deduplication" in text  # DISTINCT explained


def test_sql_edit_validates_reexplains_and_requires_reconfirm(client, auth):
    bad = client.put(
        "/api/mapping/sql",
        json={"activity": "PO Approved", "sql": "SELECT nonsense FROM nowhere",
              "updated_by": "Venkat"},
        headers=auth,
    )
    assert bad.status_code == 400 and "SQL rejected" in bad.json()["detail"]

    # valid edit: approvals from ME29N *or* ME28 (a plausible customer tweak)
    new_sql = ("SELECT c.OBJECTID AS case_id, "
               "COALESCE(TRY_CAST(CAST(c.UDATE AS VARCHAR) AS TIMESTAMP), "
               "TRY_STRPTIME(CAST(c.UDATE AS VARCHAR), '%Y%m%d')) AS timestamp "
               "FROM src_cdhdr c WHERE c.OBJECTCLAS = 'EINKBELEG' AND c.TCODE = 'ME29N'")
    edited = client.put(
        "/api/mapping/sql",
        json={"activity": "PO Approved", "sql": new_sql, "updated_by": "Venkat"},
        headers=auth,
    ).json()
    assert edited["status"] == "draft"  # confirmed -> draft: must re-confirm
    approved = next(a for a in edited["activities"] if a["name"] == "PO Approved")
    assert approved["sql_edited_by"] == "Venkat"
    assert "TCODE equals 'ME29N'" in approved["sql_explanation"]
    assert "re-confirm" in edited["edit_note"].lower() or "draft" in edited["edit_note"].lower()

    # blocked from materializing until re-confirmed
    assert client.post("/api/dataload/run", headers=auth).status_code == 409
    confirmed = client.post(
        "/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=auth
    ).json()
    assert confirmed["materialized"]["events"] == 107

    # SME name mandatory for edits
    assert client.put(
        "/api/mapping/sql",
        json={"activity": "PO Approved", "sql": new_sql, "updated_by": " "},
        headers=auth,
    ).status_code == 400