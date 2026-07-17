"""Order-to-Cash tests: auto-detection, O2C priors, end-to-end materialization,
and party-agnostic (customer) analytics."""

import pytest
from fastapi.testclient import TestClient

from modelling.priors import CASE_GRANULARITY_QID, SO_HEADER_CASE

EXPECTED_ACTIVITIES = {
    "Order Created", "Order Changed", "Delivery Created", "Goods Issued",
    "Invoice Created", "Payment Received",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "Northwind"}).json()["api_key"]
    headers = {"X-API-Key": key}
    client.post("/api/sources/demo?dataset=o2c", headers=headers)
    return headers


def test_agent_autodetects_order_to_cash(client, auth):
    spec = client.post("/api/mapping/draft", headers=auth).json()
    assert spec["process"] == "order_to_cash"
    assert {a["name"] for a in spec["activities"]} == EXPECTED_ACTIVITIES
    assert {a["name"] for a in spec["attributes"]} == {"customer", "net_value"}
    qids = [q["id"] for q in spec["open_questions"]]
    assert CASE_GRANULARITY_QID in qids
    # order vs order-line phrased for sales, not purchasing
    q = next(q for q in spec["open_questions"] if q["id"] == CASE_GRANULARITY_QID)
    assert "sales order" in q["question"].lower()
    for a in spec["activities"]:
        assert a["sql_explanation"]


def test_o2c_end_to_end_and_customer_analytics(client, auth):
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
                json={"answers": {CASE_GRANULARITY_QID: SO_HEADER_CASE}}, headers=auth)
    confirmed = client.post(
        "/api/mapping/confirm", json={"reviewer": "Ops Manager"}, headers=auth
    ).json()
    assert confirmed["materialized"]["cases"] == 80
    assert set(confirmed["materialized"]["activities"]) == EXPECTED_ACTIVITIES

    # facets speak "customer", not "vendor"
    facets = client.get("/api/analysis/facets", headers=auth).json()
    assert facets["party"] == "customer"
    assert "Evergreen Superstores" in facets["vendors"]

    # party filter works on customers
    filtered = client.get(
        "/api/analysis/summary", params={"vendors": "Evergreen Superstores"}, headers=auth
    ).json()
    assert 0 < filtered["cases"] < 80
    assert any("customer in" in f for f in filtered["filters_applied"])

    # the dominant variant is the standard O2C flow
    variants = client.get("/api/analysis/variants", headers=auth).json()
    assert variants[0]["activities"] == [
        "Order Created", "Delivery Created", "Goods Issued",
        "Invoice Created", "Payment Received",
    ]

    # insights talk about customers
    insights = client.get("/api/analysis/insights", headers=auth).json()["insights"]
    assert any("Customer" in i["title"] for i in insights)

    # NL layer scopes to a named customer
    answer = client.post(
        "/api/ask",
        json={"question": "How long does Evergreen Superstores take end to end?"},
        headers=auth,
    ).json()
    assert answer["intent"] == "throughput"
    assert any("Evergreen Superstores" in f for f in answer["citations"]["filters"])

    # copilot KPI for the cash-collection lag
    kpi = client.post(
        "/api/copilot",
        json={"question": "create a KPI: average days from Invoice Created to Payment Received"},
        headers=auth,
    ).json()
    assert kpi["engine"] == "kpi-parser"
    assert kpi["kpi_proposal"]["definition"]["from_activity"] == "Invoice Created"
    assert kpi["kpi_proposal"]["preview"]["value"] > 10  # realistic DSO-ish lag