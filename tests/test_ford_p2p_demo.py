"""Ford Motors chaotic P2P demo: stages, drafts, materializes, and produces the
process complexity the dataset is designed for — many variants, real bottlenecks,
rework, and per-vendor speed variation (spaghetti in the variant explorer, several
insights to investigate on the dashboard)."""

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
    key = client.post("/api/workspaces", json={"name": "Ford Motors"}).json()["api_key"]
    return {"X-API-Key": key}


def test_ford_demo_stages_drafts_and_materializes(client, auth):
    staged = client.post("/api/sources/demo?dataset=ford_p2p", headers=auth).json()
    assert staged["staged"]["EKKO"] == 360
    assert "chip-shortage" in staged["note"]

    spec = client.post("/api/mapping/draft", headers=auth).json()
    qids = [q["id"] for q in spec["open_questions"]]
    assert CASE_GRANULARITY_QID in qids
    # the same five P2P activities the clean fixture drafts — same table shapes, just messier
    assert {a["name"] for a in spec["activities"]} == {
        "PO Created", "PO Approved", "PO Changed", "Goods Receipt",
        "Invoice Received", "Payment Sent",
    }

    spec = client.post("/api/mapping/answers",
                       json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}},
                       headers=auth).json()
    confirmed = client.post("/api/mapping/confirm", json={"reviewer": "Venkat"},
                            headers=auth).json()
    cases = confirmed["materialized"]["cases"]
    events = confirmed["materialized"]["events"]
    assert cases == 360
    assert events > 2000  # far more than one event per activity per case


def test_ford_demo_is_genuinely_chaotic(client, auth):
    """The point of this dataset: many distinct paths and a real bottleneck to chase,
    not a tidy 3-4-variant textbook process."""
    client.post("/api/sources/demo?dataset=ford_p2p", headers=auth)
    client.post("/api/mapping/draft", headers=auth)
    client.post("/api/mapping/answers",
               json={"answers": {CASE_GRANULARITY_QID: PO_HEADER_CASE}}, headers=auth)
    client.post("/api/mapping/confirm", json={"reviewer": "Venkat"}, headers=auth)

    variants = client.get("/api/analysis/variants", headers=auth).json()
    # "spaghetti": dozens of distinct paths, no single variant dominating the process
    assert len(variants) >= 30
    assert variants[0]["share"] < 0.25

    rework = client.get("/api/analysis/rework", headers=auth).json()
    assert rework["rework_share"] > 0.2  # PO changes, partial GRs, disputes, duplicates

    bottlenecks = client.get("/api/analysis/bottlenecks", headers=auth).json()
    assert len(bottlenecks["edges"]) >= 3
    assert bottlenecks["edges"][0]["mean_hours"] > 24  # a real multi-day hand-off delay

    insights = client.get("/api/analysis/insights", headers=auth).json()["insights"]
    titles = {i["title"] for i in insights}
    assert "Biggest bottleneck" in titles
    assert "Vendor with the slowest cycle" in titles
    # the slow-cycle vendor comparison should be dramatic (chip-shortage vs steel mills)
    slow_vendor = next(i for i in insights if i["title"] == "Vendor with the slowest cycle")
    assert "x" in slow_vendor["detail"]  # e.g. "...(4.2x faster-vendor at ...)"
