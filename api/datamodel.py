"""Data model (Celonis "Model" tab equivalent): tables, foreign keys, identifiers.

Everything is derived by the agent — relationships are parsed out of the confirmed
mapping's SQL joins and completed by key-column matching across the staged tables;
identifiers (unique-row keys, what Celonis needs for delta model loads) are probed
directly against the data. The human can add/remove relationships and change
identifiers; edits are persisted with their origin so AI vs human provenance stays
visible (§1.6).
"""

from __future__ import annotations

import json
import re

from engine.db import EventLogStore
from api import storage

# columns that act as document/master keys in SAP-ish schemas — used for FK matching
_KEY_HINTS = ("EBELN", "EBELP", "BELNR", "LIFNR", "MATNR", "MBLNR", "AWKEY", "OBJECTID",
              "GJAHR", "BUKRS", "AUGBL", "CASE_ID")

_JOIN_RE = re.compile(
    r"JOIN\s+(src_\w+)\s+(\w+)\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", re.IGNORECASE
)
_FROM_RE = re.compile(r"FROM\s+(src_\w+)\s+(\w+)", re.IGNORECASE)


def _model_file(ws: dict):
    return ws["dir"] / "datamodel.json"


def _write_json(path, data: dict) -> None:
    # atomic-ish: write a sibling temp file, then replace (single-process v1)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


_REL_FIELDS = ("from_table", "from_column", "to_table", "to_column")


def validate_relationship(r: dict) -> dict:
    """Normalize a client-supplied relationship; raise ValueError on bad shape."""
    if not isinstance(r, dict):
        raise ValueError("each relationship must be an object")
    missing = [f for f in _REL_FIELDS if not isinstance(r.get(f), str) or not r[f].strip()]
    if missing:
        raise ValueError(f"relationship is missing fields: {missing}")
    out = {f: r[f].strip().upper() for f in _REL_FIELDS}
    out["origin"] = r.get("origin", "human")
    out["via"] = r.get("via", "added by human")
    return out


def _rel_key(r: dict) -> str:
    a = f"{r['from_table']}.{r['from_column']}"
    b = f"{r['to_table']}.{r['to_column']}"
    return "|".join(sorted([a, b]))


def derive_relationships(ws: dict, schema: dict) -> list[dict]:
    """Agent derivation: joins used by the mapping SQL first, then key-column matching."""
    relationships: list[dict] = []
    seen: set[str] = set()

    def add(ft, fc, tt, tc, via: str) -> None:
        rel = {
            "from_table": ft.upper(), "from_column": fc.upper(),
            "to_table": tt.upper(), "to_column": tc.upper(),
            "origin": "agent", "via": via,
        }
        key = _rel_key(rel)
        if key not in seen and rel["from_table"] != rel["to_table"]:
            seen.add(key)
            relationships.append(rel)

    # 1) joins the mapping actually executes
    spec = storage.load_spec(ws)
    if spec:
        for fragment in [a.sql for a in spec.activities] + [a.sql for a in spec.attributes]:
            aliases = {alias: table.removeprefix("src_")
                       for table, alias in _FROM_RE.findall(fragment)}
            aliases.update({alias: table.removeprefix("src_")
                            for table, alias, *_ in _JOIN_RE.findall(fragment)})
            for _table, _alias, a1, c1, a2, c2 in _JOIN_RE.findall(fragment):
                t1, t2 = aliases.get(a1), aliases.get(a2)
                if t1 and t2:
                    add(t1, c1, t2, c2, "used by the mapping SQL")

    # 2) shared key columns across staged tables (proposed joins, Celonis-FK style)
    tables = {name: {c["name"].upper() for c in info["columns"]} for name, info in schema.items()}
    names = sorted(tables)
    for i, t1 in enumerate(names):
        for t2 in names[i + 1:]:
            for col in tables[t1] & tables[t2]:
                if col in _KEY_HINTS:
                    add(t1, col, t2, col, "shared key column")

    # 3) the event log's case id ties back to the case source table
    if spec and spec.status == "confirmed":
        case_source = spec.case_id.source.split(".")[0]
        matched = next((t for t in tables if case_source.upper() in t), None)
        if matched:
            col = "EBELN" if "EBELN" in tables[matched] else next(iter(tables[matched]), "")
            if col:
                add("EVENT_LOG", "CASE_ID", matched, col, "case id definition")

    return relationships


def probe_identifiers(ws: dict, schema: dict) -> dict:
    """For each staged table: candidate unique-row columns (agent-probed on real data)."""
    result: dict = {}
    with EventLogStore(ws["db_path"]) as store:
        for table, info in schema.items():
            row_count = info["row_count"]
            candidates = []
            preferred = [c["name"] for c in info["columns"]
                         if c["name"].upper() in _KEY_HINTS] + \
                        [c["name"] for c in info["columns"]
                         if c["name"].upper() not in _KEY_HINTS]
            for col in preferred[:12]:
                quoted = col.replace('"', '""')  # column names come from user CSV headers
                try:
                    distinct = store.sql(
                        f'SELECT count(DISTINCT "{quoted}") AS d FROM src_{table.lower()}'
                    ).iloc[0]["d"]
                except Exception:
                    continue
                if row_count and int(distinct) == int(row_count):
                    candidates.append(col)
            result[table] = {
                "candidates": candidates,
                "identifier": candidates[0] if candidates else None,
                "origin": "agent" if candidates else None,
            }
    return result


