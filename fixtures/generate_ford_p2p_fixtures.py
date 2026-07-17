"""Generate a chaotic, enterprise-scale Procure-to-Pay sample for "Ford Motors"
(deterministic, seed 4022).

Unlike fixtures/generate_fixtures.py (20 clean POs, 4 tidy variants, used by the Phase 0/2
equivalence tests — do not touch), this is deliberately messy: the kind of P2P reality a
global automotive OEM actually runs, with a dozen behavior profiles layered on vendor
categories that each have their own speed/rework/exception fingerprint. The point is
variety, not cleanliness — every profile below is a real pattern process miners find in
production SAP P2P data:

  standard_3way        Created -> Approved -> GR -> Invoice -> Payment (the "happy path")
  ers_self_billing     Created -> Approved -> GR -> Payment (no invoice — evaluated
                        receipt settlement; steel/aluminum mills run this way)
  chip_shortage        multiple re-negotiation cycles, 2-4 partial goods receipts, a
                        disputed/reissued invoice, split payments (semiconductor vendors)
  price_dispute        invoice arrives, then sits blocked for weeks before a credit-memo
                        reversal and a corrected invoice clear it (the AP bottleneck)
  maverick_emergency   Created -> GR -> Invoice -> Payment — no approval at all (expedited
                        buy that skipped governance; a real audit finding)
  tooling_custom_fab   2-3 change/re-approve cycles for spec revisions before a long-lead
                        goods receipt (custom dies and fixtures)
  quality_rejection    goods receipt, reject, vendor rework, a second goods receipt
  duplicate_invoice    a duplicate invoice is caught and reversed before payment
  split_payment_run    two payment runs clear one invoice (cash-flow-driven partial pay)
  stuck_cancelled      Created (+ maybe one change) and nothing else — a parked/cancelled PO
  invoice_before_gr    the invoice posts before the goods receipt — a 3-way-match violation,
                        common with freight/logistics vendors

Tables (same SAP shape as fixtures/sap/, so the existing P2P priors draft it unmodified):
EKKO, EKPO, LFA1, CDHDR, MSEG, RBKP, RSEG, BKPF.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "ford_p2p"
FMT = "%Y-%m-%d %H:%M:%S"

# ---- vendor categories: each one is a distinct speed/behavior fingerprint ----------------
#
# approve_days / gr_lag_days: business-day-ish ranges added between steps.
# k_changes: (weights for 0,1,2,3 PO-change/re-approve cycles before the final approval).
# n_gr: (weights for 1,2,3,4 goods-receipt events — partial deliveries).
# ers / dispute / invoice_before_gr / maverick / split_pay / stuck: per-PO probabilities.
# pay_terms: net payment days [min, max] from the last invoice.
# value_range: PO line net value [min, max].

CATEGORIES = {
    "steel_aluminum": {
        "vendors": ["Cleveland-Cliffs Steel", "Novelis Aluminum", "Nucor Steel Dearborn"],
        "approve_days": (0.3, 2), "gr_lag_days": (1, 5),
        "k_changes": [0.75, 0.20, 0.05, 0.0], "n_gr": [0.55, 0.30, 0.12, 0.03],
        "ers": 0.35, "dispute": 0.05, "invoice_before_gr": 0.02, "maverick": 0.02,
        "split_pay": 0.05, "stuck": 0.02, "pay_terms": (30, 45), "value_range": (8000, 120000),
    },
    "semiconductor": {
        "vendors": ["NXP Semiconductors", "Renesas Electronics", "Infineon Technologies",
                    "Bosch Electronics NA"],
        "approve_days": (2, 9), "gr_lag_days": (14, 55),
        "k_changes": [0.15, 0.35, 0.35, 0.15], "n_gr": [0.15, 0.30, 0.35, 0.20],
        "ers": 0.0, "dispute": 0.30, "invoice_before_gr": 0.03, "maverick": 0.12,
        "split_pay": 0.30, "stuck": 0.08, "pay_terms": (45, 90), "value_range": (15000, 380000),
    },
    "tooling_dies": {
        "vendors": ["Riviera Tool LLC", "Eagle Fixture & Gauge", "Tuscarora Tool & Die"],
        "approve_days": (1, 6), "gr_lag_days": (25, 70),
        "k_changes": [0.10, 0.30, 0.35, 0.25], "n_gr": [0.70, 0.22, 0.06, 0.02],
        "ers": 0.0, "dispute": 0.22, "invoice_before_gr": 0.02, "maverick": 0.03,
        "split_pay": 0.15, "stuck": 0.10, "pay_terms": (45, 60), "value_range": (40000, 620000),
    },
    "tier1_interior": {
        "vendors": ["Adient Seating", "Lear Corporation", "Yanfeng Interior Systems"],
        "approve_days": (0.5, 3), "gr_lag_days": (3, 12),
        "k_changes": [0.55, 0.30, 0.12, 0.03], "n_gr": [0.35, 0.35, 0.22, 0.08],
        "ers": 0.05, "dispute": 0.10, "invoice_before_gr": 0.03, "maverick": 0.04,
        "split_pay": 0.08, "stuck": 0.03, "pay_terms": (30, 45), "value_range": (6000, 90000),
    },
    "powertrain": {
        "vendors": ["BorgWarner Inc", "Dana Incorporated", "ZF Powertrain NA"],
        "approve_days": (1, 5), "gr_lag_days": (5, 20),
        "k_changes": [0.45, 0.30, 0.18, 0.07], "n_gr": [0.40, 0.35, 0.18, 0.07],
        "ers": 0.0, "dispute": 0.15, "invoice_before_gr": 0.04, "maverick": 0.15,
        "split_pay": 0.12, "stuck": 0.05, "pay_terms": (30, 60), "value_range": (12000, 260000),
    },
    "logistics_freight": {
        "vendors": ["Ryder Logistics", "Central Transport", "Penske Logistics"],
        "approve_days": (0.2, 1.5), "gr_lag_days": (1, 6),
        "k_changes": [0.85, 0.13, 0.02, 0.0], "n_gr": [0.75, 0.18, 0.05, 0.02],
        "ers": 0.0, "dispute": 0.08, "invoice_before_gr": 0.35, "maverick": 0.06,
        "split_pay": 0.04, "stuck": 0.02, "pay_terms": (30, 45), "value_range": (1500, 35000),
    },
    "mro_facilities": {
        "vendors": ["Grainger Industrial Supply", "Motion Industries",
                    "Applied Industrial Technologies"],
        "approve_days": (0.2, 1.5), "gr_lag_days": (1, 4),
        "k_changes": [0.90, 0.09, 0.01, 0.0], "n_gr": [0.85, 0.12, 0.03, 0.0],
        "ers": 0.10, "dispute": 0.04, "invoice_before_gr": 0.03, "maverick": 0.25,
        "split_pay": 0.03, "stuck": 0.02, "pay_terms": (15, 30), "value_range": (300, 12000),
    },
}

PLANTS = [("1210", "Dearborn Truck Plant"), ("1220", "Chicago Assembly Plant"),
          ("1230", "Kansas City Assembly"), ("1240", "Louisville Assembly"),
          ("1250", "Ohio Assembly Plant")]
BUYERS = ["MJOHNSON", "RPATEL", "KWILLIAMS", "TNGUYEN", "ACARTER", "SBROOKS", "DKOVACS"]
MATERIAL_GROUPS = {
    "steel_aluminum": "RAWMAT", "semiconductor": "ELECTR", "tooling_dies": "TOOLEQ",
    "tier1_interior": "INTRIM", "powertrain": "PWRTRN", "logistics_freight": "SVCLOG",
    "mro_facilities": "MROFAC",
}


def biz(dt: datetime) -> datetime:
    hour = 6 + (dt.hour % 13)  # clamp into a 6am-7pm plant/office window
    return dt.replace(hour=hour, minute=dt.minute % 60, second=0, microsecond=0)


def weighted(rng: random.Random, weights: list[float]) -> int:
    return rng.choices(range(len(weights)), weights=weights, k=1)[0]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):>4} rows -> {path.relative_to(OUT.parent.parent)}")


def main() -> None:
    rng = random.Random(4022)
    OUT.mkdir(exist_ok=True)

    vendor_ids: dict[str, str] = {}
    vendor_category: dict[str, str] = {}
    seq = 0
    for cat, cfg in CATEGORIES.items():
        for name in cfg["vendors"]:
            seq += 1
            vid = f"V{10000 + seq}"
            vendor_ids[name] = vid
            vendor_category[name] = cat

    ekko, ekpo, cdhdr, mseg, rbkp, rseg, bkpf = [], [], [], [], [], [], []
    inv_seq = pay_seq = mat_seq = mvt_seq = 0

    n_pos = 360
    start = datetime(2026, 1, 5, 7, 0)
    span_days = 260  # ~8.5 months of PO creation activity

    for i in range(1, n_pos + 1):
        cat = rng.choice(list(CATEGORIES.keys()))
        cfg = CATEGORIES[cat]
        vendor = rng.choice(cfg["vendors"])
        lifnr = vendor_ids[vendor]
        plant_code, _ = rng.choice(PLANTS)
        buyer = rng.choice(BUYERS)

        ebeln = str(4700000000 + i)
        created = biz(start + timedelta(days=rng.uniform(0, span_days),
                                        hours=rng.uniform(0, 10)))

        ekko.append({"EBELN": ebeln, "BUKRS": "1000", "WERKS": plant_code,
                     "EKGRP": buyer[:3], "LIFNR": lifnr, "AEDAT": created.strftime(FMT),
                     "WAERS": "USD"})

        # PO lines: 1-4 items, value scaled by category
        n_lines = rng.randint(1, 4)
        matkl = MATERIAL_GROUPS[cat]
        line_values = []
        for line in range(1, n_lines + 1):
            mat_seq += 1
            netwr = round(rng.uniform(*cfg["value_range"]) / n_lines, 2)
            line_values.append(netwr)
            ekpo.append({"EBELN": ebeln, "EBELP": f"{line * 10:05d}",
                         "MATNR": f"MAT-{cat[:4].upper()}-{3000 + mat_seq}", "MATKL": matkl,
                         "MENGE": rng.randint(1, 400), "NETWR": netwr})
        po_value = round(sum(line_values), 2)

        stuck = rng.random() < cfg["stuck"]
        maverick = (not stuck) and rng.random() < cfg["maverick"]
        cursor = created

        # -- change / (re-)approval cycles -------------------------------------------------
        k = 0 if (stuck or maverick) else weighted(rng, cfg["k_changes"])
        if stuck:
            # a parked/cancelled PO: at most one change document, then it just dies
            if rng.random() < 0.6:
                cursor = biz(cursor + timedelta(days=rng.uniform(1, 6)))
                cdhdr.append({"OBJECTCLAS": "EINKBELEG", "OBJECTID": ebeln, "TCODE": "ME22N",
                              "UDATE": cursor.strftime(FMT), "USERNAME": buyer})
            continue  # no receipt, no invoice, no payment — a real stuck-case finding

        approved_at = None
        if not maverick:
            for cycle in range(k + 1):
                if cycle > 0:
                    cursor = biz(cursor + timedelta(days=rng.uniform(2, 12)))
                    cdhdr.append({"OBJECTCLAS": "EINKBELEG", "OBJECTID": ebeln,
                                  "TCODE": "ME22N", "UDATE": cursor.strftime(FMT),
                                  "USERNAME": buyer})
                cursor = biz(cursor + timedelta(days=rng.uniform(*cfg["approve_days"])))
                cdhdr.append({"OBJECTCLAS": "EINKBELEG", "OBJECTID": ebeln, "TCODE": "ME29N",
                              "UDATE": cursor.strftime(FMT),
                              "USERNAME": f"APPROVER{rng.randint(1, 4)}"})
                approved_at = cursor

        # -- goods receipt(s) ----------------------------------------------------------------
        n_gr = 1 if maverick else weighted(rng, cfg["n_gr"]) + 1
        gr_base = approved_at or created
        gr_times = []
        for g in range(n_gr):
            lag = rng.uniform(*cfg["gr_lag_days"]) * (0.6 if g == 0 else rng.uniform(0.8, 1.6))
            gr_time = biz(gr_base + timedelta(days=lag + g * rng.uniform(3, 14)))
            gr_times.append(gr_time)
            mvt_seq += 1
            mseg.append({"MBLNR": f"50{6000 + mvt_seq:07d}", "ZEILE": "0001",
                         "BWART": "101", "EBELN": ebeln, "EBELP": "00010",
                         "BUDAT_MKPF": gr_time.strftime(FMT)})
        # quality rejection + rework receipt: an extra 122 (return) + repeat 101
        if not maverick and cat in ("tier1_interior", "powertrain") and rng.random() < 0.10:
            reject_time = biz(gr_times[-1] + timedelta(days=rng.uniform(0.5, 2)))
            mvt_seq += 1
            mseg.append({"MBLNR": f"50{6000 + mvt_seq:07d}", "ZEILE": "0001", "BWART": "122",
                         "EBELN": ebeln, "EBELP": "00010",
                         "BUDAT_MKPF": reject_time.strftime(FMT)})
            rework_gr = biz(reject_time + timedelta(days=rng.uniform(4, 15)))
            gr_times.append(rework_gr)
            mvt_seq += 1
            mseg.append({"MBLNR": f"50{6000 + mvt_seq:07d}", "ZEILE": "0001", "BWART": "101",
                         "EBELN": ebeln, "EBELP": "00010",
                         "BUDAT_MKPF": rework_gr.strftime(FMT)})
        last_gr = max(gr_times)

        # -- invoice ---------------------------------------------------------------------
        # ERS (evaluated receipt settlement) vendors get a near-instant, system-generated
        # invoice right off the goods receipt — no vendor-submitted document, no lag. Every
        # other vendor has a real invoicing lag, which is what makes the GR->Invoice hand-off
        # a visible bottleneck for slow categories (semiconductor, tooling).
        ers = rng.random() < cfg["ers"]
        invoice_before_gr = (not ers) and rng.random() < cfg["invoice_before_gr"]
        if ers:
            inv_time = biz(last_gr + timedelta(minutes=rng.uniform(20, 240)))
        elif invoice_before_gr:
            inv_time = biz(biz(created + timedelta(days=rng.uniform(1, 4)))
                           + timedelta(days=rng.uniform(0.5, 2)))
        else:
            inv_time = biz(last_gr + timedelta(days=rng.uniform(*cfg["gr_lag_days"]) * 0.3
                                               + rng.uniform(0.5, 3)))
        inv_seq += 1
        belnr = f"51{8000 + inv_seq:07d}"
        rbkp.append({"BELNR": belnr, "BLDAT": inv_time.strftime(FMT),
                     "CPUDT": inv_time.strftime(FMT), "LIFNR": lifnr,
                     "RMWWR": po_value, "WAERS": "USD"})
        rseg.append({"BELNR": belnr, "BUZEI": "000001", "EBELN": ebeln,
                     "EBELP": "00010", "WRBTR": po_value})
        last_invoice_time = inv_time
        last_invoice_belnr = belnr

        if (not ers) and rng.random() < cfg["dispute"]:
            # price/qty dispute: blocked for weeks, a credit memo reverses it, then a
            # corrected invoice is submitted — the classic AP bottleneck hand-off
            credit_time = biz(inv_time + timedelta(days=rng.uniform(12, 40)))
            inv_seq += 1
            credit_belnr = f"51{8000 + inv_seq:07d}"
            rbkp.append({"BELNR": credit_belnr, "BLDAT": credit_time.strftime(FMT),
                         "CPUDT": credit_time.strftime(FMT), "LIFNR": lifnr,
                         "RMWWR": -po_value, "WAERS": "USD"})
            rseg.append({"BELNR": credit_belnr, "BUZEI": "000001", "EBELN": ebeln,
                         "EBELP": "00010", "WRBTR": -po_value})
            corrected_time = biz(credit_time + timedelta(days=rng.uniform(3, 10)))
            inv_seq += 1
            corrected_belnr = f"51{8000 + inv_seq:07d}"
            rbkp.append({"BELNR": corrected_belnr, "BLDAT": corrected_time.strftime(FMT),
                         "CPUDT": corrected_time.strftime(FMT), "LIFNR": lifnr,
                         "RMWWR": po_value, "WAERS": "USD"})
            rseg.append({"BELNR": corrected_belnr, "BUZEI": "000001", "EBELN": ebeln,
                         "EBELP": "00010", "WRBTR": po_value})
            last_invoice_time = corrected_time
            last_invoice_belnr = corrected_belnr
        elif (not ers) and cat == "semiconductor" and rng.random() < 0.12:
            # a duplicate invoice submitted and caught before payment
            dup_time = biz(inv_time + timedelta(days=rng.uniform(1, 5)))
            inv_seq += 1
            dup_belnr = f"51{8000 + inv_seq:07d}"
            rbkp.append({"BELNR": dup_belnr, "BLDAT": dup_time.strftime(FMT),
                         "CPUDT": dup_time.strftime(FMT), "LIFNR": lifnr,
                         "RMWWR": -po_value, "WAERS": "USD"})
            rseg.append({"BELNR": dup_belnr, "BUZEI": "000001", "EBELN": ebeln,
                         "EBELP": "00010", "WRBTR": -po_value})
            last_invoice_time = dup_time

        # -- payment -----------------------------------------------------------------------
        terms = rng.uniform(*cfg["pay_terms"])
        split = rng.random() < cfg["split_pay"]
        if split:
            p1 = biz(last_invoice_time + timedelta(days=terms * rng.uniform(0.5, 0.8)))
            pay_seq += 1
            bkpf.append({"BELNR": f"20{9500 + pay_seq:07d}", "BLART": "KZ",
                         "BUDAT": p1.strftime(FMT), "AWKEY": last_invoice_belnr,
                         "BUKRS": "1000"})
            p2 = biz(p1 + timedelta(days=rng.uniform(10, 35)))
            pay_seq += 1
            bkpf.append({"BELNR": f"20{9500 + pay_seq:07d}", "BLART": "KZ",
                         "BUDAT": p2.strftime(FMT), "AWKEY": last_invoice_belnr,
                         "BUKRS": "1000"})
        else:
            pay_time = biz(last_invoice_time + timedelta(days=terms))
            pay_seq += 1
            bkpf.append({"BELNR": f"20{9500 + pay_seq:07d}", "BLART": "KZ",
                         "BUDAT": pay_time.strftime(FMT), "AWKEY": last_invoice_belnr,
                         "BUKRS": "1000"})

    lfa1 = [{"LIFNR": vid, "NAME1": name, "LAND1": "US"}
            for name, vid in vendor_ids.items()]

    write_csv(OUT / "EKKO.csv", ekko)
    write_csv(OUT / "EKPO.csv", ekpo)
    write_csv(OUT / "LFA1.csv", lfa1)
    write_csv(OUT / "CDHDR.csv", cdhdr)
    write_csv(OUT / "MSEG.csv", mseg)
    write_csv(OUT / "RBKP.csv", rbkp)
    write_csv(OUT / "RSEG.csv", rseg)
    write_csv(OUT / "BKPF.csv", bkpf)

    n_cases = len(ekko)
    n_events = (len(ekko) + len(cdhdr) + len(mseg) + len(rbkp) + len(bkpf))
    print(f"\n{n_cases} purchase orders, ~{n_events} raw source events across "
          f"{len(vendor_ids)} vendors in {len(CATEGORIES)} categories.")


if __name__ == "__main__":
    main()
