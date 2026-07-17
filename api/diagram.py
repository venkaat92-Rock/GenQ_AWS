"""DiagramIQ API — the second offering: BPMN 2.0 diagram intelligence.

Artifacts (discovery Excels, BPMN XMLs) live per workspace under <ws>/diagrams/.
Every conversion states which engine produced it (anthropic vs rule-based)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from diagramiq.service import (
    DiagramError,
    _ollama_reachable,
    bpmn_to_review_excel,
    excel_to_bpmn_xml,
    notes_to_discovery_excel,
    uplift_bpmn_xml,
    validate_bpmn_xml,
    visio_to_bpmn_xml,
    visio_vision_check,
)


def _settings_file(ws: dict):
    return ws["dir"] / "diagram_settings.json"


def _load_settings(ws: dict) -> dict:
    if _settings_file(ws).exists():
        return json.loads(_settings_file(ws).read_text(encoding="utf-8"))
    return {"provider": "auto", "anthropic_key": ""}


def _workspace_ai(ws: dict, settings: dict):
    """(client, model) from the workspace AI Model card — one AI backend for the
    whole tool. Used when the diagram provider is auto/anthropic and no
    diagram-specific key is set, so Bedrock-on-IAM-role (or whatever the AI
    card selects) powers diagram intelligence too. Returns (None, None) when
    the workspace AI is unconfigured."""
    if settings.get("provider", "auto") not in ("auto", "anthropic"):
        return None, None
    if settings.get("anthropic_key"):  # explicit diagram key wins (legacy)
        return None, None
    from api.ai import load_ai_settings
    from modelling.llm import DEFAULT_MODEL, client_for_settings

    ai = load_ai_settings(ws)
    try:
        return client_for_settings(ai), ai.get("model") or DEFAULT_MODEL
    except RuntimeError:
        return None, None


# ---- BPMN repository (Signavio Process Manager equivalent) ----------------------------
#
# Process levels follow the Signavio hierarchy: L1 process landscape, L2 process groups,
# L3 end-to-end processes, L4 detailed BPMN (what conversions produce). Items carry
# version history with author + revision comment, and a submit -> approve/reject
# workflow with a full audit trail.

LEVELS = ("L1", "L2", "L3", "L4")
STATUSES = ("draft", "in_review", "approved", "rejected")

# Per-folder access levels (Signavio-style provisioning). Members live in the
# workspace-level store (api/members.py) shared with ProcessIQ; a member without
# the "diagramiq" license has no access to this offering. Until the first member
# is added, access control is off and everyone has full rights.
ACCESS_LEVELS = ("view", "edit", "manage")
_ACCESS_RANK = {"view": 1, "edit": 2, "manage": 3}


def _repo_file(ws: dict):
    return _dg_dir(ws) / "repo.json"


def _repo_load(ws: dict) -> list[dict]:
    if _repo_file(ws).exists():
        return json.loads(_repo_file(ws).read_text(encoding="utf-8"))
    return []


def _repo_save(ws: dict, items: list[dict]) -> None:
    tmp = _repo_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(_repo_file(ws))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_get(items: list[dict], item_id: str) -> dict:
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, f"no repository item {item_id!r}")
    return item


def register_bpmn(ws: dict, file_name: str, display_name: str, author: str,
                  comment: str, folder_id: str | None = None) -> dict:
    """Every generated BPMN becomes a governed L4 repository item (version 1, draft)."""
    items = _repo_load(ws)
    item = {
        "id": "r_" + uuid.uuid4().hex[:10],
        "name": display_name,
        "level": "L4",
        "parent_id": None,
        "folder_id": folder_id,
        "status": "draft",
        "owner": author,
        "approver": None,
        "attributes": {},
        "versions": [{"file": file_name, "author": author, "comment": comment,
                      "at": _now()}],
        "audit": [{"action": "created", "by": author, "comment": comment, "at": _now()}],
    }
    items.append(item)
    _repo_save(ws, items)
    return item


# ---- folders + members (Signavio-explorer structure and access provisioning) ----------

def _folders_file(ws: dict):
    return _dg_dir(ws) / "folders.json"


def _folders_load(ws: dict) -> list[dict]:
    if _folders_file(ws).exists():
        return json.loads(_folders_file(ws).read_text(encoding="utf-8"))
    return []


def _folders_save(ws: dict, folders: list[dict]) -> None:
    tmp = _folders_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(folders, indent=2), encoding="utf-8")
    tmp.replace(_folders_file(ws))


def _members_load(ws: dict) -> list[dict]:
    from api.members import members as workspace_members

    return workspace_members(ws)


def _member_of(members: list[dict], name: str | None) -> dict | None:
    from api.members import find_member

    return find_member(members, name)


def _folder_chain(folders: list[dict], folder_id: str | None) -> list[dict]:
    """The folder and its ancestors, nearest first."""
    by_id = {f["id"]: f for f in folders}
    chain: list[dict] = []
    cur = by_id.get(folder_id) if folder_id else None
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent_id")) if cur.get("parent_id") else None
    return chain


def _effective_access(user: str | None, folder_id: str | None,
                      folders: list[dict], members: list[dict]) -> str | None:
    """Access the user holds on a folder: view | edit | manage | None.

    No members defined -> access control is off, full rights for everyone.
    Admins always manage. Otherwise the nearest ancestor folder with an explicit
    access list decides; unrestricted paths fall back to the member's role."""
    if not members:
        return "manage"
    member = _member_of(members, user)
    if member is None:
        return None
    if member["role"] == "admin":
        return "manage"
    if "diagramiq" not in member.get("licenses", ["processiq", "diagramiq"]):
        return None  # no DiagramIQ license -> no access to this offering
    for folder in _folder_chain(folders, folder_id):
        if folder.get("access"):
            return folder["access"].get(member["name"])
    return "edit" if member["role"] == "editor" else "view"


