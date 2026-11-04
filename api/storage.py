"""Multi-tenant workspace registry + per-workspace storage layout.

Layout (PROCESSIQ_DATA, default ./data):
  data/
    tenants.json                  # api_key -> workspace record
    <workspace_id>/
      warehouse.duckdb            # staged sources + materialized event log
      spec.yaml                   # current mapping spec
      spec_v<N>.yaml              # spec history (audit trail, §1.6)

v1 keys are stored server-side in plain JSON — swap for hashed keys + a real DB before
opening sign-ups to strangers.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from pathlib import Path

from modelling.spec import MappingSpec


def data_root() -> Path:
    root = Path(os.environ.get("PROCESSIQ_DATA", Path(__file__).parent.parent / "data"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tenants_file() -> Path:
    return data_root() / "tenants.json"


def _load_tenants() -> dict:
    path = _tenants_file()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_json(path: Path, data) -> None:
    # atomic-ish: write a sibling temp file, then replace (single-process v1)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _save_tenants(tenants: dict) -> None:
    _write_json(_tenants_file(), tenants)


def create_workspace(name: str) -> dict:
    name = name.strip()
    if not name or not re.match(r"^[\w .\-]{1,60}$", name):
        raise ValueError("workspace name must be 1-60 characters (letters, digits, . - _)")
    api_key = "piq_" + secrets.token_urlsafe(24)
    workspace_id = uuid.uuid4().hex[:12]
    tenants = _load_tenants()
    tenants[api_key] = {"id": workspace_id, "name": name}
    _save_tenants(tenants)
    (data_root() / workspace_id).mkdir(parents=True, exist_ok=True)
    return {"workspace_id": workspace_id, "name": name, "api_key": api_key}


def all_workspaces() -> list[dict]:
    """Every workspace as a ws dict (used by the scheduler's scan)."""
    out = []
    for record in _load_tenants().values():
        ws_dir = data_root() / record["id"]
        if ws_dir.exists():
            out.append({**record, "dir": ws_dir, "db_path": ws_dir / "warehouse.duckdb"})
    return out


def resolve_workspace(api_key: str | None) -> dict | None:
    if not api_key:
        return None
    record = _load_tenants().get(api_key)
    if not record:
        return None
    ws_dir = data_root() / record["id"]
    ws_dir.mkdir(parents=True, exist_ok=True)
    return {**record, "dir": ws_dir, "db_path": ws_dir / "warehouse.duckdb"}


def load_reference(workspace: dict) -> dict | None:
    """The SME-defined reference process (conformance target), if one was saved."""
    path = workspace["dir"] / "reference.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_reference(workspace: dict, activities: list[str], updated_by: str) -> dict:
    record = {"activities": activities, "updated_by": updated_by}
    _write_json(workspace["dir"] / "reference.json", record)
    return record


def load_signal_baseline(workspace: dict) -> dict | None:
    """The saved metric snapshot that drift Signals compare against (mining/signals.py)."""
    path = workspace["dir"] / "signal_baseline.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_signal_baseline(workspace: dict, snapshot: dict, captured_by: str) -> dict:
    record = {**snapshot, "captured_by": captured_by}
    _write_json(workspace["dir"] / "signal_baseline.json", record)
    return record


def load_connections(workspace: dict) -> list[dict]:
    path = workspace["dir"] / "connections.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_connections(workspace: dict, connections: list[dict]) -> None:
    _write_json(workspace["dir"] / "connections.json", connections)


def load_kpis(workspace: dict) -> list[dict]:
    path = workspace["dir"] / "kpis.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_kpis(workspace: dict, kpis: list[dict]) -> None:
    _write_json(workspace["dir"] / "kpis.json", kpis)


def spec_path(workspace: dict) -> Path:
    return workspace["dir"] / "spec.yaml"


def load_spec(workspace: dict) -> MappingSpec | None:
    path = spec_path(workspace)
    if not path.exists():
        return None
    spec = MappingSpec.from_yaml(path.read_text(encoding="utf-8"))
    # (re)generate transformation summaries for specs drafted before the current format
    from modelling.sql_explain import explain_sql

    for activity in spec.activities:
        if activity.sql and not activity.sql_explanation.startswith("Source"):
            activity.sql_explanation = explain_sql(activity.sql)
    return spec


def save_spec(workspace: dict, spec: MappingSpec) -> None:
    text = spec.to_yaml()
    spec_path(workspace).write_text(text, encoding="utf-8")
    # versioned history — every draft/answer/confirm leaves an auditable copy
    (workspace["dir"] / f"spec_v{spec.version}_{spec.status}.yaml").write_text(
        text, encoding="utf-8"
    )
