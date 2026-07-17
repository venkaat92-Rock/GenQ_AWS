"""Data Load view + Ask Co-pilot backend.

The data-load canvas mirrors Celonis' Data Integration screen, but every attribute is
REAL workspace state, auto-produced by the modelling agent and editable by the human
through the mapping review — never hand-configured by a data engineer:

  Input       -> staged source uploads ("data connections")
  Processing  -> the agent's SQL fragments (transformations), refresh runs (loads),
                 DuckDB storage
  Output      -> objects/events in the materialized log + the confirmed data model (spec)

The co-pilot answers questions about any of it from a facts dict assembled here — with an
LLM when configured, template answers otherwise — and always reports which facts it used
(traceable, not magic). Process-metric questions fall through to the L5 NL layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from engine.db import EventLogStore
from api import storage


def _runs_file(ws: dict):
    return ws["dir"] / "dataload_runs.json"


def record_run(ws: dict, stats: dict, trigger: str) -> None:
    runs = load_runs(ws)
    runs.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trigger": trigger,
            **stats,
        }
    )
    _runs_file(ws).write_text(json.dumps(runs, indent=2), encoding="utf-8")


def load_runs(ws: dict) -> list[dict]:
    path = _runs_file(ws)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def build_dataload(ws: dict) -> dict:
    """Assemble the full data-load canvas state from real workspace artifacts."""
    spec = storage.load_spec(ws)
    runs = load_runs(ws)

    with EventLogStore(ws["db_path"]) as store:
        schema = store.schema_summary(sample_rows=0)
        has_log = not store.sql(
            "SELECT table_name FROM information_schema.tables WHERE table_name='event_log'"
        ).empty
        objects = events = 0
        if has_log:
            row = store.sql(
                "SELECT count(DISTINCT case_id) AS c, count(*) AS e FROM event_log"
            ).iloc[0]
            objects, events = int(row["c"]), int(row["e"])

    tables = {name: info["row_count"] for name, info in schema.items()}
    db_path = ws["db_path"]
    size_mb = round(db_path.stat().st_size / 1e6, 2) if db_path.exists() else 0.0

    transformations = []
    if spec:
        for a in spec.activities:
            transformations.append(
                {"name": a.name, "kind": "activity", "source": a.source_table,
                 "confidence": a.confidence}
            )
        for attr in spec.attributes:
            transformations.append(
                {"name": attr.name, "kind": "attribute", "source": attr.expression,
                 "confidence": "high"}
            )

    if spec is None:
        model_status = "none"
    elif spec.status == "confirmed":
        model_status = "working"
    else:
        model_status = "draft (blocked on review)" if spec.unanswered_questions() else "draft"

    from api.scheduler import load_schedule  # local import to avoid a cycle

    sched = load_schedule(ws)
    schedule_mode = (
        f"{sched['frequency']} (enabled)" if sched["enabled"] else "manual / on-confirm"
    )
    schedule_note = (
        f"Next scheduled load: {sched['next_run']}. Set by {sched['updated_by']}."
        if sched["enabled"]
        else "The log rebuilds when a mapping is confirmed, on Run now, or on a "
        "schedule you enable in the Schedule panel."
    )

    return {
        "sources": {
            "connections": [
                {"name": "CSV / table upload", "kind": "upload", "tables": len(tables),
                 "status": "ok" if tables else "empty"}
            ],
            "note": "Live system connectors (SAP RFC/OData) arrive in Phase 4.",
        },
        "processing": {
            "schedule": {
                "mode": schedule_mode,
                "note": schedule_note,
                **sched,
            },
            "jobs": {
                "extractions": len(tables),
                "transformations": len(transformations),
                "transformation_list": transformations,
                "model_loads": len(runs),
                "status": "ok" if model_status == "working" else "waiting for confirmed model",
            },
            "storage": {"engine": "DuckDB (embedded)", "size_mb": size_mb,
                        "tables": tables},
            "last_run": runs[-1] if runs else None,
        },
        "output": {
            "objects": objects,
            "events": events,
            "data_model": {
                "name": spec.process if spec else None,
                "status": model_status,
                "version": spec.version if spec else None,
                "confirmed_by": spec.reviewer if spec else None,
                "activities": len(spec.activities) if spec else 0,
                "attributes": len(spec.attributes) if spec else 0,
                "open_questions": len(spec.unanswered_questions()) if spec else 0,
                "authored_by": "AI modelling agent, human-confirmed" if spec else None,
            },
        },
        "consumers": ["Dashboard", "Ask co-pilot"],
    }


# ---- co-pilot -------------------------------------------------------------------------

_ELEMENT_KEYS = {
    "connections": ("sources",),
    "schedule": ("processing", "schedule"),
    "data_jobs": ("processing", "jobs"),
    "storage": ("processing", "storage"),
    "objects_events": ("output",),
    "data_model": ("output", "data_model"),
}

_LLM_SYSTEM = """You are the ProcessIQ co-pilot. Answer the user's question about their
process-mining data load using ONLY the facts JSON provided. Be concise and concrete in
business language. If the facts don't contain the answer, say so plainly and suggest where
to look (Data page, Mapping review, Dashboard). Never invent numbers."""


def copilot_answer(ws: dict, question: str, context: str | None, llm=None) -> dict:
    state = build_dataload(ws)
    q = question.lower()

    facts: dict = state
    scope = "data load (all elements)"
    if context in _ELEMENT_KEYS:
        node = state
        for key in _ELEMENT_KEYS[context]:
            node = node[key]
        facts = {context: node}
        scope = f"selected element: {context.replace('_', ' ')}"

    if llm is not None:
        try:
            text = llm.complete(
                _LLM_SYSTEM,
                json.dumps({"facts": facts, "scope": scope, "question": question},
                           default=str),
            ).strip()
            return {"answer": text, "scope": scope, "engine": "llm"}
        except Exception:
            pass  # deterministic path below still answers

    jobs = state["processing"]["jobs"]
    model = state["output"]["data_model"]
    storage_info = state["processing"]["storage"]

    def fmt_transformations() -> str:
        risky = [t for t in jobs["transformation_list"] if t["confidence"] != "high"]
        base = (
            f"{jobs['transformations']} transformations were drafted by the AI agent "
            f"({sum(1 for t in jobs['transformation_list'] if t['kind'] == 'activity')} "
            f"activity mappings + "
            f"{sum(1 for t in jobs['transformation_list'] if t['kind'] == 'attribute')} "
            f"attributes)."
        )
        if risky:
            base += (
                " Review first (not high confidence): "
                + ", ".join(f"{t['name']} ({t['confidence']})" for t in risky)
                + ". Edit them in Mapping review."
            )
        return base

    if context == "data_model" or "model" in q:
        if model["status"] == "none":
            answer = "No data model yet — draft one in Mapping review (the AI agent reads your staged tables and proposes it)."
        else:
            answer = (
                f"Data model '{model['name']}' v{model['version']} is {model['status']}: "
                f"{model['activities']} activities and {model['attributes']} attributes, "
                f"authored by the AI modelling agent"
                + (f" and confirmed by {model['confirmed_by']}." if model["confirmed_by"]
                   else f"; {model['open_questions']} open question(s) still need a human decision in Mapping review.")
            )
    elif context == "connections" or "connection" in q or "source" in q:
        c = state["sources"]["connections"][0]
        answer = (
            f"1 data connection ({c['name']}) with {c['tables']} staged tables: "
            + ", ".join(f"{t} ({n} rows)" for t, n in list(storage_info["tables"].items())[:10])
            + f". {state['sources']['note']}"
        )
    elif context == "schedule" or "schedule" in q or "refresh" in q or "when" in q and "load" in q:
        s = state["processing"]["schedule"]
        last = state["processing"]["last_run"]
        answer = f"Schedule: {s['mode']}. {s['note']}" + (
            f" Last load: {last['at']} ({last['trigger']}, {last.get('events', '?')} events)."
            if last else " No load has run yet."
        )
    elif context == "storage" or "storage" in q or "database" in q or "duckdb" in q:
        answer = (
            f"Storage engine: {storage_info['engine']}, {storage_info['size_mb']} MB, "
            f"{len(storage_info['tables'])} staged source tables"
            + (f" plus the materialized event log ({state['output']['events']} events)."
               if state["output"]["events"] else ".")
        )
    elif context == "objects_events" or "object" in q or "event" in q and "log" in q:
        answer = (
            f"The materialized event log holds {state['output']['objects']} cases (objects) "
            f"and {state['output']['events']} events, produced by the confirmed data model."
            if state["output"]["events"]
            else "Nothing materialized yet — confirm a mapping first."
        )
    elif context == "data_jobs" or "transformation" in q or "job" in q or "sql" in q:
        answer = (
            f"Data jobs: {jobs['extractions']} extractions (staged tables), "
            f"{jobs['model_loads']} model load(s) so far. " + fmt_transformations()
        )
    elif "confidence" in q or "risk" in q or "review" in q:
        answer = fmt_transformations()
    elif "load" in q or "run" in q:
        last = state["processing"]["last_run"]
        answer = (
            f"{jobs['model_loads']} model load(s) recorded."
            + (f" Last: {last['at']} via {last['trigger']}." if last else "")
            + " Use Run now on the Data load page to rebuild the event log from the staged sources."
        )
    else:
        # process-metric question? fall through to the L5 NL layer when a log exists
        if state["output"]["events"]:
            try:
                from api.nlq import answer_question

                with EventLogStore(ws["db_path"]) as store:
                    df = store.sql("SELECT * FROM event_log ORDER BY case_id, timestamp")
                spec = storage.load_spec(ws)
                nl = answer_question(
                    question, df, [a.name for a in spec.activities] if spec else [], None
                )
                return {"answer": nl["answer"], "scope": "process analysis",
                        "engine": "process-nlq", "citations": nl["citations"]}
            except Exception:
                pass
        answer = (
            f"Here's the current load at a glance: {jobs['extractions']} staged tables, "
            f"{jobs['transformations']} AI-drafted transformations, "
            f"{jobs['model_loads']} load(s), data model {model['status']}, "
            f"{state['output']['objects']} cases / {state['output']['events']} events. "
            "Click an element on the canvas to scope your question to it."
        )

    return {"answer": answer, "scope": scope, "engine": "deterministic"}
