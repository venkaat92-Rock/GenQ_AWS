"""DiagramIQ Dictionary — SAP Signavio Dictionary equivalent.

A central glossary of reusable, approved terms organized in categories
(Organizational Units, Documents, Activities, Events, IT Systems, Risks,
Controls, ...). Entries carry a title, description, and custom attributes.

Where it is used: the BPMN editor suggests dictionary entries while naming
elements (tasks -> Activities, events -> Events, lanes/pools -> Organizational
Units, data objects -> Documents), shows the linked entry's description, and
lets modellers add new terms back to the dictionary — so every diagram reuses
one approved vocabulary instead of inventing synonyms.

Feature parity with the Signavio glossary page: category tree with counts,
A-Z filtering, search, New/Edit/Delete, Merge Dictionary Entries, and
Import / Export Excel (bulk upload detects the header row and maps columns:
title, description, everything else becomes a custom attribute; duplicates
either update the existing entry or are skipped, chosen per import).
"""

from __future__ import annotations

import io
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Signavio's default category set, with its colored-dot convention.
DEFAULT_CATEGORIES = [
    {"name": "Organizational Units", "color": "#d9534f"},
    {"name": "Roles", "color": "#d9534f", "parent": "Organizational Units"},
    {"name": "Departments", "color": "#d9534f", "parent": "Organizational Units"},
    {"name": "External participants", "color": "#d9534f",
     "parent": "Organizational Units"},
    {"name": "Documents", "color": "#f0ad4e"},
    {"name": "Activities", "color": "#cddc39"},
    {"name": "Events", "color": "#00bcd4"},
    {"name": "IT Systems", "color": "#5bc0de"},
    {"name": "Risks", "color": "#e91e63"},
    {"name": "Controls", "color": "#4caf50"},
    {"name": "Others", "color": "#9e9e9e"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dg_dir(ws: dict):
    path = ws["dir"] / "diagrams"
    path.mkdir(exist_ok=True)
    return path


def _dict_file(ws: dict):
    return _dg_dir(ws) / "dictionary.json"


def _seed() -> dict:
    categories = []
    by_name: dict[str, str] = {}
    for c in DEFAULT_CATEGORIES:
        cat = {"id": "dc_" + uuid.uuid4().hex[:10], "name": c["name"],
               "color": c["color"], "builtin": True,
               "parent_id": by_name.get(c.get("parent", ""))}
        by_name[c["name"]] = cat["id"]
        categories.append(cat)
    return {"categories": categories, "entries": []}


def load_dictionary(ws: dict) -> dict:
    if _dict_file(ws).exists():
        return json.loads(_dict_file(ws).read_text(encoding="utf-8"))
    store = _seed()
    _save(ws, store)
    return store


def _save(ws: dict, store: dict) -> None:
    tmp = _dict_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    tmp.replace(_dict_file(ws))


def _category(store: dict, category_id: str) -> dict:
    cat = next((c for c in store["categories"] if c["id"] == category_id), None)
    if cat is None:
        raise HTTPException(404, f"no dictionary category {category_id!r}")
    return cat


def _entry(store: dict, entry_id: str) -> dict:
    entry = next((e for e in store["entries"] if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(404, f"no dictionary entry {entry_id!r}")
    return entry


def _dup_in_category(store: dict, category_id: str, title: str,
                     exclude_id: str | None = None) -> dict | None:
    title = title.strip().lower()
    return next(
        (e for e in store["entries"]
         if e["category_id"] == category_id and e["title"].lower() == title
         and e["id"] != exclude_id),
        None)


# ---- "where used" / occurrences (Signavio's Show usages) ------------------------------
#
# A dictionary entry is "used" wherever a BPMN element in a repository model carries the
# entry's title as its name — the same title-match the editor uses to suggest terms. We
# scan every model's latest version once and build a title -> usages index.

_ELEMENT_LABEL = {
    "participant": "Pool", "lane": "Lane", "task": "Task", "userTask": "User task",
    "serviceTask": "Service task", "manualTask": "Manual task",
    "scriptTask": "Script task", "sendTask": "Send task", "receiveTask": "Receive task",
    "businessRuleTask": "Business rule task", "subProcess": "Sub-process",
    "callActivity": "Call activity", "startEvent": "Start event",
    "endEvent": "End event", "intermediateCatchEvent": "Intermediate event",
    "intermediateThrowEvent": "Intermediate event", "boundaryEvent": "Boundary event",
    "exclusiveGateway": "Gateway", "parallelGateway": "Gateway",
    "inclusiveGateway": "Gateway", "eventBasedGateway": "Gateway",
    "dataObjectReference": "Data object", "dataStoreReference": "Data store",
    "sequenceFlow": "Flow", "textAnnotation": "Annotation", "group": "Group",
}


def _iter_named_elements(xml_text: str):
    """Yield (element_type, element_id, name) for every named element in a BPMN doc."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return
    for el in root.iter():
        name = el.get("name")
        if not name or not name.strip():
            continue
        tag = el.tag.split("}")[-1]  # strip XML namespace
        yield tag, el.get("id") or "", name.strip()


def _usage_index(ws: dict) -> dict[str, list[dict]]:
    """title(lowercased) -> list of usages, scanning every repository model once."""
    from api.diagram import _repo_load  # lazy: avoid import cycle at module load

    index: dict[str, list[dict]] = {}
    for item in _repo_load(ws):
        versions = item.get("versions") or []
        if not versions:
            continue
        path = _dg_dir(ws) / versions[-1]["file"]
        if not path.exists():
            continue
        try:
            xml_text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for tag, elem_id, name in _iter_named_elements(xml_text):
            index.setdefault(name.lower(), []).append({
                "model_id": item["id"], "model_name": item["name"],
                "model_level": item.get("level"), "model_status": item.get("status"),
                "element_type": _ELEMENT_LABEL.get(tag, tag),
                "element_id": elem_id, "element_name": name,
            })
    return index


class CategoryIn(BaseModel):
    name: str
    parent_id: str | None = None
    color: str = "#9e9e9e"
    by: str = ""


class EntryIn(BaseModel):
    category_id: str
    title: str
    description: str = ""
    attributes: dict | None = None
    by: str = ""


class EntryUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    attributes: dict | None = None
    category_id: str | None = None
    by: str = ""


class MergeIn(BaseModel):
    ids: list[str]        # entries to merge; the first one survives
    by: str = ""


def make_router(workspace_dep) -> APIRouter:
    router = APIRouter(prefix="/api/diagram/dictionary")

    @router.get("")
    def get_dictionary(category: str | None = None, q: str | None = None,
                       letter: str | None = None,
                       ws: dict = Depends(workspace_dep)):
        store = load_dictionary(ws)
        entries = store["entries"]
        counts: dict[str, int] = {}
        for e in store["entries"]:
            counts[e["category_id"]] = counts.get(e["category_id"], 0) + 1
        if category:
            # a category includes its subcategories, like Signavio's tree
            wanted = {category} | {c["id"] for c in store["categories"]
                                   if c.get("parent_id") == category}
            entries = [e for e in entries if e["category_id"] in wanted]
        if q:
            needle = q.strip().lower()
            entries = [e for e in entries
                       if needle in e["title"].lower()
                       or needle in e.get("description", "").lower()]
        if letter:
            if letter == "0-9":
                entries = [e for e in entries if e["title"][:1].isdigit()]
            else:
                entries = [e for e in entries
                           if e["title"][:1].upper() == letter.upper()]
        entries = sorted(entries, key=lambda e: e["title"].lower())
        index = _usage_index(ws)
        entries_out = [
            {**e, "usage_count": len(index.get(e["title"].strip().lower(), []))}
            for e in entries
        ]
        return {
            "categories": [{**c, "count": counts.get(c["id"], 0)}
                           for c in store["categories"]],
            "entries": entries_out,
            "total": len(store["entries"]),
        }

    @router.get("/entries/{entry_id}/usages")
    def entry_usages(entry_id: str, ws: dict = Depends(workspace_dep)):
        """Signavio 'Show usages': every model element that references this term."""
        store = load_dictionary(ws)
        entry = _entry(store, entry_id)
        usages = _usage_index(ws).get(entry["title"].strip().lower(), [])
        # one row per (model, element); group count by model for the summary
        models: dict[str, dict] = {}
        for u in usages:
            m = models.setdefault(u["model_id"], {
                "model_id": u["model_id"], "model_name": u["model_name"],
                "model_level": u["model_level"], "model_status": u["model_status"],
                "elements": []})
            m["elements"].append({"element_type": u["element_type"],
                                  "element_id": u["element_id"],
                                  "element_name": u["element_name"]})
        return {"entry": {"id": entry["id"], "title": entry["title"]},
                "usages": usages, "models": list(models.values()),
                "model_count": len(models), "usage_count": len(usages)}

    @router.post("/categories")
    def add_category(body: CategoryIn, ws: dict = Depends(workspace_dep)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "a category name is required")
        store = load_dictionary(ws)
        if any(c["name"].lower() == name.lower() for c in store["categories"]):
            raise HTTPException(409, f"a category named {name!r} already exists")
        if body.parent_id:
            _category(store, body.parent_id)
        cat = {"id": "dc_" + uuid.uuid4().hex[:10], "name": name,
               "color": body.color, "builtin": False,
               "parent_id": body.parent_id}
        store["categories"].append(cat)
        _save(ws, store)
        return cat

    @router.delete("/categories/{category_id}")
    def delete_category(category_id: str, ws: dict = Depends(workspace_dep)):
        store = load_dictionary(ws)
        cat = _category(store, category_id)
        if cat.get("builtin"):
            raise HTTPException(400, "built-in categories cannot be deleted")
        if any(e["category_id"] == category_id for e in store["entries"]):
            raise HTTPException(400, "the category still contains entries")
        if any(c.get("parent_id") == category_id for c in store["categories"]):
            raise HTTPException(400, "the category still has subcategories")
        store["categories"].remove(cat)
        _save(ws, store)
        return {"deleted": category_id}

    @router.post("/entries")
    def add_entry(body: EntryIn, ws: dict = Depends(workspace_dep)):
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "an entry title is required")
        store = load_dictionary(ws)
        _category(store, body.category_id)
        if _dup_in_category(store, body.category_id, title):
            raise HTTPException(409, f"{title!r} already exists in this category")
        entry = {"id": "de_" + uuid.uuid4().hex[:10],
                 "category_id": body.category_id, "title": title,
                 "description": body.description.strip(),
                 "attributes": body.attributes or {},
                 "created_by": body.by.strip() or "unknown",
                 "created_at": _now(), "updated_at": _now()}
        store["entries"].append(entry)
        _save(ws, store)
        return entry

    @router.put("/entries/{entry_id}")
    def update_entry(entry_id: str, body: EntryUpdate,
                     ws: dict = Depends(workspace_dep)):
        store = load_dictionary(ws)
        entry = _entry(store, entry_id)
        if body.category_id is not None:
            _category(store, body.category_id)
            entry["category_id"] = body.category_id
        if body.title is not None and body.title.strip():
            if _dup_in_category(store, entry["category_id"], body.title,
                                exclude_id=entry_id):
                raise HTTPException(409, f"{body.title.strip()!r} already exists "
                                         "in this category")
            entry["title"] = body.title.strip()
        if body.description is not None:
            entry["description"] = body.description.strip()
        if body.attributes is not None:
            entry["attributes"] = body.attributes
        entry["updated_at"] = _now()
        _save(ws, store)
        return entry

    @router.delete("/entries/{entry_id}")
    def delete_entry(entry_id: str, ws: dict = Depends(workspace_dep)):
        store = load_dictionary(ws)
        store["entries"].remove(_entry(store, entry_id))
        _save(ws, store)
        return {"deleted": entry_id}

    @router.post("/merge")
    def merge_entries(body: MergeIn, ws: dict = Depends(workspace_dep)):
        """Signavio 'Merge Dictionary Entries': the first id survives and absorbs
        the descriptions/attributes of the rest, which are deleted."""
        if len(body.ids) < 2:
            raise HTTPException(400, "select at least two entries to merge")
        store = load_dictionary(ws)
        target = _entry(store, body.ids[0])
        for other_id in body.ids[1:]:
            other = _entry(store, other_id)
            if other.get("description") and other["description"] not in \
                    target.get("description", ""):
                target["description"] = (target.get("description", "")
                                         + ("\n" if target.get("description")
                                            else "")
                                         + other["description"]).strip()
            target["attributes"] = {**other.get("attributes", {}),
                                    **target.get("attributes", {})}
            store["entries"].remove(other)
        target["updated_at"] = _now()
        _save(ws, store)
        return target

    # ---- bulk upload / download (Signavio Import / Export Excel) -----------------

    @router.post("/import")
    async def import_entries(file: UploadFile = File(...),
                             category_id: str = Form(...),
                             mode: str = Form("create_only"),
                             by: str = Form(""),
                             ws: dict = Depends(workspace_dep)):
        """Bulk upload. Header row is detected; the 'title'/'name' column is the
        entry title, 'description' its description, every other column becomes a
        custom attribute. mode: create_only (skip duplicates) | update_existing."""
        if mode not in ("create_only", "update_existing"):
            raise HTTPException(400, "mode must be create_only or update_existing")
        store = load_dictionary(ws)
        _category(store, category_id)

        name = (file.filename or "").lower()
        raw = await file.read()
        rows: list[list[str]]
        if name.endswith((".xlsx", ".xls")):
            from openpyxl import load_workbook

            try:
                sheet = load_workbook(io.BytesIO(raw), read_only=True).active
            except Exception:
                raise HTTPException(400, "could not read the Excel workbook")
            rows = [["" if v is None else str(v).strip() for v in r]
                    for r in sheet.iter_rows(values_only=True)]
        elif name.endswith(".csv"):
            import csv

            text = raw.decode("utf-8-sig", errors="replace")
            rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text))]
        else:
            raise HTTPException(400, "upload an .xlsx or .csv file")
        rows = [r for r in rows if any(r)]
        if not rows:
            raise HTTPException(400, "the file is empty")

        header = [h.lower() for h in rows[0]]
        if not any(h in ("title", "name", "entry") for h in header):
            raise HTTPException(400, "no header row found — the first row must "
                                     "contain a 'title' (or 'name') column")
        title_col = next(i for i, h in enumerate(header)
                         if h in ("title", "name", "entry"))
        desc_col = next((i for i, h in enumerate(header)
                         if h == "description"), None)
        attr_cols = [(i, rows[0][i]) for i, h in enumerate(header)
                     if i not in (title_col, desc_col) and h]

        created = updated = skipped = 0
        errors: list[str] = []
        for line_no, row in enumerate(rows[1:], start=2):
            title = row[title_col].strip() if title_col < len(row) else ""
            if not title:
                errors.append(f"row {line_no}: empty title — skipped")
                continue
            description = (row[desc_col].strip()
                           if desc_col is not None and desc_col < len(row) else "")
            attributes = {label: row[i].strip()
                          for i, label in attr_cols if i < len(row) and row[i].strip()}
            existing = _dup_in_category(store, category_id, title)
            if existing is not None:
                if mode == "update_existing":
                    if description:
                        existing["description"] = description
                    existing["attributes"] = {**existing.get("attributes", {}),
                                              **attributes}
                    existing["updated_at"] = _now()
                    updated += 1
                else:
                    skipped += 1
                continue
            store["entries"].append({
                "id": "de_" + uuid.uuid4().hex[:10], "category_id": category_id,
                "title": title, "description": description,
                "attributes": attributes,
                "created_by": by.strip() or "bulk import",
                "created_at": _now(), "updated_at": _now()})
            created += 1
        _save(ws, store)
        return {"created": created, "updated": updated, "skipped": skipped,
                "errors": errors}

    @router.get("/export")
    def export_entries(category: str | None = None,
                       ws: dict = Depends(workspace_dep)):
        from openpyxl import Workbook

        store = load_dictionary(ws)
        entries = store["entries"]
        cat_name = {c["id"]: c["name"] for c in store["categories"]}
        if category:
            entries = [e for e in entries if e["category_id"] == category]
        attr_keys = sorted({k for e in entries
                            for k in e.get("attributes", {})})
        wb = Workbook()
        sheet = wb.active
        sheet.title = "Dictionary"
        sheet.append(["title", "description", "category", *attr_keys])
        for e in sorted(entries, key=lambda e: (cat_name.get(e["category_id"], ""),
                                                e["title"].lower())):
            sheet.append([e["title"], e.get("description", ""),
                          cat_name.get(e["category_id"], ""),
                          *[e.get("attributes", {}).get(k, "") for k in attr_keys]])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument"
                       ".spreadsheetml.sheet",
            headers={"Content-Disposition":
                     'attachment; filename="dictionary.xlsx"'})

    return router
