"""KPI builder: series computation (group_by / trend), preview + options endpoints,
and credential-first connector metadata."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from mining.kpis import compute_kpi_series


def _log():
    rows = []
    for i, (region, days) in enumerate([("EU", 2), ("EU", 4), ("US", 10),
                                        ("US", 12), ("APAC", 6)]):
        case = f"c{i}"
        rows.append({"case_id": case, "activity": "Order Created",
                     "timestamp": f"2026-0{(i % 3) + 1}-01", "region": region})
        rows.append({"case_id": case, "activity": "Payment Received",
                     "timestamp": pd.Timestamp(f"2026-0{(i % 3) + 1}-01")
                     + pd.Timedelta(days=days), "region": region})
    return pd.DataFrame(rows)


def test_series_by_attribute_and_trend():
    df = _log()
    by_region = compute_kpi_series(df, {"kind": "cycle_time", "agg": "mean",
                                        "group_by": "region"})
    values = {p["label"]: p["value"] for p in by_region["series"]}
    assert values == {"US": 11.0, "APAC": 6.0, "EU": 3.0}
    assert by_region["unit"] == "days"
    # sorted descending for bar charts
    assert [p["label"] for p in by_region["series"]] == ["US", "APAC", "EU"]

    monthly = compute_kpi_series(df, {"kind": "case_count", "trend": "month"})
    assert [p["label"] for p in monthly["series"]] == ["2026-01", "2026-02", "2026-03"]
    assert sum(p["value"] for p in monthly["series"]) == 5

    with pytest.raises(ValueError):
        compute_kpi_series(df, {"kind": "case_count", "group_by": "nope"})
    with pytest.raises(ValueError):
        compute_kpi_series(df, {"kind": "case_count"})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE

    key = client.post("/api/workspaces", json={"name": "KPI Test"}).json()["api_key"]
    headers = {"X-API-Key": key}
    client.post("/api/sources/demo", headers=headers)  # SAP P2P fixture
    client.post("/api/mapping/draft", headers=headers)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}},
                headers=headers)
    client.post("/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=headers)
    return headers


def test_options_preview_and_chart_kpis(client, auth):
    options = client.get("/api/kpis/options", headers=auth).json()
    assert "PO Created" in options["activities"]
    assert len(options["attributes"]) > 0
    attr = options["attributes"][0]

    # preview a bar-chart KPI without saving
    preview = client.post("/api/kpis/preview", json={
        "name": "x", "definition": {"kind": "case_count", "group_by": attr,
                                    "viz": "bar"},
    }, headers=auth).json()
    assert len(preview["series"]) > 0
    assert client.get("/api/kpis", headers=auth).json() == []

    # save a trend KPI; the listing carries its series + viz for rendering
    saved = client.post("/api/kpis", json={
        "name": "Cases per month",
        "definition": {"kind": "case_count", "trend": "month", "viz": "line"},
    }, headers=auth).json()
    assert len(saved["series"]) >= 1
    listed = client.get("/api/kpis", headers=auth).json()
    assert listed[0]["definition"]["viz"] == "line"
    assert len(listed[0]["series"]) >= 1

    # invalid breakdowns are rejected up front
    assert client.post("/api/kpis/preview", json={
        "name": "x", "definition": {"kind": "case_count", "group_by": "bogus"},
    }, headers=auth).status_code == 400


def test_catalog_connectors_carry_live_connection_forms(client, auth):
    listing = client.get("/api/connectors", headers=auth).json()["connectors"]
    assert all(c.get("live_kind") for c in listing)
    detail = client.get("/api/connectors/sap-p2p", headers=auth).json()
    live = detail["live"]
    assert live["kind"] == "odata"
    assert live["defaults"]["entities"][0] == "EKKO"
    keys = {f["key"] for f in live["fields"]}
    assert {"service_url", "user", "password", "entities"} <= keys
    snow = client.get("/api/connectors/servicenow-incident", headers=auth).json()
    assert snow["live"]["kind"] == "servicenow"
    assert "sys_audit" in snow["live"]["defaults"]["tables"]
