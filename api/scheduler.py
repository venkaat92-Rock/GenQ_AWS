"""Real load schedules (Celonis Schedules equivalent).

A schedule re-materializes a workspace's event log from its staged sources on a cadence
(hourly / daily). It is OFF by default, edited only with an SME name, and it only ever
runs a mapping a human has confirmed — scheduling never bypasses the §1.1 gate.

The scheduler is a single daemon thread that scans workspaces once a minute. v1 scope:
in-process, single instance — replaced by a proper job runner when live connectors
arrive in Phase 4.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from api import storage
from api.dataload import record_run
from engine.db import EventLogStore
from modelling.materialize import materialize

FREQUENCIES: dict[str, timedelta] = {"hourly": timedelta(hours=1), "daily": timedelta(days=1)}

_DEFAULT = {"enabled": False, "frequency": "daily", "updated_by": None, "next_run": None}


def _schedule_file(ws: dict):
    return ws["dir"] / "schedule.json"


def load_schedule(ws: dict) -> dict:
    path = _schedule_file(ws)
    if path.exists():
        return {**_DEFAULT, **json.loads(path.read_text(encoding="utf-8"))}
    return dict(_DEFAULT)


def save_schedule(ws: dict, enabled: bool, frequency: str, updated_by: str,
                  base: datetime | None = None) -> dict:
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {sorted(FREQUENCIES)}")
    base = base or datetime.now(timezone.utc)
    record = {
        "enabled": enabled,
        "frequency": frequency,
        "updated_by": updated_by,
        "next_run": (
            (base + FREQUENCIES[frequency]).isoformat(timespec="seconds")
            if enabled else None
        ),
    }
    tmp = _schedule_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(_schedule_file(ws))
    return record


def _refresh_connections(ws: dict) -> tuple[list[str], list[str]]:
    """Re-extract every auto-refresh saved connection before a scheduled load, so the
    event log is rebuilt from CURRENT source data (the full Celonis loop)."""
    from connectors.live_api import run_live_extraction

    refreshed: list[str] = []
    errors: list[str] = []
    for connection in storage.load_connections(ws):
        if not connection.get("auto_refresh"):
            continue
        try:
            with EventLogStore(ws["db_path"]) as store:
                run_live_extraction(store, connection["kind"], connection["config"])
            refreshed.append(connection["name"])
        except Exception as exc:
            errors.append(f"{connection['name']}: {exc}")
    return refreshed, errors


def run_due_schedules(now: datetime | None = None) -> list[dict]:
    """One scan over all workspaces; returns what was executed (used by tests too)."""
    now = now or datetime.now(timezone.utc)
    executed = []
    for ws in storage.all_workspaces():
        try:
            sched = load_schedule(ws)
            if not sched["enabled"] or not sched["next_run"]:
                continue
            if datetime.fromisoformat(sched["next_run"]) > now:
                continue
            spec = storage.load_spec(ws)
            trigger = f"schedule ({sched['frequency']})"
            if spec is None or spec.status != "confirmed":
                record_run(ws, {"status": "skipped",
                                "reason": "no confirmed data model"}, trigger)
            else:
                try:
                    refreshed, refresh_errors = _refresh_connections(ws)
                    with EventLogStore(ws["db_path"]) as store:
                        stats = materialize(store, spec)
                    entry = {**stats, "status": "ok"}
                    if refreshed:
                        entry["refreshed_connections"] = refreshed
                    if refresh_errors:
                        entry["refresh_errors"] = refresh_errors
                    record_run(ws, entry, trigger)
                    executed.append({"workspace": ws["id"], **stats})
                except Exception as exc:  # keep the scheduler alive; surface in the log
                    record_run(ws, {"status": "failed", "error": str(exc)}, trigger)
            # next_run advances from the scan time, so a catch-up run can't leave it stale
            save_schedule(ws, True, sched["frequency"], sched["updated_by"] or "scheduler",
                          base=now)
        except Exception:
            continue  # one broken workspace must not stop the scan
    return executed


def start_scheduler() -> threading.Thread:
    def loop() -> None:
        while True:
            time.sleep(60)
            try:
                run_due_schedules()
            except Exception:
                pass

    thread = threading.Thread(target=loop, name="processiq-scheduler", daemon=True)
    thread.start()
    return thread
