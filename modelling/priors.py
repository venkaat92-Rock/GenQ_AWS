"""L2 — SAP P2P domain priors: the deterministic draft the agent starts from.

Standard SAP P2P has known table patterns (CLAUDE.md §4: "state priors, then check them"),
but real exports rarely match them verbatim: tables arrive as EKKO_PO_HEADER, goods
receipts as a merged MKPF_MSEG extract, approvals as a release date on the PO header
instead of change documents, payments as BSAK cleared items. This module therefore
resolves tables by token, knows alternative sources per activity, and normalizes both
ISO and SAP YYYYMMDD dates. Every adaptation is stated in the rationale and reflected in
the confidence level. It is also the no-LLM fallback, so the product works without a key.
"""

from __future__ import annotations

from modelling.spec import (
    ActivityMapping,
    AttributeMapping,
    CaseIdMapping,
    MappingSpec,
    OpenQuestion,
)
from modelling.sql_explain import explain_sql

CASE_GRANULARITY_QID = "case_granularity"

PO_HEADER_CASE = "Purchase order (header, EKKO.EBELN)"
PO_LINE_CASE = "Purchase order line item (EKPO.EBELN + EBELP)"

SO_HEADER_CASE = "Sales order (header, VBAK.VBELN)"
SO_LINE_CASE = "Sales order line item (VBAP.VBELN + POSNR)"


def detect_process(schema: dict) -> str | None:
    """Which standard process family do the staged tables belong to?"""
    has_p2p = _find_table(schema, "EKKO") is not None
    has_o2c = _find_table(schema, "VBAK") is not None
    if has_p2p and has_o2c:
        raise ValueError(
            "both purchasing (EKKO) and sales (VBAK) tables are staged — stage one "
            "process per workspace so the mapping stays unambiguous"
        )
    if has_p2p:
        return "procure_to_pay"
    if has_o2c:
        return "order_to_cash"
    return None


def draft_spec(schema: dict, process: str, granularity: str | None = None) -> "MappingSpec":
    if process == "procure_to_pay":
        return draft_p2p_spec(schema, granularity)
    if process == "order_to_cash":
        return draft_o2c_spec(schema, granularity)
    raise ValueError(f"unsupported process {process!r}")


def _find_table(schema: dict, token: str) -> str | None:
    """Resolve a canonical SAP table in the staged schema: exact name, else by token.

    e.g. token 'EKKO' matches 'EKKO' or 'EKKO_PO_HEADER'; 'MSEG' matches
    'MKPF_MSEG_GOODS_RECEIPT'.
    """
    if token in schema:
        return token
    matches = sorted(name for name in schema if token in name)
    return matches[0] if matches else None


def _cols(schema: dict, table: str | None) -> set[str]:
    if not table or table not in schema:
        return set()
    return {c["name"].upper() for c in schema[table]["columns"]}


def _src(table: str) -> str:
    return f"src_{table.lower()}"


def _ts(expr: str) -> str:
    """Normalize a timestamp column: ISO strings/timestamps pass through, SAP YYYYMMDD
    integers are parsed. Unparseable values become NULL and are dropped at materialize."""
    v = f"CAST({expr} AS VARCHAR)"
    return f"COALESCE(TRY_CAST({v} AS TIMESTAMP), TRY_STRPTIME({v}, '%Y%m%d'))"


def _pick(cols: set[str], *candidates: str) -> str | None:
    return next((c for c in candidates if c in cols), None)


