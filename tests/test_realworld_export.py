"""Regression tests for realistic customer exports (2026-07-05 bug report).

The first real upload used descriptive table names (EKKO_PO_HEADER, ...), SAP YYYYMMDD
integer dates, approvals as EKKO.FRGDAT (no CDHDR), a merged MKPF_MSEG goods-receipt
extract, payments in BSAK, and an all-empty column (ZLSPR) whose NaN samples crashed
strict-JSON serialization of /api/sources.
"""

import json
from pathlib import Path

import pytest

from engine.db import EventLogStore
from modelling.agent import ModellingAgent
from modelling.materialize import materialize
from modelling.priors import CASE_GRANULARITY_QID, PO_HEADER_CASE

TABLES = {
    "ekko_po_header": [
        "EBELN,BUKRS,LIFNR,AEDAT,FRGKE,FRGDAT,NETWR",
        "4500000001,1000,V100,20250106,X,20250108,1200.50",
        "4500000002,1000,V101,20250110,X,20250113,860.00",
    ],
    "ekpo_po_item": [
        "EBELN,EBELP,MATNR,MENGE,NETWR",
        "4500000001,00010,MAT-1,5,700.50",
        "4500000001,00020,MAT-2,2,500.00",
        "4500000002,00010,MAT-3,1,860.00",
    ],
    "lfa1_vendor_master": [
        "LIFNR,NAME1,LAND1",
        "V100,Sunrise Metals,US",
        "V101,Blue Logistics,US",
    ],
    "mkpf_mseg_goods_receipt": [
        "MBLNR,BUDAT,BWART,EBELN,EBELP,MENGE",
        "5000001,20250110,101,4500000001,00010,5",
        "5000002,20250114,101,4500000002,00010,1",
    ],
    # ZLSPR is entirely empty -> becomes an all-NaN column after CSV sniffing
    "rbkp_invoice_header": [
        "BELNR,GJAHR,LIFNR,BLDAT,CPUDT,ZLSPR",
        "5100000001,2025,V100,20250111,20250112,",
        "5100000002,2025,V101,20250115,20250116,",
    ],
    "rseg_invoice_item": [
        "BELNR,BUZEI,EBELN,EBELP,WRBTR",
        "5100000001,000001,4500000001,00010,1200.50",
        "5100000002,000001,4500000002,00010,860.00",
    ],
    "bsak_vendor_items": [
        "BELNR,GJAHR,LIFNR,BUDAT,AUGDT,AUGBL",
        "5100000001,2025,V100,20250112,20250115,2000000001",
        "5100000002,2025,V101,20250116,20250118,2000000002",
    ],
}

EXPECTED_ACTIVITIES = {
    "PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent",
}


@pytest.fixture()
def staged_store(tmp_path):
    with EventLogStore() as store:
        for name, lines in TABLES.items():
            csv_path = Path(tmp_path) / f"{name}.csv"
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            store.stage_csv(name, csv_path)
        yield store


def test_schema_summary_is_strict_json_safe(staged_store):
    summary = staged_store.schema_summary()
    # Starlette serializes with allow_nan=False — this must not raise
    json.dumps(summary, allow_nan=False)
    zlspr = [
        row["ZLSPR"] for row in summary["RBKP_INVOICE_HEADER"]["sample"]
    ]
    assert all(v is None for v in zlspr)


def test_priors_adapt_to_renamed_and_alternative_tables(staged_store):
    spec = ModellingAgent(staged_store).draft()
    by_name = {a.name: a for a in spec.activities}
    assert set(by_name) == EXPECTED_ACTIVITIES
    # approval falls back to the release date on the PO header
    assert by_name["PO Approved"].source_table == "EKKO_PO_HEADER"
    assert "FRGDAT" in by_name["PO Approved"].timestamp
    # payments come from BSAK cleared items, clearing date preferred
    assert "BSAK" in by_name["Payment Sent"].source_table
    assert "AUGDT" in by_name["Payment Sent"].timestamp
    # granularity is still escalated for the renamed EKPO table
    assert any(q.id == CASE_GRANULARITY_QID for q in spec.open_questions)
    assert {a.name for a in spec.attributes} == {"vendor", "net_value"}


def test_yyyymmdd_dates_materialize(staged_store):
    agent = ModellingAgent(staged_store)
    spec = agent.draft()
    spec.answer_question(CASE_GRANULARITY_QID, PO_HEADER_CASE)
    spec = agent.redraft_with_answers(spec)
    spec.confirm("Venkat")
    result = materialize(staged_store, spec)
    # 2 POs x 5 activities, every YYYYMMDD int parsed (none dropped as NULL)
    assert result == {"events": 10, "cases": 2, "activities": sorted(EXPECTED_ACTIVITIES)}
    df = staged_store.fetch_event_log()
    assert str(df["timestamp"].min())[:10] == "2025-01-06"