def build_datamodel(ws: dict) -> dict:
    with EventLogStore(ws["db_path"]) as store:
        schema = store.schema_summary(sample_rows=0)
        has_log = not store.sql(
            "SELECT table_name FROM information_schema.tables WHERE table_name='event_log'"
        ).empty

    spec = storage.load_spec(ws)
    # The agent layer is keyed to what it was derived from; when the spec advances or
    # tables are (un)staged, it re-derives and merges — human edits always survive,
    # and a premature first look can never freeze a stale model (review 2026-07-05).
    fingerprint = {
        "spec_version": spec.version if spec else None,
        "spec_status": spec.status if spec else None,
        "tables": sorted(schema),
    }

    saved = None
    if _model_file(ws).exists():
        saved = json.loads(_model_file(ws).read_text(encoding="utf-8"))

    if saved is None or saved.get("fingerprint") != fingerprint:
        fresh_rels = derive_relationships(ws, schema)
        fresh_ids = probe_identifiers(ws, schema)
        prior = saved or {"relationships": [], "identifiers": {},
                          "deleted_agent_keys": [], "updated_by": "AI modelling agent"}
        deleted = set(prior.get("deleted_agent_keys", []))
        human_rels = [r for r in prior["relationships"] if r.get("origin") == "human"]
        human_keys = {_rel_key(r) for r in human_rels}
        merged_rels = [
            r for r in fresh_rels if _rel_key(r) not in deleted and _rel_key(r) not in human_keys
        ] + human_rels
        merged_ids = fresh_ids
        for table, entry in prior.get("identifiers", {}).items():
            if entry.get("origin") == "human":
                merged_ids.setdefault(table, {"candidates": []})
                merged_ids[table]["identifier"] = entry.get("identifier")
                merged_ids[table]["origin"] = "human"
        saved = {
            "relationships": merged_rels,
            "identifiers": merged_ids,
            "deleted_agent_keys": sorted(deleted),
            "updated_by": prior.get("updated_by", "AI modelling agent"),
            "fingerprint": fingerprint,
        }
        _write_json(_model_file(ws), saved)

    tables = [
        {
            "name": name,
            "role": "source",
            "row_count": info["row_count"],
            "columns": [c["name"] for c in info["columns"]],
            "identifier": saved["identifiers"].get(name, {}).get("identifier"),
            "identifier_origin": saved["identifiers"].get(name, {}).get("origin"),
            "identifier_candidates": saved["identifiers"].get(name, {}).get("candidates", []),
        }
        for name, info in schema.items()
    ]
    if has_log:
        with EventLogStore(ws["db_path"]) as store:
            n = store.count()
        tables.append(
            {"name": "EVENT_LOG", "role": "event_log", "row_count": n,
             "columns": ["case_id", "activity", "timestamp", "vendor", "net_value"],
             "identifier": None, "identifier_origin": None, "identifier_candidates": []}
        )

    missing = [t["name"] for t in tables if t["role"] == "source" and not t["identifier"]]
    return {
        "tables": tables,
        "relationships": saved["relationships"],
        "missing_identifiers": missing,
        "updated_by": saved.get("updated_by"),
    }


def save_datamodel_edits(ws: dict, relationships: list[dict] | None,
                         identifiers: dict | None, updated_by: str) -> dict:
    build_datamodel(ws)  # ensure the saved file exists and is current before editing
    saved = json.loads(_model_file(ws).read_text(encoding="utf-8"))
    if relationships is not None:
        validated = []
        for r in relationships:
            v = validate_relationship(r)
            if v["origin"] != "agent":
                v["origin"] = "human"
                v.setdefault("via", f"added by {updated_by}")
            validated.append(v)
        # agent rows the human removed must not resurface on the next re-derive
        new_keys = {_rel_key(r) for r in validated}
        removed_agent = {
            _rel_key(r) for r in saved["relationships"]
            if r.get("origin") == "agent" and _rel_key(r) not in new_keys
        }
        saved["deleted_agent_keys"] = sorted(
            set(saved.get("deleted_agent_keys", [])) | removed_agent
        )
        saved["relationships"] = validated
    if identifiers is not None:
        for table, ident in identifiers.items():
            entry = saved["identifiers"].setdefault(
                table, {"candidates": [], "identifier": None, "origin": None}
            )
            entry["identifier"] = ident
            entry["origin"] = "human"
    saved["updated_by"] = updated_by
    _write_json(_model_file(ws), saved)
    return build_datamodel(ws)


def rederive(ws: dict) -> dict:
    """Throw away edits and let the agent re-derive the whole model."""
    _model_file(ws).unlink(missing_ok=True)
    return build_datamodel(ws)
