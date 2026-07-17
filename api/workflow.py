"""DiagramIQ workflow automation — SAP Signavio Process Governance equivalent.

An editable, executable workflow: a trigger, a set of steps (user task, approval,
notification, automation, condition, end), assignees/roles, and per-step forms. A
workflow can be authored from scratch or generated from a repository BPMN model
(each task becomes a step). Once activated it can be started as a *case* (instance)
that progresses through the steps — user/approval steps wait for a person; other
steps run automatically; conditions branch on the case variables collected by forms.

Rules (enforced, tested):
- Step ids are unique; every routing target references a real step or "end".
- Only an active workflow can be started; a draft cannot.
- Approval steps require an approve/reject decision and route on it.
- A workflow with running cases cannot be deleted.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

STEP_TYPES = ("task", "approval", "notification", "automation", "condition", "end")
_HUMAN = ("task", "approval")
_TERMINAL = (None, "", "end")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dg_dir(ws: dict):
    p = ws["dir"] / "diagrams"
    p.mkdir(exist_ok=True)
    return p


def _wf_file(ws: dict):
    return ws["dir"] / "workflows.json"


def _inst_file(ws: dict):
    return ws["dir"] / "workflow_instances.json"


def _load(path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_workflows(ws):
    return _load(_wf_file(ws))


def _save_workflows(ws, wfs):
    _save(_wf_file(ws), wfs)


def _load_instances(ws):
    return _load(_inst_file(ws))


def _save_instances(ws, insts):
    _save(_inst_file(ws), insts)


def _get_wf(ws, wf_id) -> dict:
    wf = next((w for w in _load_workflows(ws) if w["id"] == wf_id), None)
    if wf is None:
        raise HTTPException(404, f"no workflow {wf_id!r}")
    return wf


def _validate_steps(steps: list[dict], start_step) -> None:
    ids = [s.get("id") for s in steps]
    if not all(ids) or len(ids) != len(set(ids)):
        raise HTTPException(400, "each step needs a unique, non-empty id")
    targets = set(ids) | {"end", "", None}
    for s in steps:
        t = s.get("type")
        if t not in STEP_TYPES:
            raise HTTPException(400, f"step {s.get('id')!r}: type must be one of "
                                     f"{STEP_TYPES}")
        if not (s.get("name") or "").strip():
            raise HTTPException(400, f"step {s.get('id')!r}: a name is required")
        if t == "approval":
            approval_targets = set(ids) | {"end"}  # both branches must be explicit
            for key in ("on_approve", "on_reject"):
                if s.get(key) not in approval_targets:
                    raise HTTPException(
                        400, f"step {s.get('id')!r}: {key} must reference a step or 'end'")
        elif t == "condition":
            for c in s.get("conditions", []):
                if c.get("next") not in targets:
                    raise HTTPException(
                        400, f"step {s.get('id')!r}: a condition target is invalid")
            if s.get("default_next") not in targets:
                raise HTTPException(
                    400, f"step {s.get('id')!r}: default_next is invalid")
        elif t != "end":
            if s.get("next") not in targets:
                raise HTTPException(
                    400, f"step {s.get('id')!r}: next must reference a step or 'end'")
    if steps and start_step not in {s["id"] for s in steps}:
        raise HTTPException(400, "start_step must reference an existing step")


def _cond_matches(cond: dict, variables: dict) -> bool:
    left = variables.get(cond.get("var", ""))
    op = cond.get("op", "==")
    right = cond.get("value")
    if op == "truthy":
        return bool(left)
    if op == "contains":
        return str(right) in str(left or "")
    if op in (">", "<"):
        try:
            lf, rf = float(left), float(right)
        except (TypeError, ValueError):
            return False
        return lf > rf if op == ">" else lf < rf
    if op == "!=":
        return str(left) != str(right)
    return str(left) == str(right)  # default ==


def _next_after(step: dict, decision=None, variables=None) -> str | None:
    t = step["type"]
    if t == "approval":
        return step.get("on_approve" if decision == "approve" else "on_reject")
    if t == "condition":
        for c in step.get("conditions", []):
            if _cond_matches(c, variables or {}):
                return c.get("next")
        return step.get("default_next")
    return step.get("next")


def _advance(inst: dict, wf: dict) -> None:
    """Run automatic steps until a human step or the end is reached."""
    steps = {s["id"]: s for s in wf["steps"]}
    for _ in range(200):  # loop guard against a mis-wired cycle
        cur = inst["current_step"]
        if cur in _TERMINAL or cur not in steps:
            inst["status"] = "completed"
            inst["current_step"] = None
            inst["completed_at"] = _now()
            return
        step = steps[cur]
        if step["type"] in _HUMAN:
            return  # wait for a person
        inst["history"].append({
            "step_id": cur, "step_name": step["name"], "type": step["type"],
            "action": "executed", "by": "system", "at": _now(), "data": {}})
        if step["type"] == "end":
            inst["status"] = "completed"
            inst["current_step"] = None
            inst["completed_at"] = _now()
            return
        inst["current_step"] = _next_after(step, variables=inst["variables"])
    inst["status"] = "completed"
    inst["current_step"] = None


def _decorate(inst: dict, wf: dict) -> dict:
    steps = {s["id"]: s for s in wf["steps"]}
    cur = steps.get(inst.get("current_step") or "")
    return {**inst,
            "current_step_name": cur["name"] if cur else None,
            "current_step_type": cur["type"] if cur else None,
            "current_assignee": cur.get("assignee") if cur else None,
            "current_form": cur.get("form", []) if cur else []}


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    trigger: dict = {}
    roles: list[str] = []
    steps: list[dict] = []
    start_step: str | None = None
    by: str = ""


class FromModelIn(BaseModel):
    by: str = ""


class StartIn(BaseModel):
    variables: dict = {}
    by: str = ""


class CompleteIn(BaseModel):
    data: dict = {}
    decision: str | None = None  # approval: "approve" | "reject"
    by: str = ""


def make_router(workspace_dep) -> APIRouter:
    router = APIRouter(prefix="/api/diagram/workflows")

    @router.get("")
    def list_workflows(ws: dict = Depends(workspace_dep)):
        wfs = _load_workflows(ws)
        insts = _load_instances(ws)
        out = []
        for w in wfs:
            wi = [i for i in insts if i["workflow_id"] == w["id"]]
            out.append({**w, "step_count": len(w["steps"]),
                        "instance_count": len(wi),
                        "running": sum(1 for i in wi if i["status"] == "running")})
        return {"workflows": out}

    @router.post("")
    def create_workflow(body: WorkflowIn, ws: dict = Depends(workspace_dep)):
        if not body.name.strip():
            raise HTTPException(400, "a workflow name is required")
        _validate_steps(body.steps, body.start_step)
        wf = {"id": "wf_" + uuid.uuid4().hex[:10], "name": body.name.strip(),
              "description": body.description.strip(), "status": "draft",
              "trigger": body.trigger or {"type": "manual", "fields": []},
              "roles": body.roles, "steps": body.steps,
              "start_step": body.start_step or (body.steps[0]["id"] if body.steps
                                                else None),
              "model_id": None, "created_by": body.by.strip() or "unknown",
              "created_at": _now(), "updated_at": _now()}
        wfs = _load_workflows(ws)
        wfs.append(wf)
        _save_workflows(ws, wfs)
        return wf

    @router.post("/from-model/{model_id}")
    def from_model(model_id: str, body: FromModelIn,
                   ws: dict = Depends(workspace_dep)):
        """Generate a workflow skeleton from a repository BPMN model — one user-task
        step per activity, wired in document order. The author edits from there."""
        from api.diagram import _repo_load

        item = next((i for i in _repo_load(ws) if i["id"] == model_id), None)
        if item is None:
            raise HTTPException(404, f"no model {model_id!r}")
        tasks: list[str] = []
        versions = item.get("versions") or []
        if versions:
            path = _dg_dir(ws) / versions[-1]["file"]
            if path.exists():
                try:
                    root = ET.fromstring(path.read_text(encoding="utf-8"))
                    for el in root.iter():
                        tag = el.tag.split("}")[-1]
                        if (tag == "task" or tag.endswith("Task")
                                or tag in ("subProcess", "callActivity")):
                            nm = el.get("name")
                            if nm and nm.strip():
                                tasks.append(nm.strip())
                except ET.ParseError:
                    pass
        steps = []
        for i, nm in enumerate(tasks):
            steps.append({"id": f"s{i + 1}", "name": nm, "type": "task",
                          "assignee": "", "form": [],
                          "next": f"s{i + 2}" if i + 1 < len(tasks) else "end"})
        if not steps:
            steps = [{"id": "s1", "name": "Review", "type": "task",
                      "assignee": "", "form": [], "next": "end"}]
        wf = {"id": "wf_" + uuid.uuid4().hex[:10],
              "name": f"{item['name']} workflow", "description":
              f"Generated from the {item['level']} model \"{item['name']}\".",
              "status": "draft", "trigger": {"type": "manual", "fields": []},
              "roles": [], "steps": steps, "start_step": "s1",
              "model_id": model_id, "created_by": body.by.strip() or "DiagramIQ agent",
              "created_at": _now(), "updated_at": _now()}
        wfs = _load_workflows(ws)
        wfs.append(wf)
        _save_workflows(ws, wfs)
        return wf

    @router.get("/instances/{instance_id}")
    def get_instance(instance_id: str, ws: dict = Depends(workspace_dep)):
        inst = next((i for i in _load_instances(ws) if i["id"] == instance_id), None)
        if inst is None:
            raise HTTPException(404, f"no case {instance_id!r}")
        return _decorate(inst, _get_wf(ws, inst["workflow_id"]))

    @router.post("/instances/{instance_id}/complete")
    def complete_step(instance_id: str, body: CompleteIn,
                      ws: dict = Depends(workspace_dep)):
        instances = _load_instances(ws)
        inst = next((i for i in instances if i["id"] == instance_id), None)
        if inst is None:
            raise HTTPException(404, f"no case {instance_id!r}")
        if inst["status"] != "running":
            raise HTTPException(409, "this case is not running")
        wf = _get_wf(ws, inst["workflow_id"])
        steps = {s["id"]: s for s in wf["steps"]}
        step = steps.get(inst.get("current_step") or "")
        if step is None:
            raise HTTPException(400, "the case has no active step")
        if step["type"] == "approval" and body.decision not in ("approve", "reject"):
            raise HTTPException(400, "an approval step needs a decision "
                                     "(approve or reject)")
        inst["variables"].update(body.data or {})
        inst["history"].append({
            "step_id": step["id"], "step_name": step["name"], "type": step["type"],
            "action": body.decision or "completed",
            "by": body.by.strip() or "anonymous", "at": _now(),
            "data": body.data or {}})
        inst["current_step"] = _next_after(step, decision=body.decision,
                                           variables=inst["variables"])
        _advance(inst, wf)
        _save_instances(ws, instances)
        return _decorate(inst, wf)

    @router.get("/{wf_id}")
    def get_workflow(wf_id: str, ws: dict = Depends(workspace_dep)):
        return _get_wf(ws, wf_id)

    @router.put("/{wf_id}")
    def update_workflow(wf_id: str, body: WorkflowIn,
                        ws: dict = Depends(workspace_dep)):
        wfs = _load_workflows(ws)
        wf = next((w for w in wfs if w["id"] == wf_id), None)
        if wf is None:
            raise HTTPException(404, f"no workflow {wf_id!r}")
        if not body.name.strip():
            raise HTTPException(400, "a workflow name is required")
        _validate_steps(body.steps, body.start_step)
        wf.update({"name": body.name.strip(),
                   "description": body.description.strip(),
                   "trigger": body.trigger or wf.get("trigger"),
                   "roles": body.roles, "steps": body.steps,
                   "start_step": body.start_step or (body.steps[0]["id"]
                                                     if body.steps else None),
                   "updated_at": _now()})
        # editing a workflow returns it to draft — re-activate to run new cases
        wf["status"] = "draft"
        _save_workflows(ws, wfs)
        return wf

    @router.post("/{wf_id}/activate")
    def activate_workflow(wf_id: str, ws: dict = Depends(workspace_dep)):
        wfs = _load_workflows(ws)
        wf = next((w for w in wfs if w["id"] == wf_id), None)
        if wf is None:
            raise HTTPException(404, f"no workflow {wf_id!r}")
        if not wf["steps"]:
            raise HTTPException(400, "add at least one step before activating")
        _validate_steps(wf["steps"], wf["start_step"])
        wf["status"] = "active"
        wf["updated_at"] = _now()
        _save_workflows(ws, wfs)
        return wf

    @router.delete("/{wf_id}")
    def delete_workflow(wf_id: str, ws: dict = Depends(workspace_dep)):
        _get_wf(ws, wf_id)
        if any(i["workflow_id"] == wf_id and i["status"] == "running"
               for i in _load_instances(ws)):
            raise HTTPException(400, "this workflow has running cases — "
                                     "complete or cancel them first")
        wfs = [w for w in _load_workflows(ws) if w["id"] != wf_id]
        _save_workflows(ws, wfs)
        return {"deleted": wf_id}

    @router.post("/{wf_id}/start")
    def start_case(wf_id: str, body: StartIn, ws: dict = Depends(workspace_dep)):
        wf = _get_wf(ws, wf_id)
        if wf["status"] != "active":
            raise HTTPException(409, "activate the workflow before starting cases")
        if not wf["steps"]:
            raise HTTPException(400, "the workflow has no steps")
        inst = {"id": "wi_" + uuid.uuid4().hex[:10], "workflow_id": wf_id,
                "workflow_name": wf["name"], "status": "running",
                "current_step": wf["start_step"] or wf["steps"][0]["id"],
                "variables": dict(body.variables),
                "started_by": body.by.strip() or "anonymous", "started_at": _now(),
                "completed_at": None,
                "history": [{"step_id": None, "step_name": "Started",
                             "type": "trigger", "action": "started",
                             "by": body.by.strip() or "anonymous", "at": _now(),
                             "data": dict(body.variables)}]}
        _advance(inst, wf)
        instances = _load_instances(ws)
        instances.append(inst)
        _save_instances(ws, instances)
        return _decorate(inst, wf)

    @router.get("/{wf_id}/instances")
    def list_instances(wf_id: str, ws: dict = Depends(workspace_dep)):
        _get_wf(ws, wf_id)
        wf = _get_wf(ws, wf_id)
        insts = [i for i in _load_instances(ws) if i["workflow_id"] == wf_id]
        insts.sort(key=lambda i: i["started_at"], reverse=True)
        return {"instances": [_decorate(i, wf) for i in insts]}

    return router
