"""Regression tests for the 2026-07-05 adversarial-review findings (data model / KPIs)."""

import pytest
from fastapi.testclient import TestClient

from mining.kpis import compute_kpi, parse_kpi_request
from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE

ACTIVITIES = ["PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Reg Test"}).json()["api_key"]
    return {"X-API-Key": key}


def _stage_and_confirm(client, auth):
    client.post("/api/sources/demo", headers=auth)
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=auth)


# ---- finding 1: NL KPI parser misrouting ----------------------------------------------


def test_kpi_parser_not_hijacked_by_substrings():
    d = parse_kpi_request(
        "Create a KPI: median days from PO Created to Goods Receipt for accounts payable",
        ACTIVITIES,
    )
    assert d["kind"] == "cycle_time"
    assert d["from_activity"] == "PO Created" and d["to_activity"] == "Goods Receipt"

    d = parse_kpi_request(
        "Create a KPI: average days from PO Created to Payment Sent for orders with high spend",
        ACTIVITIES,
    )
    assert d["kind"] == "cycle_time" and d["agg"] == "mean"

    d = parse_kpi_request("Create a KPI: share of cases with discounts applied", ACTIVITIES)
    assert d is None or d["kind"] != "case_count"


# ---- findings 3/7/8: attribute_agg validation + NaN safety ----------------------------


def test_attribute_agg_bad_agg_is_400_not_500(client, auth):
    _stage_and_confirm(client, auth)
    response = client.post(
        "/api/kpis",
        json={"name": "x", "definition": {"kind": "attribute_agg",
                                          "attribute": "net_value", "agg": "p90"}},
        headers=auth,
    )
    assert response.status_code == 400


def test_attribute_agg_empty_slice_is_json_safe():
    import pandas as pd

    df = pd.DataFrame(
        {"case_id": ["a"], "activity": ["X"], "timestamp": ["2026-01-01 00:00:00"],
         "net_value": [float("nan")]}
    )
    result = compute_kpi(df, {"kind": "attribute_agg", "attribute": "net_value",
                              "agg": "mean"})
    assert result["value"] == 0.0 and result["cases_measured"] == 0


# ---- finding 4: transition_wait counts cases, not transitions --------------------------


def test_transition_wait_counts_distinct_cases():
    import pandas as pd

    rows = [
        ("A", "X", "2026-01-01 00:00"), ("A", "Y", "2026-01-02 00:00"),
        ("A", "X", "2026-01-03 00:00"), ("A", "Y", "2026-01-05 00:00"),
        ("B", "X", "2026-01-01 00:00"), ("B", "Y", "2026-01-03 00:00"),
    ]
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp"])
    result = compute_kpi(df, {"kind": "transition_wait", "from_activity": "X",
                              "to_activity": "Y", "agg": "mean"})
    assert result["cases_measured"] == 2  # 3 transitions but 2 cases


# ---- finding 2: premature GET must not freeze the data model ---------------------------


def test_datamodel_refreshes_after_confirm_and_new_tables(client, auth):
    client.post("/api/sources/demo", headers=auth)
    early = client.get("/api/datamodel", headers=auth).json()  # BEFORE any mapping
    assert not any("EVENT_LOG" in (r["from_table"], r["to_table"])
                   for r in early["relationships"])

    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    client.post("/api/mapping/confirm", json={"reviewer": "SME"}, headers=auth)

    after = client.get("/api/datamodel", headers=auth).json()
    assert any("EVENT_LOG" in (r["from_table"], r["to_table"])
               for r in after["relationships"])
    assert any(r["via"] == "used by the mapping SQL" for r in after["relationships"])

    # a table staged later gets identifier-probed on the next GET
    csv = b"DOCNO,NOTE\nD1,a\nD2,b\n"
    client.post("/api/sources/upload",
                files={"file": ("late_table.csv", csv, "text/csv")}, headers=auth)
    latest = client.get("/api/datamodel", headers=auth).json()
    late = next(t for t in latest["tables"] if t["name"] == "LATE_TABLE")
    assert late["identifier"] == "DOCNO"

    # human edits survive the re-derive triggered by yet another schema change
    rels = latest["relationships"]
    added = {"from_table": "EKKO", "from_column": "WAERS",
             "to_table": "RBKP", "to_column": "WAERS"}
    client.put("/api/datamodel",
               json={"relationships": rels + [added], "updated_by": "Venkat"}, headers=auth)
    client.post("/api/sources/upload",
                files={"file": ("late2.csv", csv, "text/csv")}, headers=auth)
    merged = client.get("/api/datamodel", headers=auth).json()
    assert any(r["from_column"] == "WAERS" and r["origin"] == "human"
               for r in merged["relationships"])


def test_datamodel_human_deletions_survive_rederive_merge(client, auth):
    _stage_and_confirm(client, auth)
    model = client.get("/api/datamodel", headers=auth).json()
    rels = model["relationships"]
    victim = next(r for r in rels if r["origin"] == "agent")
    remaining = [r for r in rels if r is not victim]
    client.put("/api/datamodel",
               json={"relationships": remaining, "updated_by": "Venkat"}, headers=auth)
    # schema change triggers a merge re-derive; the deleted agent rel must not resurface
    csv = b"K,V\n1,a\n2,b\n"
    client.post("/api/sources/upload",
                files={"file": ("kv.csv", csv, "text/csv")}, headers=auth)
    merged = client.get("/api/datamodel", headers=auth).json()
    keys = {(r["from_table"], r["from_column"], r["to_table"], r["to_column"])
            for r in merged["relationships"]}
    assert (victim["from_table"], victim["from_column"],
            victim["to_table"], victim["to_column"]) not in keys


# ---- finding 10: malformed relationship payloads are 400, and don't poison the file ----


def test_datamodel_rejects_malformed_relationships(client, auth):
    _stage_and_confirm(client, auth)
    bad = client.put(
        "/api/datamodel",
        json={"relationships": [{"from_table": "EKKO"}], "updated_by": "V"},
        headers=auth,
    )
    assert bad.status_code == 400
    # the model file is not poisoned — subsequent edits still work
    model = client.get("/api/datamodel", headers=auth).json()
    ok = client.put(
        "/api/datamodel",
        json={"relationships": model["relationships"], "updated_by": "V"},
        headers=auth,
    )
    assert ok.status_code == 200


# ---- finding 9: malformed filter dates are 400 -----------------------------------------


def test_malformed_filter_dates_are_400(client, auth):
    _stage_and_confirm(client, auth)
    response = client.get("/api/analysis/summary",
                          params={"start_from": "not-a-date"}, headers=auth)
    assert response.status_code == 400
    assert "start_from" in response.json()["detail"]


# ---- finding 15: upload returns the staged (normalized) table name ---------------------


def test_upload_returns_normalized_table_name(client, auth):
    csv = b"A,B\n1,2\n"
    response = client.post(
        "/api/sources/upload",
        files={"file": ("My Table-Name.csv", csv, "text/csv")},
        headers=auth,
    ).json()
    assert response["table"] == "my_table_name"
    assert response["table"].upper() in client.get("/api/sources", headers=auth).json()