def draft_p2p_spec(schema: dict, granularity: str | None = None) -> MappingSpec:
    """Draft a P2P mapping spec from staged source tables.

    `granularity` is None on the first draft (raises the open question) or the human's
    answer on re-draft. Line-level granularity fans header-level events (approval,
    invoice, payment) out to every PO line, which is stated in the rationale.
    """
    ekko = _find_table(schema, "EKKO")
    ekpo = _find_table(schema, "EKPO")
    lfa1 = _find_table(schema, "LFA1")
    cdhdr = _find_table(schema, "CDHDR")
    mseg = _find_table(schema, "MSEG")
    rbkp = _find_table(schema, "RBKP")
    rseg = _find_table(schema, "RSEG")
    bkpf = _find_table(schema, "BKPF")
    bsak = _find_table(schema, "BSAK")

    line_level = granularity == PO_LINE_CASE
    if line_level:
        case_id = CaseIdMapping(
            source="EKPO.EBELN + EBELP",
            description="One case per purchase-order line item; header-level events are "
            "applied to every line of their PO.",
        )
    else:
        case_id = CaseIdMapping(
            source="EKKO.EBELN",
            description="One case per purchase order (header level).",
        )

    def case_select(po_col: str) -> tuple[str, str]:
        """(case-id select expression, extra join clause) for a header-level PO column."""
        if line_level and ekpo:
            return (
                f"{po_col} || '-' || p.EBELP",
                f"JOIN {_src(ekpo)} p ON p.EBELN = {po_col}",
            )
        return po_col, ""

    activities: list[ActivityMapping] = []
    open_questions: list[OpenQuestion] = []
    ekko_cols = _cols(schema, ekko)

    # -- PO Created --------------------------------------------------------------------
    if ekko and {"EBELN", "AEDAT"} <= ekko_cols:
        sel, join = case_select("e.EBELN")
        activities.append(
            ActivityMapping(
                name="PO Created",
                source_table=ekko,
                condition="every purchase-order header row",
                timestamp=f"{ekko}.AEDAT",
                rationale="PO header creation date is the natural start event of P2P.",
                confidence="high",
                sql=f"SELECT {sel} AS case_id, {_ts('e.AEDAT')} AS timestamp "
                f"FROM {_src(ekko)} e {join}",
            )
        )

    # -- PO Approved (+ PO Changed) ----------------------------------------------------
    cdhdr_cols = _cols(schema, cdhdr)
    if cdhdr and {"OBJECTID", "TCODE", "UDATE"} <= cdhdr_cols:
        sel, join = case_select("c.OBJECTID")
        activities.append(
            ActivityMapping(
                name="PO Approved",
                source_table=cdhdr,
                condition="purchase-order change documents recorded by the release "
                "transaction (TCODE = 'ME29N')",
                timestamp=f"{cdhdr}.UDATE",
                rationale="SAP writes a change document when a PO is released/approved via "
                "ME29N. If this customer approves through workflow instead, this "
                "condition is wrong — hence medium confidence.",
                confidence="medium",
                sql=(
                    f"SELECT {sel} AS case_id, {_ts('c.UDATE')} AS timestamp "
                    f"FROM {_src(cdhdr)} c {join} "
                    f"WHERE c.OBJECTCLAS = 'EINKBELEG' AND c.TCODE = 'ME29N'"
                ),
            )
        )
        activities.append(
            ActivityMapping(
                name="PO Changed",
                source_table=cdhdr,
                condition="purchase-order change documents from the change transaction "
                "(TCODE = 'ME22N')",
                timestamp=f"{cdhdr}.UDATE",
                rationale="ME22N change documents capture post-approval edits that often "
                "force re-approval (rework signal).",
                confidence="medium",
                sql=(
                    f"SELECT {sel} AS case_id, {_ts('c.UDATE')} AS timestamp "
                    f"FROM {_src(cdhdr)} c {join} "
                    f"WHERE c.OBJECTCLAS = 'EINKBELEG' AND c.TCODE = 'ME22N'"
                ),
            )
        )
    elif ekko and "FRGDAT" in ekko_cols:
        # No change documents staged — fall back to the release date on the PO header.
        sel, join = case_select("e.EBELN")
        activities.append(
            ActivityMapping(
                name="PO Approved",
                source_table=ekko,
                condition="purchase orders that carry a release date (FRGDAT is set)",
                timestamp=f"{ekko}.FRGDAT",
                rationale="No change-document table (CDHDR) was staged, so the release "
                "date on the PO header stands in for the approval event. This yields at "
                "most one approval per PO and cannot show re-approvals — if you need "
                "those, stage CDHDR as well.",
                confidence="medium",
                sql=(
                    f"SELECT {sel} AS case_id, {_ts('e.FRGDAT')} AS timestamp "
                    f"FROM {_src(ekko)} e {join} WHERE e.FRGDAT IS NOT NULL"
                ),
            )
        )

    # -- Goods Receipt -----------------------------------------------------------------
    mseg_cols = _cols(schema, mseg)
    gr_ts = _pick(mseg_cols, "BUDAT_MKPF", "BUDAT", "CPUDT_MKPF", "CPUDT")
    if mseg and "EBELN" in mseg_cols and "BWART" in mseg_cols and gr_ts:
        if line_level and "EBELP" in mseg_cols:
            sel, join = "m.EBELN || '-' || m.EBELP", ""
        else:
            sel, join = case_select("m.EBELN")
        activities.append(
            ActivityMapping(
                name="Goods Receipt",
                source_table=mseg,
                condition="goods movements with movement type 101 (GR against PO)",
                timestamp=f"{mseg}.{gr_ts}",
                rationale="Movement type 101 is the standard goods receipt for a purchase "
                f"order; {gr_ts} (posting date of the material document) is the event time.",
                confidence="high",
                sql=(
                    f"SELECT {sel} AS case_id, {_ts('m.' + gr_ts)} AS timestamp "
                    f"FROM {_src(mseg)} m {join} WHERE m.BWART = '101'"
                ),
            )
        )

    # -- Invoice Received --------------------------------------------------------------
    rbkp_cols = _cols(schema, rbkp)
    rseg_cols = _cols(schema, rseg)
    inv_ts = _pick(rbkp_cols, "CPUDT", "BLDAT", "BUDAT")
    if rbkp and rseg and inv_ts and {"BELNR", "EBELN"} <= rseg_cols and "BELNR" in rbkp_cols:
        sel, join = case_select("r.EBELN")
        activities.append(
            ActivityMapping(
                name="Invoice Received",
                source_table=f"{rbkp} + {rseg}",
                condition="invoice documents linked to the PO through invoice line items",
                timestamp=f"{rbkp}.{inv_ts}",
                rationale=(
                    "CPUDT is the entry date of the invoice document — when the invoice "
                    "actually arrived in the system. BLDAT (document date) would measure "
                    "the vendor's clock, not the process."
                    if inv_ts == "CPUDT"
                    else f"{inv_ts} is the best available arrival timestamp on the staged "
                    "invoice header."
                ),
                confidence="medium",
                sql=(
                    f"SELECT DISTINCT {sel} AS case_id, {_ts('h.' + inv_ts)} AS timestamp "
                    f"FROM {_src(rbkp)} h JOIN {_src(rseg)} r ON h.BELNR = r.BELNR {join}"
                ),
            )
        )

    # -- Payment Sent ------------------------------------------------------------------
    bkpf_cols = _cols(schema, bkpf)
    bsak_cols = _cols(schema, bsak)
    if bkpf and rseg and {"AWKEY", "BUDAT"} <= bkpf_cols and {"BELNR", "EBELN"} <= rseg_cols:
        sel, join = case_select("r.EBELN")
        blart = "b.BLART = 'KZ' AND " if "BLART" in bkpf_cols else ""
        activities.append(
            ActivityMapping(
                name="Payment Sent",
                source_table=f"{bkpf} + {rseg}",
                condition="accounting documents of type KZ (vendor payment) whose reference "
                "key points at an invoice on this PO",
                timestamp=f"{bkpf}.BUDAT",
                rationale="KZ documents are outgoing payments; AWKEY carries the link back "
                "to the cleared invoice. Posting date approximates the payment date.",
                confidence="medium",
                sql=(
                    f"SELECT DISTINCT {sel} AS case_id, {_ts('b.BUDAT')} AS timestamp "
                    f"FROM {_src(bkpf)} b JOIN {_src(rseg)} r ON b.AWKEY = r.BELNR "
                    f"{join} WHERE {blart}1=1"
                ),
            )
        )
    elif bsak and rseg and "BELNR" in bsak_cols and {"BELNR", "EBELN"} <= rseg_cols:
        pay_ts = _pick(bsak_cols, "AUGDT", "BUDAT")
        if pay_ts:
            sel, join = case_select("r.EBELN")
            activities.append(
                ActivityMapping(
                    name="Payment Sent",
                    source_table=f"{bsak} + {rseg}",
                    condition="cleared vendor line items (BSAK) matched to the PO's invoice "
                    "document number",
                    timestamp=f"{bsak}.{pay_ts}",
                    rationale=(
                        "BSAK holds cleared vendor items; AUGDT (clearing date) is when the "
                        "payment actually cleared."
                        if pay_ts == "AUGDT"
                        else "BSAK holds cleared vendor items; posting date approximates "
                        "the payment."
                    )
                    + " This assumes BSAK.BELNR carries the invoice document number — "
                    "please verify, as some systems store the FI document there instead.",
                    confidence="medium",
                    sql=(
                        f"SELECT DISTINCT {sel} AS case_id, {_ts('b.' + pay_ts)} AS timestamp "
                        f"FROM {_src(bsak)} b JOIN {_src(rseg)} r ON b.BELNR = r.BELNR {join}"
                    ),
                )
            )

    # -- attributes ----------------------------------------------------------------------
    attributes: list[AttributeMapping] = []
    lfa1_cols = _cols(schema, lfa1)
    if ekko and lfa1 and "LIFNR" in ekko_cols and {"LIFNR", "NAME1"} <= lfa1_cols:
        sel, join = case_select("e.EBELN")
        attributes.append(
            AttributeMapping(
                name="vendor",
                expression=f"vendor name from the vendor master ({ekko}.LIFNR -> {lfa1}.NAME1)",
                rationale="Vendor is the most common analysis dimension in P2P.",
                sql=(
                    f"SELECT {sel} AS case_id, l.NAME1 AS vendor FROM {_src(ekko)} e "
                    f"JOIN {_src(lfa1)} l ON e.LIFNR = l.LIFNR {join}"
                ),
            )
        )
    ekpo_cols = _cols(schema, ekpo)
    if ekpo and {"EBELN", "NETWR"} <= ekpo_cols:
        if line_level and "EBELP" in ekpo_cols:
            attributes.append(
                AttributeMapping(
                    name="net_value",
                    expression=f"net value of the PO line ({ekpo}.NETWR)",
                    rationale="At line granularity each case carries its own line value.",
                    sql=f"SELECT EBELN || '-' || EBELP AS case_id, NETWR AS net_value "
                    f"FROM {_src(ekpo)}",
                )
            )
        else:
            attributes.append(
                AttributeMapping(
                    name="net_value",
                    expression=f"total PO value = sum of line net values ({ekpo}.NETWR)",
                    rationale="Header-level value enables spend-weighted analysis.",
                    sql=(
                        f"SELECT EBELN AS case_id, SUM(NETWR) AS net_value "
                        f"FROM {_src(ekpo)} GROUP BY EBELN"
                    ),
                )
            )

    # The canonical P2P ambiguity: PO vs PO-line as the case notion. Always escalated,
    # never guessed (§4: "Escalate ambiguity, never guess silently").
    if granularity is None and ekpo and {"EBELN", "EBELP"} <= ekpo_cols:
        open_questions.append(
            OpenQuestion(
                id=CASE_GRANULARITY_QID,
                question="Should a case be a purchase order or a purchase-order line item?",
                why_it_matters=f"{ekpo} shows multiple line items per PO. At header level, "
                "partial deliveries and line-level splits are invisible; at line level, "
                "header events (approval, invoice, payment) are duplicated onto every "
                "line. This choice changes every metric in the dashboard.",
                options=[PO_HEADER_CASE, PO_LINE_CASE],
            )
        )

    resolved = {
        canonical: actual
        for canonical, actual in [
            ("EKKO", ekko), ("EKPO", ekpo), ("LFA1", lfa1), ("CDHDR", cdhdr),
            ("MSEG", mseg), ("RBKP", rbkp), ("RSEG", rseg), ("BKPF", bkpf), ("BSAK", bsak),
        ]
        if actual
    }
    notes = (
        "Drafted from SAP P2P priors over the staged schema. Table resolution: "
        + ", ".join(f"{k} -> {v}" for k, v in resolved.items())
        + ". Every SQL fragment is inspectable below; nothing runs until a named "
        "reviewer confirms."
    )

    for activity in activities:
        activity.sql_explanation = explain_sql(activity.sql)

    return MappingSpec(
        process="procure_to_pay",
        case_id=case_id,
        activities=activities,
        attributes=attributes,
        open_questions=open_questions,
        notes=notes,
    )


