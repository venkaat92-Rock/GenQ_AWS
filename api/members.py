"""Workspace members: roles + per-offering licenses, managed by admins.

Single source of truth for BOTH offerings (ProcessIQ and DiagramIQ). DiagramIQ's
folder ACLs resolve members from here, and a member without the "diagramiq"
license has no access to that offering at all.

Rules (enforced server-side, tested):
- The first member added must be an admin (bootstrap); afterwards only admins can
  add, change, or remove members.
- Email is required, validated, and unique (case-insensitive). Names are unique
  and immutable once created — folder access grants are keyed by name.
- Roles: admin / editor / viewer. Licenses: any non-empty subset of
  processiq / diagramiq (both by default).
- The workspace can never lose its last admin (delete and demote are blocked).
- Every mutation is recorded in an audit trail (capped, newest last).

v1 posture: the acting user ("by") is a name/email, not an authenticated login —
the same honor-based identity used across the product, flagged for the pre-deploy
hardening pass.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

ROLES = ("admin", "editor", "viewer")
LICENSES = ("processiq", "diagramiq")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_AUDIT_CAP = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store_file(ws: dict):
    return ws["dir"] / "members.json"


def _legacy_file(ws: dict):
    # earlier releases kept DiagramIQ-only members here
    return ws["dir"] / "diagrams" / "members.json"


def _save_store(ws: dict, store: dict) -> None:
    tmp = _store_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    tmp.replace(_store_file(ws))


def load_store(ws: dict) -> dict:
    if _store_file(ws).exists():
        return json.loads(_store_file(ws).read_text(encoding="utf-8"))
    legacy = _legacy_file(ws)
    if legacy.exists():
        old = json.loads(legacy.read_text(encoding="utf-8"))
        store = {
            "members": [
                {"id": "m_" + uuid.uuid4().hex[:10], "name": m["name"],
                 "email": "", "role": m.get("role", "editor"),
                 "licenses": sorted(LICENSES), "added_by": "migration",
                 "added_at": _now()}
                for m in old if isinstance(m, dict) and m.get("name")
            ],
            "audit": [{"action": "migrated", "member": "*", "by": "system",
                       "at": _now(),
                       "detail": f"{len(old)} member(s) moved from DiagramIQ"}],
        }
        _save_store(ws, store)
        legacy.unlink()
        return store
    return {"members": [], "audit": []}


def members(ws: dict) -> list[dict]:
    return load_store(ws)["members"]


def find_member(member_list: list[dict], actor: str | None) -> dict | None:
    """Resolve an acting user by name or email, case-insensitively."""
    actor = (actor or "").strip().lower()
    if not actor:
        return None
    return next((m for m in member_list
                 if m["name"].lower() == actor or m.get("email", "").lower() == actor),
                None)


def _require_admin(member_list: list[dict], actor: str | None) -> dict:
    member = find_member(member_list, actor)
    if member is None or member["role"] != "admin":
        raise HTTPException(403, "only a workspace admin can manage members")
    return member


def _audit(store: dict, action: str, member: str, by: str, detail: str = "") -> None:
    store.setdefault("audit", []).append(
        {"action": action, "member": member, "by": by, "at": _now(),
         "detail": detail})
    store["audit"] = store["audit"][-_AUDIT_CAP:]


def _validate_email(email: str, member_list: list[dict],
                    exclude_id: str | None = None) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, f"{email!r} is not a valid email address")
    if any(m.get("email", "").lower() == email for m in member_list
           if m["id"] != exclude_id):
        raise HTTPException(409, f"a member with email {email!r} already exists")
    return email


def _validate_licenses(licenses: list[str]) -> list[str]:
    cleaned = sorted({l.strip().lower() for l in licenses if l.strip()})
    if not cleaned:
        raise HTTPException(400, "assign at least one license")
    bad = [l for l in cleaned if l not in LICENSES]
    if bad:
        raise HTTPException(400, f"unknown license(s) {bad}; valid: {list(LICENSES)}")
    return cleaned


def _other_admins(member_list: list[dict], member_id: str) -> int:
    return sum(1 for m in member_list
               if m["role"] == "admin" and m["id"] != member_id)


class MemberCreate(BaseModel):
    name: str
    email: str
    role: str = "editor"
    licenses: list[str] = list(LICENSES)
    by: str = ""


class MemberUpdate(BaseModel):
    role: str | None = None
    licenses: list[str] | None = None
    email: str | None = None
    by: str = ""


def make_router(workspace_dep) -> APIRouter:
    router = APIRouter(prefix="/api/members")

    @router.get("")
    def list_members(ws: dict = Depends(workspace_dep)):
        store = load_store(ws)
        return {"members": store["members"],
                "audit": store.get("audit", [])[-50:],
                "enabled": bool(store["members"]),
                "roles": list(ROLES), "licenses": list(LICENSES)}

    @router.post("")
    def add_member(body: MemberCreate, ws: dict = Depends(workspace_dep)):
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "a member name is required")
        store = load_store(ws)
        existing = store["members"]
        if any(m["name"].lower() == name.lower() for m in existing):
            raise HTTPException(409, f"a member named {name!r} already exists")
        email = _validate_email(body.email, existing)
        licenses = _validate_licenses(body.licenses)
        if existing:
            actor = _require_admin(existing, body.by)
            added_by = actor["name"]
        else:
            if body.role != "admin":
                raise HTTPException(400, "the first member must be the admin who "
                                         "will manage access and licenses")
            added_by = "bootstrap"
        member = {"id": "m_" + uuid.uuid4().hex[:10], "name": name, "email": email,
                  "role": body.role, "licenses": licenses,
                  "added_by": added_by, "added_at": _now()}
        existing.append(member)
        _audit(store, "added", name, added_by,
               f"role={body.role}, licenses={','.join(licenses)}")
        _save_store(ws, store)
        return member

    @router.put("/{member_id}")
    def update_member(member_id: str, body: MemberUpdate,
                      ws: dict = Depends(workspace_dep)):
        store = load_store(ws)
        member_list = store["members"]
        member = next((m for m in member_list if m["id"] == member_id), None)
        if member is None:
            raise HTTPException(404, f"no member {member_id!r}")
        actor = _require_admin(member_list, body.by)
        changes = []
        if body.role is not None and body.role != member["role"]:
            if body.role not in ROLES:
                raise HTTPException(400, f"role must be one of {ROLES}")
            if (member["role"] == "admin"
                    and not _other_admins(member_list, member_id)):
                raise HTTPException(400, "cannot demote the last admin")
            changes.append(f"role {member['role']} -> {body.role}")
            member["role"] = body.role
        if body.licenses is not None:
            licenses = _validate_licenses(body.licenses)
            if licenses != member.get("licenses"):
                changes.append(f"licenses -> {','.join(licenses)}")
                member["licenses"] = licenses
        if body.email is not None:
            email = _validate_email(body.email, member_list, exclude_id=member_id)
            if email != member.get("email"):
                changes.append(f"email -> {email}")
                member["email"] = email
        if changes:
            _audit(store, "updated", member["name"], actor["name"],
                   "; ".join(changes))
            _save_store(ws, store)
        return member

    @router.delete("/{member_id}")
    def remove_member(member_id: str, by: str = "",
                      ws: dict = Depends(workspace_dep)):
        store = load_store(ws)
        member_list = store["members"]
        member = next((m for m in member_list if m["id"] == member_id), None)
        if member is None:
            raise HTTPException(404, f"no member {member_id!r}")
        actor = _require_admin(member_list, by)
        if member["role"] == "admin" and not _other_admins(member_list, member_id):
            raise HTTPException(400, "cannot remove the last admin")
        member_list.remove(member)
        _audit(store, "removed", member["name"], actor["name"])
        _save_store(ws, store)
        return {"members": member_list}

    return router
