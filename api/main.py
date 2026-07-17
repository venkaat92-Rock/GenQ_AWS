"""ProcessIQ API — multi-tenant FastAPI backend (L5 surface over L2/L3/L4).

Flow per workspace: upload sources -> agent drafts mapping -> human answers open
questions + confirms -> materialize -> analysis + NL Q&A. The confirm gate is enforced
server-side; there is no endpoint that materializes a draft.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import uuid

import os

from api import storage
from api.dataload import build_dataload, copilot_answer, load_runs, record_run
from api.scheduler import load_schedule, save_schedule, start_scheduler
from api.datamodel import build_datamodel, rederive, save_datamodel_edits
from api.filters import Filters, apply_filters, filter_params
from mining.kpis import compute_kpi, compute_kpi_series, parse_kpi_request
from api.nlq import answer_question
from engine.db import EventLogStore, safe_table_name
from mining.cases import case_table, to_records
from mining.conformance import conformance
from mining.dfg import discover_dfg
from mining.insights import generate_insights
from mining.metrics import bottlenecks, rework, throughput
from mining.variants import variant_analysis
from modelling.agent import ModellingAgent
from api.ai import workspace_llm
from modelling.materialize import materialize
from modelling.priors import CASE_GRANULARITY_QID
from modelling.spec import MappingNotConfirmed

FIXTURES_SAP = Path(__file__).parent.parent / "fixtures" / "sap"
FIXTURES_O2C = Path(__file__).parent.parent / "fixtures" / "o2c"
FIXTURES_FORD_P2P = Path(__file__).parent.parent / "fixtures" / "ford_p2p"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="GenQ", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.on_event("startup")
def _start_scheduler() -> None:
    if not os.environ.get("PROCESSIQ_DISABLE_SCHEDULER"):
        start_scheduler()


# ---- auth ---------------------------------------------------------------------------


def workspace(x_api_key: str | None = Header(default=None)) -> dict:
    ws = storage.resolve_workspace(x_api_key)
    if ws is None:
        raise HTTPException(401, "missing or invalid X-API-Key")
    return ws


def _event_log(store: EventLogStore) -> pd.DataFrame:
    tables = store.sql(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'event_log'"
    )
    if tables.empty:
        raise HTTPException(
            400, "no event log materialized yet — draft and confirm a mapping first"
        )
    df = store.sql("SELECT * FROM event_log ORDER BY case_id, timestamp")
    if df.empty:
        raise HTTPException(
            400, "the event log is empty — check the mapping's timestamps and re-run the load"
        )
    return df


# ---- workspaces ---------------------------------------------------------------------


class WorkspaceIn(BaseModel):
    name: str


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceIn):
    try:
        return storage.create_workspace(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/workspaces/me")
def whoami(ws: dict = Depends(workspace)):
    return {"workspace_id": ws["id"], "name": ws["name"]}


# ---- sources (L1-lite: CSV/table upload) --------------------------------------------


@app.post("/api/sources/upload")
async def upload_source(
    ws: dict = Depends(workspace),
    file: UploadFile = File(...),
    table: str = Form(default=""),
):
    name = table or Path(file.filename or "table").stem
    target = ws["dir"] / f"upload_{Path(file.filename or name).name}"
    target.write_bytes(await file.read())
    try:
        with EventLogStore(ws["db_path"]) as store:
            rows = store.stage_csv(name, target)
        staged_name = safe_table_name(name)  # the name the table was actually staged under
    except HTTPException:
        raise
    except Exception as exc:
        # duckdb CSV-sniffing errors, encoding issues, non-CSV files, bad names, ...
        raise HTTPException(400, f"could not read '{file.filename}' as a CSV table: {exc}")
    finally:
        target.unlink(missing_ok=True)
    return {"table": staged_name, "rows": rows}


@app.post("/api/sources/demo")
def load_demo(ws: dict = Depends(workspace), dataset: str = "p2p"):
    sources = {
        "p2p": (FIXTURES_SAP, "synthetic SAP purchase-to-pay demo data"),
        "o2c": (FIXTURES_O2C, "synthetic SAP order-to-cash (order management) demo data"),
        "ford_p2p": (FIXTURES_FORD_P2P,
                    "synthetic Ford Motors purchase-to-pay demo data — 360 POs across "
                    "7 vendor categories with chip-shortage delays, price disputes, "
                    "maverick buying, and partial deliveries"),
    }
    if dataset not in sources:
        raise HTTPException(400, "dataset must be 'p2p', 'o2c', or 'ford_p2p'")
    fixture_dir, note = sources[dataset]
    staged = {}
    with EventLogStore(ws["db_path"]) as store:
        for csv in sorted(fixture_dir.glob("*.csv")):
            staged[csv.stem] = store.stage_csv(csv.stem, csv)
    return {"staged": staged, "note": note}


@app.get("/api/sources")
def list_sources(ws: dict = Depends(workspace)):
    with EventLogStore(ws["db_path"]) as store:
        return store.schema_summary()


# ---- ready-made process connectors (L1) ---------------------------------------------


@app.get("/api/connectors")
def list_connectors():
    from connectors.catalog import CATALOG

    return {
        "connectors": [
            {k: c[k] for k in ("id", "name", "system", "process", "agent_support",
                               "description")}
            | {"table_count": len(c["tables"]),
               "live_kind": c.get("live", {}).get("kind")}
            for c in CATALOG
        ],
        "live": [
            {"kind": "sqlite", "label": "SQLite file (server path)"},
            {"kind": "postgres", "label": "PostgreSQL (DSN)"},
            {"kind": "mysql", "label": "MySQL (DSN)"},
            {"kind": "url", "label": "CSV from URL"},
        ],
    }


@app.get("/api/connectors/{connector_id}")
def connector_detail(connector_id: str):
    from connectors.catalog import get_connector

    connector = get_connector(connector_id)
    if connector is None:
        raise HTTPException(404, f"no connector {connector_id!r}")
    return connector


@app.get("/api/connectors/{connector_id}/templates")
def connector_templates(connector_id: str):
    """Zip of empty CSVs with the right headers — hand to whoever exports the data."""
    import io
    import zipfile

    from fastapi.responses import Response

    from connectors.catalog import get_connector

    connector = get_connector(connector_id)
    if connector is None:
        raise HTTPException(404, f"no connector {connector_id!r}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in connector["tables"]:
            zf.writestr(f"{table['name']}.csv", ",".join(table["columns"]) + "\n")
        zf.writestr(
            "README.txt",
            f"{connector['name']}\n\n{connector['description']}\n\n"
            f"How to export: {connector['export_hint']}\n\n"
            "Fill one CSV per table (headers provided) and upload them on the Data page.\n"
            "Tables marked required in the catalog are needed for the agent's draft; the\n"
            "others enrich it.\n",
        )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{connector_id}-csv-templates.zip"'},
    )


class ExtractIn(BaseModel):
    kind: str          # sqlite | postgres | mysql | url | servicenow | jira | salesforce | odata
    config: dict = {}  # kind-specific fields (DSNs, URLs, credentials, table lists)
    name: str = ""     # when set: save as a reusable connection
    auto_refresh: bool = False  # saved connections: re-extract before scheduled loads


_load_connections = storage.load_connections
_save_connections = storage.save_connections

_SECRET_KEYS = ("password", "token", "access_token", "connection")


def _masked(connection: dict) -> dict:
    config = {
        k: ("•••" if k in _SECRET_KEYS and v else v)
        for k, v in connection["config"].items()
    }
    return {**connection, "config": config}


@app.post("/api/connectors/extract")
def connectors_extract(body: ExtractIn, ws: dict = Depends(workspace)):
    from connectors.extract import ConnectorError
    from connectors.live_api import run_live_extraction

    try:
        with EventLogStore(ws["db_path"]) as store:
            result = run_live_extraction(store, body.kind, body.config)
    except ConnectorError as exc:
        raise HTTPException(400, str(exc))

    if body.name.strip():  # save for re-use / scheduled refresh (v1: plain JSON)
        connections = _load_connections(ws)
        connections = [c for c in connections if c["name"] != body.name.strip()]
        connections.append({
            "name": body.name.strip(), "kind": body.kind, "config": body.config,
            "auto_refresh": body.auto_refresh,
        })
        _save_connections(ws, connections)
        result["saved_as"] = body.name.strip()
    return result


@app.get("/api/connections")
def list_connections(ws: dict = Depends(workspace)):
    return {"connections": [_masked(c) for c in _load_connections(ws)]}


@app.post("/api/connections/{name}/run")
def run_connection(name: str, ws: dict = Depends(workspace)):
    from connectors.extract import ConnectorError
    from connectors.live_api import run_live_extraction

    connection = next((c for c in _load_connections(ws) if c["name"] == name), None)
    if connection is None:
        raise HTTPException(404, f"no saved connection {name!r}")
    try:
        with EventLogStore(ws["db_path"]) as store:
            return run_live_extraction(store, connection["kind"], connection["config"])
    except ConnectorError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/connections/{name}")
def delete_connection(name: str, ws: dict = Depends(workspace)):
    connections = _load_connections(ws)
    remaining = [c for c in connections if c["name"] != name]
    if len(remaining) == len(connections):
        raise HTTPException(404, f"no saved connection {name!r}")
    _save_connections(ws, remaining)
    return {"deleted": name}


# ---- mapping: draft -> answer -> confirm -> materialize (the §1.1 loop) -------------


@app.post("/api/mapping/draft")
def draft_mapping(ws: dict = Depends(workspace)):
    with EventLogStore(ws["db_path"]) as store:
        try:
            spec = ModellingAgent(store, workspace_llm(ws)).draft()
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    storage.save_spec(ws, spec)
    return spec.to_dict()


@app.get("/api/mapping")
def get_mapping(ws: dict = Depends(workspace)):
    spec = storage.load_spec(ws)
    if spec is None:
        raise HTTPException(404, "no mapping drafted yet")
    return spec.to_dict()


@app.post("/api/mapping/reopen")
def reopen_mapping(ws: dict = Depends(workspace)):
    """Start a new version of a confirmed mapping: unlock it back to draft, keeping
    all its content, so the reviewer can edit and re-confirm (§1.1 loop, next version)."""
    spec = storage.load_spec(ws)
    if spec is None:
        raise HTTPException(404, "no mapping drafted yet")
    try:
        spec.reopen()
    except MappingNotConfirmed as exc:
        raise HTTPException(409, str(exc))
    storage.save_spec(ws, spec)
    return spec.to_dict()


class AnswersIn(BaseModel):
    answers: dict[str, str]


@app.post("/api/mapping/answers")
def answer_questions(body: AnswersIn, ws: dict = Depends(workspace)):
    spec = storage.load_spec(ws)
    if spec is None:
        raise HTTPException(404, "no mapping drafted yet")
    if spec.status == "confirmed":
        raise HTTPException(409, "mapping already confirmed — draft a new version to change it")
    try:
        for qid, answer in body.answers.items():
            spec.answer_question(qid, answer)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    if CASE_GRANULARITY_QID in body.answers:
        with EventLogStore(ws["db_path"]) as store:
            spec = ModellingAgent(store).redraft_with_answers(spec)
    storage.save_spec(ws, spec)
    return spec.to_dict()


class SqlEditIn(BaseModel):
    activity: str
    sql: str
    updated_by: str


@app.put("/api/mapping/sql")
def edit_activity_sql(body: SqlEditIn, ws: dict = Depends(workspace)):
    """Human override of an activity's SQL. The edit is validated against the staged
    tables, its plain-English explanation is regenerated, and the mapping drops back to
    draft — changed logic never runs without a fresh confirmation (§1.1)."""
    from modelling.sql_explain import explain_sql

    if not body.updated_by.strip():
        raise HTTPException(400, "a reviewer name is required (SME sign-off)")
    spec = storage.load_spec(ws)
    if spec is None:
        raise HTTPException(404, "no mapping drafted yet")
    target = next((a for a in spec.activities if a.name == body.activity), None)
    if target is None:
        raise HTTPException(404, f"no activity named {body.activity!r} in the mapping")

    # validate: must run against the staged tables and expose case_id + timestamp
    try:
        with EventLogStore(ws["db_path"]) as store:
            preview = store.sql(f"SELECT case_id, timestamp FROM ({body.sql}) AS q LIMIT 5")
    except Exception as exc:
        raise HTTPException(400, f"SQL rejected: {exc}")

    target.sql = body.sql.strip()
    target.sql_explanation = explain_sql(target.sql)
    target.sql_edited_by = body.updated_by.strip()
    was_confirmed = spec.status == "confirmed"
    spec.status = "draft"
    spec.reviewer = None
    spec.confirmed_at = None
    spec.notes = (spec.notes + f" (SQL of '{target.name}' edited by "
                  f"{body.updated_by.strip()}; re-confirmation required.)").strip()
    storage.save_spec(ws, spec)
    return {
        **spec.to_dict(),
        "edit_note": (
            "SQL saved and validated"
            + (f" ({len(preview)} sample rows)" if len(preview) else " (0 rows matched — check the conditions)")
            + (". The mapping is back in draft — re-confirm to apply it to the event log."
               if was_confirmed else ".")
        ),
    }


class ConfirmIn(BaseModel):
    reviewer: str


@app.post("/api/mapping/confirm")
def confirm_mapping(body: ConfirmIn, ws: dict = Depends(workspace)):
    spec = storage.load_spec(ws)
    if spec is None:
        raise HTTPException(404, "no mapping drafted yet")
    try:
        spec.confirm(body.reviewer)
        with EventLogStore(ws["db_path"]) as store:
            result = materialize(store, spec)
    except MappingNotConfirmed as exc:
        raise HTTPException(409, str(exc))
    storage.save_spec(ws, spec)
    record_run(ws, result, trigger=f"mapping confirmed by {spec.reviewer}")
    return {"materialized": result, "spec_status": spec.status, "reviewer": spec.reviewer}


# ---- analysis (L4 over the materialized log) ----------------------------------------


def _filtered_log(ws: dict, f: Filters) -> tuple[pd.DataFrame, list[str]]:
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    try:
        filtered, applied = apply_filters(df, f)
    except ValueError as exc:  # malformed filter input (e.g. bad dates)
        raise HTTPException(400, str(exc))
    if filtered.empty:
        raise HTTPException(400, "no cases match the current filters")
    return filtered, applied


def _reference_for(ws: dict, df: pd.DataFrame) -> tuple[list[str], str]:
    """The conformance target: the SME-saved reference process, else the most
    frequent variant (and the answer says which one was used)."""
    saved = storage.load_reference(ws)
    if saved and saved.get("activities"):
        return saved["activities"], f"reference process set by {saved.get('updated_by', 'SME')}"
    return variant_analysis(df)[0]["activities"], "most frequent variant (no reference saved yet)"


@app.get("/api/analysis/facets")
def analysis_facets(ws: dict = Depends(workspace)):
    """Available filter values, for the UI's filter bar."""
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    cases = case_table(df)
    from api.filters import party_column

    party = party_column(df)
    return {
        "party": party or "vendor",
        "vendors": sorted(df[party].dropna().unique().tolist()) if party else [],
        "activities": sorted(df["activity"].unique().tolist()),
        "date_min": str(cases["start"].min().date()),
        "date_max": str(cases["start"].max().date()),
        "duration_max_days": float(cases["duration_days"].max().round(1)),
    }