def draft_o2c_spec(schema: dict, granularity: str | None = None) -> MappingSpec:
    """Draft an Order-to-Cash mapping spec (sales side: VBAK/LIKP/VBRK/BSAD family)."""
    vbak = _find_table(schema, "VBAK")
    vbap = _find_table(schema, "VBAP")
    kna1 = _find_table(schema, "KNA1")
    cdhdr = _find_table(schema, "CDHDR")
    likp = _find_table(schema, "LIKP")
    lips = _find_table(schema, "LIPS")
    vbrk = _find_table(schema, "VBRK")
    vbrp = _find_table(schema, "VBRP")
    bsad = _find_table(schema, "BSAD")

    line_level = granularity == SO_LINE_CASE
    if line_level:
        case_id = CaseIdMapping(
            source="VBAP.VBELN + POSNR",
            description="One case per sales-order line item; header-level events are "
            "applied to every line of their order.",
        )
    else:
        case_id = CaseIdMapping(
            source="VBAK.VBELN",
            description="One case per sales order (header level).",
        )

    def case_select(order_col: str) -> tuple[str, str]:
        if line_level and vbap:
            return (
                f"{order_col} || '-' || p.POSNR",
                f"JOIN {_src(vbap)} p ON p.VBELN = {order_col}",
            )
        return order_col, ""

    activities: list[ActivityMapping] = []
    open_questions: list[OpenQuestion] = []
    vbak_cols = _cols(schema, vbak)

    if vbak and {"VBELN", "ERDAT"} <= vbak_cols:
        sel, join = case_select("v.VBELN")
        activities.append(
            ActivityMapping(
                name="Order Created",
                source_table=vbak,
                condition="every sales-order header row",
                timestamp=f"{vbak}.ERDAT",
                rationale="Sales order creation is the natural start of order-to-cash.",
                confidence="high",
                sql=f"SELECT {sel} AS case_id, {_ts('v.ERDAT')} AS timestamp "
                f"FROM {_src(vbak)} v {join}",
            )
        )

    cdhdr_cols = _cols(schema, cdhdr)
    if cdhdr and {"OBJECTID", "TCODE", "UDATE"} <= cdhdr_cols:
        sel, join = case_select("c.OBJECTID")
        activities.append(
            ActivityMapping(
                name="Order Changed",
                source_table=cdhdr,
                condition="sales-document change documents from the change transaction "
                "(TCODE = 'VA02')",
                timestamp=f"{cdhdr}.UDATE",
                rationale="VA02 change documents capture post-entry edits (credit holds "
                "released, quantities changed) — the O2C rework signal.",
                confidence="medium",
                sql=(
                    f"SELECT {sel} AS case_id, {_ts('c.UDATE')} AS timestamp "
                    f"FROM {_src(cdhdr)} c {join} "
                    f"WHERE c.OBJECTCLAS = 'VERKBELEG' AND c.TCODE = 'VA02'"
                ),
            )
        )

    likp_cols = _cols(schema, likp)
    lips_cols = _cols(schema, lips)
    if likp and lips and {"VBELN", "ERDAT"} <= likp_cols and {"VBELN", "VGBEL"} <= lips_cols:
        sel, join = case_select("l.VGBEL")
        activities.append(
            ActivityMapping(
                name="Delivery Created",
                source_table=f"{likp} + {lips}",
                condition="outbound deliveries linked to the order through delivery items "
                "(LIPS.VGBEL = order number)",
                timestamp=f"{likp}.ERDAT",
                rationale="Delivery header creation marks the start of fulfilment; the "
                "order link lives on the delivery item, not the header.",
                confidence="medium",
                sql=(
                    f"SELECT DISTINCT {sel} AS case_id, {_ts('h.ERDAT')} AS timestamp "
                    f"FROM {_src(likp)} h JOIN {_src(lips)} l ON h.VBELN = l.VBELN {join}"
                ),
            )
        )
        if "WADAT_IST" in likp_cols:
            activities.append(
                ActivityMapping(
                    name="Goods Issued",
                    source_table=f"{likp} + {lips}",
                    condition="deliveries with an actual goods-movement date "
                    "(WADAT_IST is set)",
                    timestamp=f"{likp}.WADAT_IST",
                    rationale="WADAT_IST is the actual goods-issue date — when stock "
                    "physically left. Deliveries without it have not shipped yet.",
                    confidence="high",
                    sql=(
                        f"SELECT DISTINCT {sel} AS case_id, {_ts('h.WADAT_IST')} AS timestamp "
                        f"FROM {_src(likp)} h JOIN {_src(lips)} l ON h.VBELN = l.VBELN {join} "
                        f"WHERE h.WADAT_IST IS NOT NULL"
                    ),
                )
            )

    vbrk_cols = _cols(schema, vbrk)
    vbrp_cols = _cols(schema, vbrp)
    inv_ts = _pick(vbrk_cols, "FKDAT", "ERDAT")
    if vbrk and vbrp and inv_ts and {"VBELN", "AUBEL"} <= vbrp_cols and "VBELN" in vbrk_cols:
        sel, join = case_select("p.AUBEL")
        activities.append(
            ActivityMapping(
                name="Invoice Created",
                source_table=f"{vbrk} + {vbrp}",
                condition="billing documents linked to the order through billing items "
                "(VBRP.AUBEL = order number)",
                timestamp=f"{vbrk}.{inv_ts}",
                rationale=f"{inv_ts} is the billing date on the invoice header; the order "
                "link lives on the billing item.",
                confidence="medium",
                sql=(
                    f"SELECT DISTINCT {sel} AS case_id, {_ts('h.' + inv_ts)} AS timestamp "
                    f"FROM {_src(vbrk)} h JOIN {_src(vbrp)} p ON h.VBELN = p.VBELN {join}"
                ),
            )
        )

    bsad_cols = _cols(schema, bsad)
    pay_ts = _pick(bsad_cols, "AUGDT", "BUDAT")
    if bsad and vbrp and pay_ts and "BELNR" in bsad_cols and {"VBELN", "AUBEL"} <= vbrp_cols:
        sel, join = case_select("p.AUBEL")
        activities.append(
            ActivityMapping(
                name="Payment Received",
                source_table=f"{bsad} + {vbrp}",
                condition="cleared customer line items (BSAD) matched to the order's "
                "invoice number",
                timestamp=f"{bsad}.{pay_ts}",
                rationale=(
                    "BSAD holds cleared customer items; AUGDT (clearing date) is when the "
                    "customer's payment cleared."
                    if pay_ts == "AUGDT"
                    else "BSAD holds cleared customer items; posting date approximates the payment."
                )
                + " This assumes BSAD.BELNR carries the invoice document number — please "
                "verify against your FI document flow.",
                confidence="medium",
                sql=(
                    f"SELECT DISTINCT {sel} AS case_id, {_ts('b.' + pay_ts)} AS timestamp "
                    f"FROM {_src(bsad)} b JOIN {_src(vbrp)} p ON b.BELNR = p.VBELN {join}"
                ),
            )
        )

    attributes: list[AttributeMapping] = []
    kna1_cols = _cols(schema, kna1)
    if vbak and kna1 and "KUNNR" in vbak_cols and {"KUNNR", "NAME1"} <= kna1_cols:
        sel, join = case_select("v.VBELN")
        attributes.append(
            AttributeMapping(
                name="customer",
                expression=f"customer name from the customer master ({vbak}.KUNNR -> {kna1}.NAME1)",
                rationale="Customer is the most common analysis dimension in O2C.",
                sql=(
                    f"SELECT {sel} AS case_id, k.NAME1 AS customer FROM {_src(vbak)} v "
                    f"JOIN {_src(kna1)} k ON v.KUNNR = k.KUNNR {join}"
                ),
            )
        )
    vbap_cols = _cols(schema, vbap)
    if vbap and {"VBELN", "NETWR"} <= vbap_cols:
        if line_level and "POSNR" in vbap_cols:
            attributes.append(
                AttributeMapping(
                    name="net_value",
                    expression=f"net value of the order line ({vbap}.NETWR)",
                    rationale="At line granularity each case carries its own line value.",
                    sql=f"SELECT VBELN || '-' || POSNR AS case_id, NETWR AS net_value "
                    f"FROM {_src(vbap)}",
                )
            )
        else:
            attributes.append(
                AttributeMapping(
                    name="net_value",
                    expression=f"total order value = sum of line net values ({vbap}.NETWR)",
                    rationale="Header-level value enables revenue-weighted analysis.",
                    sql=(
                        f"SELECT VBELN AS case_id, SUM(NETWR) AS net_value "
                        f"FROM {_src(vbap)} GROUP BY VBELN"
                    ),
                )
            )

    if granularity is None and vbap and {"VBELN", "POSNR"} <= vbap_cols:
        open_questions.append(
            OpenQuestion(
                id=CASE_GRANULARITY_QID,
                question="Should a case be a sales order or a sales-order line item?",
                why_it_matters=f"{vbap} shows multiple line items per order. At header "
                "level, partial deliveries and line-level splits are invisible; at line "
                "level, header events (order entry, invoice, payment) are duplicated onto "
                "every line. This choice changes every metric in the dashboard.",
                options=[SO_HEADER_CASE, SO_LINE_CASE],
            )
        )

    resolved = {
        canonical: actual
        for canonical, actual in [
            ("VBAK", vbak), ("VBAP", vbap), ("KNA1", kna1), ("CDHDR", cdhdr),
            ("LIKP", likp), ("LIPS", lips), ("VBRK", vbrk), ("VBRP", vbrp), ("BSAD", bsad),
        ]
        if actual
    }
    notes = (
        "Drafted from SAP Order-to-Cash priors over the staged schema. Table resolution: "
        + ", ".join(f"{k} -> {v}" for k, v in resolved.items())
        + ". Every SQL fragment is inspectable below; nothing runs until a named "
        "reviewer confirms."
    )

    for activity in activities:
        activity.sql_explanation = explain_sql(activity.sql)

    return MappingSpec(
        process="order_to_cash",
        case_id=case_id,
        activities=activities,
        attributes=attributes,
        open_questions=open_questions,
        notes=notes,
    )
