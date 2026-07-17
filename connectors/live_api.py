"""Live API extractors: pull records straight from the source system's REST/OData API.

  servicenow — Table API   (instance URL + user/password)
  jira       — REST v2     (base URL + email/API-token, optional JQL; issues + changelog)
  salesforce — REST/SOQL   (instance URL + access token; one query per object)
  odata      — OData v2/v4 (service URL + basic auth or bearer; SAP Gateway, Dynamics, ...)

All extraction is read-only GETs with pagination caps. Credentials are used for the
request (or stored per-workspace ONLY when the user saves a connection for scheduled
refresh — plain JSON in v1, same posture as workspace keys, flagged in the UI).
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request

from connectors.extract import ConnectorError
from engine.db import EventLogStore

MAX_ROWS = 50_000
PAGE = 500


def _get_json(url: str, user: str = "", password: str = "", token: str = "") -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    elif user:
        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise ConnectorError(f"{exc.code} from {urllib.parse.urlparse(url).netloc}: {detail}")
    except Exception as exc:
        raise ConnectorError(f"could not reach {urllib.parse.urlparse(url).netloc}: {exc}")


# ---- ServiceNow -------------------------------------------------------------------------


def extract_servicenow(store: EventLogStore, instance_url: str, user: str,
                       password: str, tables: list[str]) -> dict:
    if not tables:
        raise ConnectorError("list at least one ServiceNow table (e.g. incident, sys_audit)")
    base = instance_url.rstrip("/")
    staged = {}
    for table in tables:
        rows: list[dict] = []
        offset = 0
        while len(rows) < MAX_ROWS:
            url = (f"{base}/api/now/table/{urllib.parse.quote(table)}"
                   f"?sysparm_limit={PAGE}&sysparm_offset={offset}")
            payload = _get_json(url, user, password)
            page = payload.get("result", [])
            rows.extend(page)
            if len(page) < PAGE:
                break
            offset += PAGE
        staged[table] = store.stage_rows(table, rows)
    return {"staged": staged, "kind": "servicenow"}


# ---- Jira -------------------------------------------------------------------------------


def extract_jira(store: EventLogStore, base_url: str, email: str, token: str,
                 jql: str = "", max_issues: int = 5000) -> dict:
    base = base_url.rstrip("/")
    issues_rows: list[dict] = []
    changelog_rows: list[dict] = []
    start = 0
    while start < max_issues:
        url = (f"{base}/rest/api/2/search?startAt={start}&maxResults=100"
               f"&expand=changelog&jql={urllib.parse.quote(jql)}")
        payload = _get_json(url, email, token)
        issues = payload.get("issues", [])
        for issue in issues:
            fields = issue.get("fields", {})
            issues_rows.append({
                "key": issue.get("key"),
                "project": (fields.get("project") or {}).get("key"),
                "issuetype": (fields.get("issuetype") or {}).get("name"),
                "status": (fields.get("status") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name"),
                "created": fields.get("created"),
                "resolved": fields.get("resolutiondate"),
                "assignee": (fields.get("assignee") or {}).get("displayName"),
                "reporter": (fields.get("reporter") or {}).get("displayName"),
            })
            for history in (issue.get("changelog") or {}).get("histories", []):
                for item in history.get("items", []):
                    changelog_rows.append({
                        "issue_key": issue.get("key"),
                        "field": item.get("field"),
                        "fromString": item.get("fromString"),
                        "toString": item.get("toString"),
                        "created": history.get("created"),
                        "author": (history.get("author") or {}).get("displayName"),
                    })
        start += 100
        if start >= payload.get("total", 0) or not issues:
            break
    staged = {"issues": store.stage_rows("issues", issues_rows)}
    if changelog_rows:
        staged["changelog"] = store.stage_rows("changelog", changelog_rows)
    return {"staged": staged, "kind": "jira"}


# ---- Salesforce -------------------------------------------------------------------------


def extract_salesforce(store: EventLogStore, instance_url: str, access_token: str,
                       objects: list[str], api_version: str = "v59.0") -> dict:
    if not objects:
        raise ConnectorError("list at least one Salesforce object (e.g. Opportunity)")
    base = instance_url.rstrip("/")
    staged = {}
    for obj in objects:
        soql = f"SELECT FIELDS(ALL) FROM {obj} LIMIT 200"
        url = f"{base}/services/data/{api_version}/query?q={urllib.parse.quote(soql)}"
        rows: list[dict] = []
        while url and len(rows) < MAX_ROWS:
            payload = _get_json(url, token=access_token)
            for record in payload.get("records", []):
                record.pop("attributes", None)
                rows.append(record)
            next_url = payload.get("nextRecordsUrl")
            url = f"{base}{next_url}" if next_url and not payload.get("done") else None
        staged[obj] = store.stage_rows(obj, rows)
    return {"staged": staged, "kind": "salesforce"}


# ---- Generic OData (SAP Gateway, Dynamics 365, ...) --------------------------------------


def extract_odata(store: EventLogStore, service_url: str, entities: list[str],
                  user: str = "", password: str = "", token: str = "") -> dict:
    if not entities:
        raise ConnectorError("list at least one entity set (e.g. PurchaseOrderSet)")
    base = service_url.rstrip("/")
    staged = {}
    for entity in entities:
        sep = "&" if "?" in entity else "?"
        url = f"{base}/{entity}{sep}$format=json"
        rows: list[dict] = []
        while url and len(rows) < MAX_ROWS:
            payload = _get_json(url, user, password, token)
            if "d" in payload:  # OData v2 (SAP Gateway)
                data = payload["d"]
                page = data.get("results", data if isinstance(data, list) else [])
                next_url = data.get("__next")
            else:               # OData v4
                page = payload.get("value", [])
                next_url = payload.get("@odata.nextLink")
            for record in page:
                if isinstance(record, dict):
                    record.pop("__metadata", None)
                    rows.append(record)
            url = next_url
        table = entity.split("?")[0].split("/")[-1]
        staged[table] = store.stage_rows(table, rows)
    return {"staged": staged, "kind": "odata"}


# ---- dispatch (shared by one-off extraction and saved connections) ------------------------


def run_live_extraction(store: EventLogStore, kind: str, config: dict) -> dict:
    from connectors.extract import extract_database, extract_url

    if kind in ("sqlite", "postgres", "mysql"):
        return extract_database(store, kind, config.get("connection", ""),
                                config.get("tables", []), config.get("schema_name"))
    if kind == "url":
        return extract_url(store, config.get("connection", ""), config.get("table", ""))
    if kind == "servicenow":
        return extract_servicenow(store, config.get("instance_url", ""),
                                  config.get("user", ""), config.get("password", ""),
                                  config.get("tables", []))
    if kind == "jira":
        return extract_jira(store, config.get("base_url", ""), config.get("email", ""),
                            config.get("token", ""), config.get("jql", ""))
    if kind == "salesforce":
        return extract_salesforce(store, config.get("instance_url", ""),
                                  config.get("access_token", ""),
                                  config.get("objects", []))
    if kind == "odata":
        return extract_odata(store, config.get("service_url", ""),
                             config.get("entities", []), config.get("user", ""),
                             config.get("password", ""), config.get("token", ""))
    raise ConnectorError(f"unsupported connection kind {kind!r}")
