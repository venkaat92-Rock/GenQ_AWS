"""Generate a realistic Order-to-Cash sample dataset (deterministic, seed 2026).

80 sales orders for "Northwind Trading" over Mar-May 2026, with the messiness a real
order-management process has:

  44  standard:        Order -> Delivery -> Goods Issue -> Invoice -> Payment
  10  credit hold:     Order -> Order Changed (hold released) -> standard chain
   8  partial:         two delivery/GI/invoice waves, one payment at the end
   7  open AR:         invoiced but not yet paid (open receivables)
   5  stuck orders:    Order -> Order Changed, then nothing (cancelled/parked)
   6  slow fulfilment: delivery starts 7-14 days after order entry

Customers have payment-behavior profiles (fast/normal/slow payers) so the dashboards
show a real DSO story. Tables: VBAK, VBAP, KNA1, CDHDR, LIKP, LIPS, VBRK, VBRP, BSAD.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "o2c"
FMT = "%Y-%m-%d %H:%M:%S"

CUSTOMERS = [
    # (KUNNR, name, city, pay_min_days, pay_max_days)
    ("C1001", "Alpine Retail Group", "Munich", 10, 18),
    ("C1002", "Borealis Wholesale", "Oslo", 15, 28),
    ("C1003", "Cypress Distribution", "Austin", 20, 35),
    ("C1004", "Delta Marts", "Chicago", 25, 40),
    ("C1005", "Evergreen Superstores", "Seattle", 45, 65),   # the slow payer
    ("C1006", "Fjord Trading Co", "Bergen", 12, 22),
]

MATERIALS = ["FG-1001", "FG-1002", "FG-2040", "FG-2041", "FG-3300", "FG-4100", "FG-5210"]


def biz(dt: datetime) -> datetime:
    """Clamp into business-ish hours."""
    hour = 8 + (dt.hour % 10)
    return dt.replace(hour=hour, minute=dt.minute, second=0)


def main() -> None:
    rng = random.Random(2026)
    OUT.mkdir(exist_ok=True)

    vbak, vbap, cdhdr, likp, lips, vbrk, vbrp, bsad = [], [], [], [], [], [], [], []
    delivery_seq = invoice_seq = clearing_seq = 0

    profiles = (
        ["standard"] * 44 + ["credit_hold"] * 10 + ["partial"] * 8
        + ["open_ar"] * 7 + ["stuck"] * 5 + ["slow_fulfilment"] * 6
    )
    rng.shuffle(profiles)

    start = datetime(2026, 3, 2, 9, 0)

    for i, profile in enumerate(profiles, start=1):
        order_no = f"{900000000 + i}"
        customer = rng.choice(CUSTOMERS)
        kunnr, _, _, pay_min, pay_max = customer
        created = biz(start + timedelta(days=rng.uniform(0, 75)))

        vbak.append({"VBELN": order_no, "AUART": "OR", "ERDAT": created.strftime(FMT),
                     "KUNNR": kunnr, "VKORG": "1000", "WAERK": "USD"})

        n_lines = rng.randint(1, 4)
        order_value = 0.0
        for line in range(1, n_lines + 1):
            netwr = round(rng.uniform(400, 18000), 2)
            order_value += netwr
            vbap.append({"VBELN": order_no, "POSNR": f"{line * 10:06d}",
                         "MATNR": rng.choice(MATERIALS), "KWMENG": rng.randint(1, 120),
                         "NETWR": netwr})

        cursor = created

        if profile in ("credit_hold", "stuck"):
            cursor = biz(cursor + timedelta(days=rng.uniform(1, 3)))
            cdhdr.append({"OBJECTCLAS": "VERKBELEG", "OBJECTID": order_no,
                          "TCODE": "VA02", "UDATE": cursor.strftime(FMT),
                          "USERNAME": f"CSR{rng.randint(1, 4)}"})
            if profile == "stuck":
                continue  # parked/cancelled: no fulfilment, no billing

        fulfilment_lag = (
            rng.uniform(7, 14) if profile == "slow_fulfilment" else rng.uniform(1, 4)
        )
        waves = 2 if profile == "partial" else 1
        last_invoice_date = None
        invoice_numbers = []

        for wave in range(waves):
            delivery_seq += 1
            dlv_no = f"{800000000 + delivery_seq}"
            dlv_created = biz(cursor + timedelta(days=fulfilment_lag + wave * rng.uniform(3, 7)))
            goods_issue = biz(dlv_created + timedelta(days=rng.uniform(0.2, 1.5)))
            likp.append({"VBELN": dlv_no, "ERDAT": dlv_created.strftime(FMT),
                         "WADAT_IST": goods_issue.strftime(FMT), "VSTEL": "W001"})
            lips.append({"VBELN": dlv_no, "POSNR": "000010", "VGBEL": order_no,
                         "VGPOS": "000010", "LFIMG": rng.randint(1, 120)})

            invoice_seq += 1
            inv_no = f"{950000000 + invoice_seq}"
            inv_date = biz(goods_issue + timedelta(days=rng.uniform(0.3, 2)))
            share = 1.0 / waves
            vbrk.append({"VBELN": inv_no, "FKART": "F2", "FKDAT": inv_date.strftime(FMT),
                         "NETWR": round(order_value * share, 2), "WAERK": "USD"})
            vbrp.append({"VBELN": inv_no, "POSNR": "000010", "AUBEL": order_no,
                         "AUPOS": "000010", "NETWR": round(order_value * share, 2)})
            invoice_numbers.append(inv_no)
            last_invoice_date = inv_date

        if profile != "open_ar" and last_invoice_date is not None:
            pay_date = biz(last_invoice_date + timedelta(days=rng.uniform(pay_min, pay_max)))
            for inv_no in invoice_numbers:
                clearing_seq += 1
                bsad.append({"BELNR": inv_no, "KUNNR": kunnr, "BUKRS": "1000",
                             "BUDAT": last_invoice_date.strftime(FMT),
                             "AUGDT": pay_date.strftime(FMT),
                             "AUGBL": f"{140000000 + clearing_seq}",
                             "DMBTR": round(order_value / len(invoice_numbers), 2)})

    kna1 = [{"KUNNR": c[0], "NAME1": c[1], "ORT01": c[2], "LAND1": "US"} for c in CUSTOMERS]

    for name, rows in [("VBAK", vbak), ("VBAP", vbap), ("KNA1", kna1), ("CDHDR", cdhdr),
                       ("LIKP", likp), ("LIPS", lips), ("VBRK", vbrk), ("VBRP", vbrp),
                       ("BSAD", bsad)]:
        path = OUT / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows):>3} rows -> fixtures/o2c/{name}.csv")


if __name__ == "__main__":
    main()
