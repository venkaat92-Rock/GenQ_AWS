"""Generate ALL synthetic fixtures (deterministic, seeded).

Produces:
  1. p2p_event_log.csv           — flat reference event log (20 cases / 107 events).
     Uses the exact RNG draw order of the original generator (seed 42) so the file
     stays byte-identical to the Phase 0 fixture.
  2. sap/*.csv                   — SAP-style P2P source tables consistent with that log:
     EKKO (PO header), EKPO (PO lines), LFA1 (vendor master), CDHDR (change docs:
     approvals + changes), MSEG (goods movements), RBKP/RSEG (invoices), BKPF (payments).

The source tables are what the L2 modelling agent reads; materializing its confirmed
mapping over them must reproduce the flat event log exactly — that equivalence is the
key Phase 2 test.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SAP_DIR = HERE / "sap"

VARIANTS = [
    (["PO Created", "PO Approved", "Goods Receipt", "Invoice Received", "Payment Sent"], 12),
    (["PO Created", "PO Approved", "PO Changed", "PO Approved", "Goods Receipt",
      "Invoice Received", "Payment Sent"], 4),
    (["PO Created", "PO Approved", "Invoice Received", "Goods Receipt", "Payment Sent"], 3),
    (["PO Created", "Goods Receipt", "Invoice Received", "Payment Sent"], 1),
]

VENDORS = ["Acme Industrial", "Baltic Steel", "Corex Supplies", "Delta Logistics"]
VENDOR_IDS = {name: f"V{1000 + i}" for i, name in enumerate(VENDORS)}
FMT = "%Y-%m-%d %H:%M:%S"


def build_cases() -> list[dict]:
    """Reproduce the Phase 0 event streams draw-for-draw (seed 42 — do not reorder)."""
    rng = random.Random(42)
    base = datetime(2026, 1, 5, 8, 0)
    cases = []
    case_num = 0
    for activities, n_cases in VARIANTS:
        for _ in range(n_cases):
            case_num += 1
            case_id = f"PO-{4500000000 + case_num}"
            vendor = rng.choice(VENDORS)
            net_value = round(rng.uniform(500, 25000), 2)
            ts = base + timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 8))
            events = []
            for activity in activities:
                ts += timedelta(days=rng.randint(0, 4), hours=rng.randint(1, 10),
                                minutes=rng.randint(0, 59))
                events.append((activity, ts))
            cases.append(
                {"case_id": case_id, "vendor": vendor, "net_value": net_value, "events": events}
            )
    return cases


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):>3} rows -> {path.relative_to(HERE.parent)}")


def write_event_log(cases: list[dict]) -> None:
    rows = [
        {
            "case_id": c["case_id"],
            "activity": activity,
            "timestamp": ts.strftime(FMT),
            "vendor": c["vendor"],
            "net_value": c["net_value"],
        }
        for c in cases
        for activity, ts in c["events"]
    ]
    write_csv(HERE / "p2p_event_log.csv", rows)


def write_sap_tables(cases: list[dict]) -> None:
    # Source-only attributes come from a second RNG so the event-log draws stay untouched.
    rng2 = random.Random(7)
    ekko, ekpo, cdhdr, mseg, rbkp, rseg, bkpf = [], [], [], [], [], [], []
    inv_seq = pay_seq = mat_seq = 0

    for c in cases:
        ebeln = c["case_id"].replace("PO-", "")
        lifnr = VENDOR_IDS[c["vendor"]]

        # PO lines: split net_value across 1-3 items (last line takes the remainder)
        n_lines = rng2.randint(1, 3)
        remaining = c["net_value"]
        for line in range(1, n_lines + 1):
            mat_seq += 1
            if line < n_lines:
                netwr = round(remaining * rng2.uniform(0.2, 0.6), 2)
                remaining = round(remaining - netwr, 2)
            else:
                netwr = remaining
            ekpo.append(
                {"EBELN": ebeln, "EBELP": f"{line * 10:05d}", "MATNR": f"MAT-{2000 + mat_seq}",
                 "MENGE": rng2.randint(1, 50), "NETWR": netwr}
            )

        for activity, ts in c["events"]:
            stamp = ts.strftime(FMT)
            if activity == "PO Created":
                ekko.append({"EBELN": ebeln, "BUKRS": "1000", "LIFNR": lifnr,
                             "AEDAT": stamp, "WAERS": "USD"})
            elif activity == "PO Approved":
                cdhdr.append({"OBJECTCLAS": "EINKBELEG", "OBJECTID": ebeln, "TCODE": "ME29N",
                              "UDATE": stamp, "USERNAME": f"APPROVER{rng2.randint(1, 3)}"})
            elif activity == "PO Changed":
                cdhdr.append({"OBJECTCLAS": "EINKBELEG", "OBJECTID": ebeln, "TCODE": "ME22N",
                              "UDATE": stamp, "USERNAME": f"BUYER{rng2.randint(1, 5)}"})
            elif activity == "Goods Receipt":
                mseg.append({"MBLNR": f"50{5000 + len(mseg):06d}", "ZEILE": "0001",
                             "BWART": "101", "EBELN": ebeln, "EBELP": "00010",
                             "BUDAT_MKPF": stamp})
            elif activity == "Invoice Received":
                inv_seq += 1
                belnr = f"51{7000 + inv_seq:06d}"
                rbkp.append({"BELNR": belnr, "BLDAT": stamp, "CPUDT": stamp, "LIFNR": lifnr,
                             "RMWWR": c["net_value"], "WAERS": "USD"})
                rseg.append({"BELNR": belnr, "BUZEI": "000001", "EBELN": ebeln,
                             "EBELP": "00010", "WRBTR": c["net_value"]})
            elif activity == "Payment Sent":
                pay_seq += 1
                # AWKEY links the payment doc back to the invoice it clears
                bkpf.append({"BELNR": f"20{9000 + pay_seq:06d}", "BLART": "KZ",
                             "BUDAT": stamp, "AWKEY": rbkp[-1]["BELNR"], "BUKRS": "1000"})

    lfa1 = [{"LIFNR": VENDOR_IDS[n], "NAME1": n, "LAND1": "US"} for n in VENDORS]

    write_csv(SAP_DIR / "EKKO.csv", ekko)
    write_csv(SAP_DIR / "EKPO.csv", ekpo)
    write_csv(SAP_DIR / "LFA1.csv", lfa1)
    write_csv(SAP_DIR / "CDHDR.csv", cdhdr)
    write_csv(SAP_DIR / "MSEG.csv", mseg)
    write_csv(SAP_DIR / "RBKP.csv", rbkp)
    write_csv(SAP_DIR / "RSEG.csv", rseg)
    write_csv(SAP_DIR / "BKPF.csv", bkpf)


def main() -> None:
    cases = build_cases()
    write_event_log(cases)
    write_sap_tables(cases)


if __name__ == "__main__":
    main()