@app.get("/api/analysis/summary")
def analysis_summary(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, applied = _filtered_log(ws, f)
    variants = variant_analysis(df)
    reference, ref_note = _reference_for(ws, df)
    t = throughput(df)
    r = rework(df)
    c = conformance(df, [reference])
    spec = storage.load_spec(ws)
    return {
        "process": spec.process.replace("_", "-") if spec else "process",
        "cases": t["cases"],
        "events": int(len(df)),
        "variants": len(variants),
        "median_days": t["median_hours"] / 24,
        "p90_days": t["p90_hours"] / 24,
        "rework_share": r["rework_share"],
        "conforming_share": c["conforming_share"],
        "reference_variant": " -> ".join(reference),
        "reference_note": ref_note,
        "filters_applied": applied,
    }


@app.get("/api/analysis/variants")
def analysis_variants(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    return variant_analysis(df)


@app.get("/api/analysis/dfg")
def analysis_dfg(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    return discover_dfg(df)


@app.get("/api/analysis/throughput")
def analysis_throughput(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    return throughput(df)


@app.get("/api/analysis/bottlenecks")
def analysis_bottlenecks(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    return bottlenecks(df)


@app.get("/api/analysis/rework")
def analysis_rework(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    return rework(df)


@app.get("/api/analysis/conformance")
def analysis_conformance(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    df, _ = _filtered_log(ws, f)
    reference, ref_note = _reference_for(ws, df)
    result = conformance(df, [reference])
    result["note"] = ref_note
    return result


@app.get("/api/analysis/cases")
def analysis_cases(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    """Case explorer: one row per case with duration, variant, rework/conformance flags."""
    df, applied = _filtered_log(ws, f)
    reference, _ = _reference_for(ws, df)
    return {"cases": to_records(case_table(df, reference)), "filters_applied": applied}


@app.get("/api/analysis/case/{case_id}")
def analysis_case_detail(case_id: str, ws: dict = Depends(workspace)):
    """Event timeline of a single case."""
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    events = df[df["case_id"].astype(str) == case_id].sort_values("timestamp")
    if events.empty:
        raise HTTPException(404, f"case {case_id!r} not found")
    return {
        "case_id": case_id,
        "events": [
            {"activity": r["activity"], "timestamp": str(r["timestamp"])}
            for _, r in events.iterrows()
        ],
    }


@app.get("/api/analysis/insights")
def analysis_insights(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    """Root-cause-lite: transparent comparisons narrated in business language."""
    df, applied = _filtered_log(ws, f)
    reference, _ = _reference_for(ws, df)
    return {"insights": generate_insights(df, reference), "filters_applied": applied}


# ---- SME reference process (Signavio-style conformance target) ----------------------


class ReferenceIn(BaseModel):
    activities: list[str]
    updated_by: str


@app.get("/api/reference")
def get_reference(ws: dict = Depends(workspace)):
    saved = storage.load_reference(ws)
    if saved:
        return {**saved, "source": "sme"}
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    return {
        "activities": variant_analysis(df)[0]["activities"],
        "updated_by": None,
        "source": "most_frequent_variant",
    }


@app.put("/api/reference")
def put_reference(body: ReferenceIn, ws: dict = Depends(workspace)):
    if not body.activities:
        raise HTTPException(400, "reference must contain at least one activity")
    if not body.updated_by.strip():
        raise HTTPException(400, "a reviewer name is required (SME sign-off)")
    return {**storage.save_reference(ws, body.activities, body.updated_by.strip()), "source": "sme"}


# ---- data load canvas + co-pilot -----------------------------------------------------


@app.get("/api/dataload")
def get_dataload(ws: dict = Depends(workspace)):
    """The Celonis-style data-integration canvas — all values are real workspace state."""
    return build_dataload(ws)


@app.get("/api/dataload/runs")
def dataload_runs(ws: dict = Depends(workspace)):
    """Full load history (the Celonis job Logs tab equivalent), newest first."""
    return {"runs": list(reversed(load_runs(ws)))}


class ScheduleIn(BaseModel):
    enabled: bool
    frequency: str
    updated_by: str


@app.get("/api/schedule")
def get_schedule(ws: dict = Depends(workspace)):
    return load_schedule(ws)


@app.put("/api/schedule")
def put_schedule(body: ScheduleIn, ws: dict = Depends(workspace)):
    if not body.updated_by.strip():
        raise HTTPException(400, "a reviewer name is required (SME sign-off)")
    if body.enabled:
        spec = storage.load_spec(ws)
        if spec is None or spec.status != "confirmed":
            raise HTTPException(
                409, "cannot enable a schedule before a data model is confirmed — "
                "the schedule only ever runs human-confirmed mappings"
            )
    try:
        return save_schedule(ws, body.enabled, body.frequency, body.updated_by.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/dataload/run")
def run_dataload(ws: dict = Depends(workspace)):
    """Run now: rebuild the event log from staged sources using the confirmed model."""
    spec = storage.load_spec(ws)
    if spec is None or spec.status != "confirmed":
        raise HTTPException(409, "no confirmed data model — confirm a mapping first")
    with EventLogStore(ws["db_path"]) as store:
        result = materialize(store, spec)
    record_run(ws, result, trigger="manual run")
    return {"materialized": result}


class CopilotIn(BaseModel):
    question: str
    context: str | None = None  # canvas element id, e.g. "data_jobs"


@app.post("/api/copilot")
def copilot(body: CopilotIn, ws: dict = Depends(workspace)):
    if not body.question.strip():
        raise HTTPException(400, "question is empty")

    # KPI creation intent first (dashboard co-pilot): propose, preview, let the user save
    spec = storage.load_spec(ws)
    activities = [a.name for a in spec.activities] if spec else []
    kpi_def = parse_kpi_request(body.question, activities)
    if kpi_def is not None:
        with EventLogStore(ws["db_path"]) as store:
            df = _event_log(store)
        name = kpi_def.pop("name")
        preview = compute_kpi(df, kpi_def)
        return {
            "answer": (
                f"Here's the KPI I'd create — {name}: currently "
                f"{preview['value']} {preview['unit']} "
                f"(measured on {preview['cases_measured']} cases). "
                "Click Save KPI to pin it to your dashboard, or rephrase to adjust."
            ),
            "scope": "kpi builder",
            "engine": "kpi-parser",
            "kpi_proposal": {"name": name, "definition": kpi_def, "preview": preview},
        }

    return copilot_answer(ws, body.question, body.context, workspace_llm(ws))


# ---- custom KPIs ---------------------------------------------------------------------


class KpiIn(BaseModel):
    name: str
    definition: dict


def _compute_for(df, definition: dict) -> dict:
    """Value + optional series, depending on the definition's breakdown."""
    computed = compute_kpi(df, definition)
    if definition.get("group_by") or definition.get("trend"):
        computed = {**computed, **compute_kpi_series(df, definition)}
    return computed


@app.get("/api/kpis")
def list_kpis(ws: dict = Depends(workspace), f: Filters = Depends(filter_params)):
    kpis = storage.load_kpis(ws)
    if not kpis:
        return []
    df, _ = _filtered_log(ws, f)
    out = []
    for kpi in kpis:
        try:
            computed = _compute_for(df, kpi["definition"])
        except Exception as exc:
            computed = {"value": None, "unit": f"error: {exc}", "cases_measured": 0}
        out.append({**kpi, **computed})
    return out


@app.get("/api/kpis/options")
def kpi_options(ws: dict = Depends(workspace)):
    """What the KPI builder can choose from: activities and case attributes."""
    spec = storage.load_spec(ws)
    activities = [a.name for a in spec.activities] if spec else []
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    core = {"case_id", "activity", "timestamp"}
    attributes = [c for c in df.columns if c not in core]
    return {"activities": activities or sorted(df["activity"].unique().tolist()),
            "attributes": attributes}


@app.post("/api/kpis/preview")
def preview_kpi(body: KpiIn, ws: dict = Depends(workspace)):
    """Compute without saving — the builder shows this before the user commits."""
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    try:
        return _compute_for(df, body.definition)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"invalid KPI definition: {exc}")


@app.post("/api/kpis")
def create_kpi(body: KpiIn, ws: dict = Depends(workspace)):
    if not body.name.strip():
        raise HTTPException(400, "KPI needs a name")
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    try:
        preview = _compute_for(df, body.definition)  # validates the definition
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"invalid KPI definition: {exc}")
    kpis = storage.load_kpis(ws)
    kpi = {"id": uuid.uuid4().hex[:10], "name": body.name.strip(),
           "definition": body.definition}
    kpis.append(kpi)
    storage.save_kpis(ws, kpis)
    return {**kpi, **preview}


@app.delete("/api/kpis/{kpi_id}")
def delete_kpi(kpi_id: str, ws: dict = Depends(workspace)):
    kpis = storage.load_kpis(ws)
    remaining = [k for k in kpis if k["id"] != kpi_id]
    if len(remaining) == len(kpis):
        raise HTTPException(404, f"no KPI {kpi_id!r}")
    storage.save_kpis(ws, remaining)
    return {"deleted": kpi_id}


# ---- data model (Celonis Model-tab equivalent) ---------------------------------------


@app.get("/api/datamodel")
def get_datamodel(ws: dict = Depends(workspace)):
    return build_datamodel(ws)


class DataModelEditIn(BaseModel):
    relationships: list[dict] | None = None
    identifiers: dict[str, str | None] | None = None
    updated_by: str


@app.put("/api/datamodel")
def edit_datamodel(body: DataModelEditIn, ws: dict = Depends(workspace)):
    if not body.updated_by.strip():
        raise HTTPException(400, "a reviewer name is required (SME sign-off)")
    try:
        return save_datamodel_edits(ws, body.relationships, body.identifiers,
                                    body.updated_by.strip())
    except ValueError as exc:  # malformed relationship payloads
        raise HTTPException(400, str(exc))


@app.post("/api/datamodel/rederive")
def rederive_datamodel(ws: dict = Depends(workspace)):
    return rederive(ws)


# ---- NL self-serve (L5) -------------------------------------------------------------


class AskIn(BaseModel):
    question: str


@app.post("/api/ask")
def ask(body: AskIn, ws: dict = Depends(workspace)):
    if not body.question.strip():
        raise HTTPException(400, "question is empty")
    spec = storage.load_spec(ws)
    activity_order = [a.name for a in spec.activities] if spec else []
    with EventLogStore(ws["db_path"]) as store:
        df = _event_log(store)
    return answer_question(body.question, df, activity_order, workspace_llm(ws))


# ---- DiagramIQ offering ---------------------------------------------------------------

from api.diagram import make_router as _make_diagram_router  # noqa: E402
from api.members import make_router as _make_members_router  # noqa: E402
from api.ai import make_router as _make_ai_router  # noqa: E402
from api.dictionary import make_router as _make_dictionary_router  # noqa: E402
from api.workflow import make_router as _make_workflow_router  # noqa: E402

app.include_router(_make_diagram_router(workspace))
app.include_router(_make_members_router(workspace))
app.include_router(_make_ai_router(workspace))
app.include_router(_make_dictionary_router(workspace))
app.include_router(_make_workflow_router(workspace))


# ---- UI (built React app) -----------------------------------------------------------

if STATIC_DIR.exists():

    @app.get("/", include_in_schema=False)
    def index():
        # index.html must never be cached — it points at the hashed asset bundles,
        # so a stale copy pins users to an old version of the app after a deploy.
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