def _require_access(ws: dict, user: str | None, folder_id: str | None,
                    needed: str) -> None:
    members = _members_load(ws)
    if not members:
        return
    got = _effective_access(user, folder_id, _folders_load(ws), members)
    if got is None or _ACCESS_RANK[got] < _ACCESS_RANK[needed]:
        raise HTTPException(
            403, f"{(user or 'anonymous').strip() or 'anonymous'} does not have "
                 f"{needed} access to this folder")


router = APIRouter(prefix="/api/diagram")

SAMPLE_NOTES = Path(__file__).parent.parent / "fixtures" / "diagram_sample_notes.txt"

_KIND_BY_SUFFIX = {".xlsx": "excel", ".bpmn": "bpmn", ".xml": "bpmn"}


def _dg_dir(ws: dict) -> Path:
    path = ws["dir"] / "diagrams"
    path.mkdir(exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w. -]", "_", Path(name).name).strip()
    if not name or name.startswith("."):
        raise HTTPException(400, "invalid file name")
    return name


def _stamped(base: str, suffix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%m%d-%H%M%S")
    return f"{base}_{stamp}{suffix}"


def _artifact_path(ws: dict, name: str) -> Path:
    path = _dg_dir(ws) / _safe_name(name)
    if not path.exists():
        raise HTTPException(404, f"no artifact named {name!r}")
    return path


async def _incoming_bpmn(ws: dict, file: UploadFile | None, artifact: str) -> tuple[str, str]:
    """XML content + base name from either an upload or a stored artifact."""
    if file is not None and file.filename:
        content = (await file.read()).decode("utf-8-sig", errors="replace")
        return content, Path(_safe_name(file.filename)).stem
    if artifact:
        path = _artifact_path(ws, artifact)
        return path.read_text(encoding="utf-8"), path.stem
    raise HTTPException(400, "provide a BPMN file upload or an artifact name")


class RepoItemIn(BaseModel):
    name: str
    level: str = "L4"
    parent_id: str | None = None
    folder_id: str | None = None
    xml: str
    author: str
    comment: str = "initial version"

class VersionIn(BaseModel):
    xml: str
    author: str
    comment: str = ""
    attributes: dict | None = None  # per-element custom attributes

class MetadataIn(BaseModel):
    name: str | None = None
    level: str | None = None
    parent_id: str | None = None
    clear_parent: bool = False
    folder_id: str | None = None
    clear_folder: bool = False
    by: str

class AdoptIn(BaseModel):
    file: str
    folder_id: str | None = None
    by: str = ""

class FolderIn(BaseModel):
    name: str
    parent_id: str | None = None
    by: str = ""

class FolderAccessIn(BaseModel):
    access: dict  # member name -> view | edit | manage (empty dict = unrestricted)
    by: str = ""

class SubmitIn(BaseModel):
    approver: str
    by: str

class DecisionIn(BaseModel):
    action: str  # approve | reject
    by: str
    comment: str = ""


def make_router(workspace_dep):
    """Bind the routes to the app's workspace auth dependency."""

    _INTERNAL_FILES = {"repo.json", "folders.json", "members.json"}

    @router.get("/artifacts")
    def list_artifacts(ws: dict = Depends(workspace_dep)):
        files = sorted((p for p in _dg_dir(ws).glob("*")
                        if p.name not in _INTERNAL_FILES),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "artifacts": [
                {
                    "name": f.name,
                    "kind": _KIND_BY_SUFFIX.get(f.suffix.lower(), "file"),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    ).isoformat(timespec="seconds"),
                }
                for f in files if f.is_file()
            ]
        }

    @router.get("/artifacts/{name}")
    def download_artifact(name: str, ws: dict = Depends(workspace_dep)):
        path = _artifact_path(ws, name)
        media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 if path.suffix == ".xlsx" else "application/xml")
        return FileResponse(path, media_type=media, filename=path.name)

    @router.get("/artifacts/{name}/xml")
    def artifact_xml(name: str, ws: dict = Depends(workspace_dep)):
        path = _artifact_path(ws, name)
        if path.suffix.lower() not in (".bpmn", ".xml"):
            raise HTTPException(400, "not a BPMN artifact")
        return {"name": name, "xml": path.read_text(encoding="utf-8")}

    # ---- repository: hierarchy, versions, approvals ---------------------------------

    @router.get("/repo")
    def repo_list(user: str | None = None, ws: dict = Depends(workspace_dep)):
        items = _repo_load(ws)
        folders = _folders_load(ws)
        members = _members_load(ws)
        order = {level: i for i, level in enumerate(LEVELS)}
        items.sort(key=lambda i: (order.get(i["level"], 9), i["name"].lower()))
        # once members exist, filter to folders the acting user may at least view
        if members:
            visible = {f["id"] for f in folders
                       if _effective_access(user, f["id"], folders, members)}
            folders = [f for f in folders if f["id"] in visible]
            root_ok = _effective_access(user, None, folders, members) is not None
            items = [i for i in items
                     if (i.get("folder_id") in visible
                         or (i.get("folder_id") is None and root_ok))]
        me = _member_of(members, user)
        return {
            "items": [
                {**i, "latest_file": i["versions"][-1]["file"],
                 "version_count": len(i["versions"])}
                for i in items
            ],
            "folders": folders,
            "members_enabled": bool(members),
            "my_role": me["role"] if me else None,
        }

    # ---- members: managed centrally at /api/members; read-only view here ------------

    @router.get("/repo/members")
    def members_list(ws: dict = Depends(workspace_dep)):
        return {"members": _members_load(ws)}

    # ---- folders (Signavio-explorer structure) ---------------------------------------

    @router.post("/repo/folders")
    def folder_create(body: FolderIn, ws: dict = Depends(workspace_dep)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "a folder name is required")
        folders = _folders_load(ws)
        if body.parent_id and not any(f["id"] == body.parent_id for f in folders):
            raise HTTPException(404, f"no folder {body.parent_id!r}")
        _require_access(ws, body.by, body.parent_id, "edit")
        siblings = [f for f in folders if f.get("parent_id") == body.parent_id]
        if any(f["name"].lower() == name.lower() for f in siblings):
            raise HTTPException(409, f"a folder named {name!r} already exists here")
        folder = {"id": "f_" + uuid.uuid4().hex[:10], "name": name,
                  "parent_id": body.parent_id, "access": {}}
        folders.append(folder)
        _folders_save(ws, folders)
        return folder

    @router.put("/repo/folders/{folder_id}")
    def folder_rename(folder_id: str, body: FolderIn,
                      ws: dict = Depends(workspace_dep)):
        folders = _folders_load(ws)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if folder is None:
            raise HTTPException(404, f"no folder {folder_id!r}")
        _require_access(ws, body.by, folder_id, "edit")
        if body.name.strip():
            folder["name"] = body.name.strip()
        _folders_save(ws, folders)
        return folder

    @router.delete("/repo/folders/{folder_id}")
    def folder_delete(folder_id: str, by: str = "",
                      ws: dict = Depends(workspace_dep)):
        folders = _folders_load(ws)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if folder is None:
            raise HTTPException(404, f"no folder {folder_id!r}")
        _require_access(ws, by, folder_id, "manage")
        if any(f.get("parent_id") == folder_id for f in folders):
            raise HTTPException(400, "the folder still contains subfolders")
        if any(i.get("folder_id") == folder_id for i in _repo_load(ws)):
            raise HTTPException(400, "the folder still contains diagrams — move them first")
        folders.remove(folder)
        _folders_save(ws, folders)
        return {"deleted": folder_id}

    @router.put("/repo/folders/{folder_id}/access")
    def folder_access(folder_id: str, body: FolderAccessIn,
                      ws: dict = Depends(workspace_dep)):
        """Admin provisioning: restrict a folder to specific members.

        An empty access map lifts the restriction (role defaults apply again)."""
        folders = _folders_load(ws)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if folder is None:
            raise HTTPException(404, f"no folder {folder_id!r}")
        members = _members_load(ws)
        if not members:
            raise HTTPException(400, "add workspace members before provisioning access")
        _require_access(ws, body.by, folder_id, "manage")
        cleaned: dict[str, str] = {}
        for raw_name, level in body.access.items():
            member = _member_of(members, raw_name)
            if member is None:
                raise HTTPException(400, f"{raw_name!r} is not a workspace member")
            if level not in ACCESS_LEVELS:
                raise HTTPException(400, f"access must be one of {ACCESS_LEVELS}")
            cleaned[member["name"]] = level
        folder["access"] = cleaned
        _folders_save(ws, folders)
        return folder

    # ---- model reporting (Signavio Model Reporting equivalent) ----------------------
    # NOTE: declared before /repo/{item_id} so "report" is not read as an item id.

    @router.get("/repo/report")
    def repo_report(ws: dict = Depends(workspace_dep)):
        """How many models exist, by level and status; the approval funnel; revisions;
        pending approvals, recent approvals, and recent governance activity."""
        items = _repo_load(ws)
        by_level = {lvl: 0 for lvl in LEVELS}
        by_status = {st: 0 for st in STATUSES}
        by_owner: dict[str, int] = {}
        by_month: dict[str, int] = {}
        total_versions = 0
        pending: list[dict] = []
        approved: list[dict] = []
        activity: list[dict] = []
        for it in items:
            by_level[it["level"]] = by_level.get(it["level"], 0) + 1
            by_status[it["status"]] = by_status.get(it["status"], 0) + 1
            by_owner[it["owner"]] = by_owner.get(it["owner"], 0) + 1
            versions = it.get("versions") or []
            total_versions += len(versions)
            created = (versions[0]["at"] if versions else "")[:7]  # YYYY-MM
            if created:
                by_month[created] = by_month.get(created, 0) + 1
            latest = versions[-1] if versions else {}
            row = {"id": it["id"], "name": it["name"], "level": it["level"],
                   "status": it["status"], "owner": it["owner"],
                   "approver": it.get("approver"), "versions": len(versions),
                   "updated_at": latest.get("at")}
            if it["status"] == "in_review":
                pending.append(row)
            if it["status"] == "approved":
                appr = next((a for a in reversed(it.get("audit", []))
                             if a["action"] == "approved"), None)
                approved.append({**row, "approved_by": appr["by"] if appr else None,
                                 "approved_at": (appr["at"] if appr
                                                 else latest.get("at"))})
            for a in it.get("audit", []):
                activity.append({**a, "model": it["name"], "model_id": it["id"]})
        activity.sort(key=lambda a: a.get("at", ""), reverse=True)
        approved.sort(key=lambda r: r.get("approved_at") or "", reverse=True)
        total = len(items)
        return {
            "total": total,
            "total_versions": total_versions,
            "by_level": by_level,
            "by_status": by_status,
            "by_owner": sorted(
                [{"owner": k, "count": v} for k, v in by_owner.items()],
                key=lambda x: x["count"], reverse=True),
            "by_month": [{"month": k, "count": by_month[k]}
                         for k in sorted(by_month)],
            "approval_rate": round(by_status.get("approved", 0) / total * 100, 1)
            if total else 0.0,
            "pending_approvals": pending,
            "recently_approved": approved[:20],
            "recent_activity": activity[:30],
        }

    @router.get("/repo/{item_id}")
    def repo_detail(item_id: str, ws: dict = Depends(workspace_dep)):
        item = _repo_get(_repo_load(ws), item_id)
        return {**item, "latest_file": item["versions"][-1]["file"]}

    @router.post("/repo/adopt")
    def repo_adopt(body: AdoptIn, ws: dict = Depends(workspace_dep)):
        """Bring an existing BPMN artifact (pre-repository conversions) under governance.

        Idempotent: if any item already carries this file in its history, return it."""
        file_name = _safe_name(body.file)
        items = _repo_load(ws)
        existing = next(
            (i for i in items if any(v["file"] == file_name for v in i["versions"])),
            None,
        )
        if existing is not None:
            return {**existing, "latest_file": existing["versions"][-1]["file"]}
        path = _artifact_path(ws, file_name)
        if path.suffix.lower() not in (".bpmn", ".xml"):
            raise HTTPException(400, "only BPMN artifacts can be adopted")
        try:
            validate_bpmn_xml(path.read_text(encoding="utf-8"))
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        if body.folder_id and not any(
                f["id"] == body.folder_id for f in _folders_load(ws)):
            raise HTTPException(404, f"no folder {body.folder_id!r}")
        _require_access(ws, body.by, body.folder_id, "edit")
        display = re.sub(r"_\d{4}-\d{6}$", "", path.stem).replace("_", " ").title()
        item = register_bpmn(ws, file_name, display, "DiagramIQ agent",
                             "adopted from artifacts", folder_id=body.folder_id)
        return {**item, "latest_file": file_name}


    @router.post("/repo/items")
    def repo_create(body: RepoItemIn, ws: dict = Depends(workspace_dep)):
        """Create a diagram directly in the repository (e.g. an L1-L3 drawn in the editor)."""
        if body.level not in LEVELS:
            raise HTTPException(400, f"level must be one of {LEVELS}")
        if not body.author.strip():
            raise HTTPException(400, "an author name is required")
        try:
            validate_bpmn_xml(body.xml)  # parse check
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        items = _repo_load(ws)
        if body.parent_id:
            parent = _repo_get(items, body.parent_id)
            if LEVELS.index(parent["level"]) >= LEVELS.index(body.level):
                raise HTTPException(400, "the parent must sit on a higher level "
                                    "(e.g. an L3 process under an L2 group)")
        if body.folder_id and not any(
                f["id"] == body.folder_id for f in _folders_load(ws)):
            raise HTTPException(404, f"no folder {body.folder_id!r}")
        _require_access(ws, body.author, body.folder_id, "edit")
        out = _dg_dir(ws) / _stamped(_safe_name(body.name).replace(" ", "_"), ".bpmn")
        out.write_text(body.xml, encoding="utf-8")
        item = register_bpmn(ws, out.name, body.name.strip(), body.author.strip(),
                             body.comment, folder_id=body.folder_id)
        # register_bpmn defaults to L4/no-parent; apply requested placement
        items = _repo_load(ws)
        stored = _repo_get(items, item["id"])
        stored["level"], stored["parent_id"] = body.level, body.parent_id
        _repo_save(ws, items)
        return {**stored, "latest_file": out.name}


    @router.post("/repo/{item_id}/versions")
    def repo_save_version(item_id: str, body: VersionIn,
                          ws: dict = Depends(workspace_dep)):
        if not body.author.strip():
            raise HTTPException(400, "an author name is required")
        try:
            validate_bpmn_xml(body.xml)
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        items = _repo_load(ws)
        item = _repo_get(items, item_id)
        _require_access(ws, body.author, item.get("folder_id"), "edit")
        base = _safe_name(item["name"]).replace(" ", "_")
        out = _dg_dir(ws) / _stamped(f"{base}_v{len(item['versions']) + 1}", ".bpmn")
        out.write_text(body.xml, encoding="utf-8")
        item["versions"].append({"file": out.name, "author": body.author.strip(),
                                 "comment": body.comment, "at": _now()})
        if body.attributes is not None:
            item["attributes"] = body.attributes
        item["status"] = "draft"  # edits always require a fresh approval
        item["audit"].append({"action": "new_version", "by": body.author.strip(),
                              "comment": body.comment, "at": _now()})
        _repo_save(ws, items)
        return {**item, "latest_file": out.name}


    @router.put("/repo/{item_id}")
    def repo_update(item_id: str, body: MetadataIn, ws: dict = Depends(workspace_dep)):
        if not body.by.strip():
            raise HTTPException(400, "an author name is required")
        items = _repo_load(ws)
        item = _repo_get(items, item_id)
        _require_access(ws, body.by, item.get("folder_id"), "edit")
        if body.level is not None:
            if body.level not in LEVELS:
                raise HTTPException(400, f"level must be one of {LEVELS}")
            item["level"] = body.level
        if body.clear_parent:
            item["parent_id"] = None
        elif body.parent_id is not None:
            parent = _repo_get(items, body.parent_id)
            if parent["id"] == item["id"]:
                raise HTTPException(400, "an item cannot be its own parent")
            if LEVELS.index(parent["level"]) >= LEVELS.index(item["level"]):
                raise HTTPException(400, "the parent must sit on a higher level")
            item["parent_id"] = body.parent_id
        if body.clear_folder:
            _require_access(ws, body.by, None, "edit")
            item["folder_id"] = None
        elif body.folder_id is not None:
            if not any(f["id"] == body.folder_id for f in _folders_load(ws)):
                raise HTTPException(404, f"no folder {body.folder_id!r}")
            _require_access(ws, body.by, body.folder_id, "edit")
            item["folder_id"] = body.folder_id
        if body.name:
            item["name"] = body.name.strip()
        item["audit"].append({"action": "metadata", "by": body.by.strip(),
                              "comment": "", "at": _now()})
        _repo_save(ws, items)
        return item


    @router.post("/repo/{item_id}/submit")
    def repo_submit(item_id: str, body: SubmitIn, ws: dict = Depends(workspace_dep)):
        if not body.approver.strip() or not body.by.strip():
            raise HTTPException(400, "submitter and approver names are required")
        items = _repo_load(ws)
        item = _repo_get(items, item_id)
        _require_access(ws, body.by, item.get("folder_id"), "edit")
        if item["status"] == "in_review":
            raise HTTPException(409, "already awaiting approval")
        item["status"] = "in_review"
        item["approver"] = body.approver.strip()
        item["audit"].append({"action": "submitted", "by": body.by.strip(),
                              "comment": f"approver: {body.approver.strip()}",
                              "at": _now()})
        _repo_save(ws, items)
        return item


    @router.post("/repo/{item_id}/decide")
    def repo_decide(item_id: str, body: DecisionIn, ws: dict = Depends(workspace_dep)):
        if body.action not in ("approve", "reject"):
            raise HTTPException(400, "action must be approve or reject")
        if not body.by.strip():
            raise HTTPException(400, "the approver's name is required")
        items = _repo_load(ws)
        item = _repo_get(items, item_id)
        _require_access(ws, body.by, item.get("folder_id"), "edit")
        if item["status"] != "in_review":
            raise HTTPException(409, "only items awaiting approval can be decided")
        item["status"] = "approved" if body.action == "approve" else "rejected"
        item["audit"].append({"action": item["status"], "by": body.by.strip(),
                              "comment": body.comment, "at": _now()})
        _repo_save(ws, items)
        return item

    @router.get("/settings")
    def get_settings(ws: dict = Depends(workspace_dep)):
        settings = _load_settings(ws)
        import os

        return {
            "provider": settings.get("provider", "auto"),
            "has_workspace_key": bool(settings.get("anthropic_key")),
            "has_server_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "ollama_reachable": _ollama_reachable(),
        }

    @router.put("/settings")
    def put_settings(body: dict, ws: dict = Depends(workspace_dep)):
        provider = body.get("provider", "auto")
        if provider not in ("auto", "anthropic", "ollama", "rule-based"):
            raise HTTPException(400, "provider must be auto/anthropic/ollama/rule-based")
        settings = _load_settings(ws)
        settings["provider"] = provider
        if "anthropic_key" in body:
            settings["anthropic_key"] = (body.get("anthropic_key") or "").strip()
        _settings_file(ws).write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return get_settings(ws)

    @router.post("/notes")
    async def notes(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        process_name: str = Form(default="Discovered Process"),
        use_sample: bool = Form(default=False),
    ):
        """Transcript/notes -> Process Discovery Excel (provider per workspace settings)."""
        if use_sample:
            source, base = SAMPLE_NOTES, "sample_procurement"
        elif file is not None and file.filename:
            base = Path(_safe_name(file.filename)).stem
            source = _dg_dir(ws) / f"upload_{_safe_name(file.filename)}"
            source.write_bytes(await file.read())
        else:
            raise HTTPException(400, "upload a .txt/.docx/.pdf file or set use_sample")
        settings = _load_settings(ws)
        ai_client, ai_model = _workspace_ai(ws, settings)
        out = _dg_dir(ws) / _stamped(f"{base}_discovery", ".xlsx")
        try:
            summary = notes_to_discovery_excel(
                Path(source), out, process_name,
                provider=settings.get("provider", "auto"),
                api_key=settings.get("anthropic_key", ""),
                client=ai_client, model=ai_model,
            )
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        finally:
            if not use_sample:
                Path(source).unlink(missing_ok=True)
        return summary

    @router.post("/excel-to-bpmn")
    async def excel_to_bpmn(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        artifact: str = Form(default=""),
        process_name: str = Form(default=""),
    ):
        """Process Discovery Excel -> Signavio-ready BPMN 2.0 XML + validation."""
        if file is not None and file.filename:
            base = Path(_safe_name(file.filename)).stem
            source = _dg_dir(ws) / f"upload_{_safe_name(file.filename)}"
            source.write_bytes(await file.read())
            cleanup = True
        elif artifact:
            source = _artifact_path(ws, artifact)
            base = source.stem.replace("_discovery", "")
            cleanup = False
        else:
            raise HTTPException(400, "upload an .xlsx or reference a stored artifact")
        try:
            xml = excel_to_bpmn_xml(Path(source), process_name)
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        finally:
            if cleanup:
                Path(source).unlink(missing_ok=True)
        out = _dg_dir(ws) / _stamped(f"{base}", ".bpmn")
        out.write_text(xml, encoding="utf-8")
        issues = validate_bpmn_xml(xml)
        item = register_bpmn(ws, out.name, base.replace("_", " ").title(),
                             "DiagramIQ agent", "generated from discovery Excel")
        return {"bpmn": out.name, "issues": issues,
                "issue_count": len(issues), "repo_id": item["id"]}

    @router.post("/visio")
    async def visio(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        image: UploadFile = File(default=None),
        process_name: str = Form(default=""),
        preserve_layout: bool = Form(default=True),
        use_sample: bool = Form(default=False),
    ):
        """Visio .vsdx -> Signavio-ready BPMN 2.0 (+ optional AI image cross-check)."""
        if use_sample:
            source = Path(__file__).parent.parent / "fixtures" / "visio" / "ecc_workflow.vsdx"
            base, cleanup = "ecc_workflow_visio", False
        else:
            if file is None or not file.filename:
                raise HTTPException(400, "upload a .vsdx file or set use_sample")
            base = Path(_safe_name(file.filename)).stem + "_visio"
            source = _dg_dir(ws) / f"upload_{_safe_name(file.filename)}"
            source.write_bytes(await file.read())
            cleanup = True
        try:
            xml = visio_to_bpmn_xml(Path(source), process_name, preserve_layout)
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        finally:
            if cleanup:
                Path(source).unlink(missing_ok=True)

        out = _dg_dir(ws) / _stamped(base, ".bpmn")
        out.write_text(xml, encoding="utf-8")
        issues = validate_bpmn_xml(xml)
        item = register_bpmn(ws, out.name, base.replace("_", " ").title(),
                             "DiagramIQ agent", "converted from Visio")

        vision = None
        if image is not None and image.filename:
            img_path = _dg_dir(ws) / f"upload_{_safe_name(image.filename)}"
            img_path.write_bytes(await image.read())
            try:
                dg = _load_settings(ws)
                ai_client, ai_model = _workspace_ai(ws, dg)
                vision = visio_vision_check(
                    xml, img_path, dg.get("anthropic_key", ""),
                    client=ai_client, model=ai_model,
                )
            finally:
                img_path.unlink(missing_ok=True)

        return {"bpmn": out.name, "issue_count": len(issues), "issues": issues,
                "preserve_layout": preserve_layout, "vision_check": vision,
                "repo_id": item["id"]}

    @router.post("/validate")
    async def validate(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        artifact: str = Form(default=""),
    ):
        """BPMN XML -> structured validation report (Signavio best-practice rules)."""
        xml, _ = await _incoming_bpmn(ws, file, artifact)
        try:
            issues = validate_bpmn_xml(xml)
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        by_severity: dict[str, int] = {}
        for issue in issues:
            by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1
        return {"issues": issues, "by_severity": by_severity}

    @router.post("/uplift")
    async def uplift(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        artifact: str = Form(default=""),
        process_name: str = Form(default=""),
    ):
        """Rule-based uplift: naming, gateway, structure and layout fixes (no AI call)."""
        xml, base = await _incoming_bpmn(ws, file, artifact)
        try:
            before = len(validate_bpmn_xml(xml))
            uplifted, changes = uplift_bpmn_xml(xml, process_name)
            after = len(validate_bpmn_xml(uplifted))
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        out = _dg_dir(ws) / _stamped(f"{base}_uplifted", ".bpmn")
        out.write_text(uplifted, encoding="utf-8")
        item = register_bpmn(ws, out.name, f"{base.replace('_', ' ').title()} (uplifted)",
                             "DiagramIQ agent", "rule-based uplift")
        return {"bpmn": out.name, "issues_before": before, "issues_after": after,
                "changes": changes, "change_count": len(changes), "repo_id": item["id"]}

    @router.post("/bpmn-to-excel")
    async def bpmn_to_excel(
        ws: dict = Depends(workspace_dep),
        file: UploadFile = File(default=None),
        artifact: str = Form(default=""),
        process_name: str = Form(default="Uplifted Process"),
    ):
        """BPMN XML -> editable Excel review (round-trip editing)."""
        xml, base = await _incoming_bpmn(ws, file, artifact)
        out = _dg_dir(ws) / _stamped(f"{base}_review", ".xlsx")
        try:
            summary = bpmn_to_review_excel(xml, out, process_name)
        except DiagramError as exc:
            raise HTTPException(400, str(exc))
        return summary

    return router
