"""Global analysis filters (Celonis-style) applied to the event log at case level.

A filter never mutates data — it selects a set of cases, and every analysis runs on
exactly that subset. `apply_filters` also returns a human-readable description of what
was applied, so answers stay traceable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from fastapi import Query

from mining.cases import case_table

# the "who" dimension differs per process: vendor (P2P) or customer (O2C)
PARTY_COLUMNS = ("vendor", "customer")


def party_column(df: pd.DataFrame) -> str | None:
    return next((c for c in PARTY_COLUMNS if c in df.columns), None)


@dataclass
class Filters:
    vendors: str | None = None            # comma-separated vendor names
    start_from: str | None = None         # ISO date: case started on/after
    start_to: str | None = None           # ISO date: case started on/before
    min_days: float | None = None         # case duration >=
    max_days: float | None = None         # case duration <=
    include_activity: str | None = None   # case must contain this activity
    exclude_activity: str | None = None   # case must NOT contain this activity
    only_rework: bool = False             # only cases with repeated activities

    def any(self) -> bool:
        return any(
            [self.vendors, self.start_from, self.start_to, self.min_days is not None,
             self.max_days is not None, self.include_activity, self.exclude_activity,
             self.only_rework]
        )


def filter_params(
    vendors: str | None = Query(default=None),
    start_from: str | None = Query(default=None),
    start_to: str | None = Query(default=None),
    min_days: float | None = Query(default=None),
    max_days: float | None = Query(default=None),
    include_activity: str | None = Query(default=None),
    exclude_activity: str | None = Query(default=None),
    only_rework: bool = Query(default=False),
) -> Filters:
    return Filters(
        vendors, start_from, start_to, min_days, max_days,
        include_activity, exclude_activity, only_rework,
    )


def apply_filters(df: pd.DataFrame, f: Filters) -> tuple[pd.DataFrame, list[str]]:
    """Return (filtered event log, human-readable list of applied filters)."""
    if not f.any():
        return df, []

    cases = case_table(df).set_index("case_id")
    mask = pd.Series(True, index=cases.index)
    applied: list[str] = []

    party = party_column(cases)
    if f.vendors and party:
        wanted = [v.strip() for v in f.vendors.split(",") if v.strip()]
        mask &= cases[party].isin(wanted)
        applied.append(f"{party} in [{', '.join(wanted)}]")
    if f.start_from:
        try:
            mask &= cases["start"] >= pd.Timestamp(f.start_from)
        except ValueError:
            raise ValueError(f"invalid start_from date: {f.start_from!r} (use YYYY-MM-DD)")
        applied.append(f"case started on/after {f.start_from}")
    if f.start_to:
        try:
            mask &= cases["start"] <= pd.Timestamp(f.start_to) + pd.Timedelta(days=1)
        except ValueError:
            raise ValueError(f"invalid start_to date: {f.start_to!r} (use YYYY-MM-DD)")
        applied.append(f"case started on/before {f.start_to}")
    if f.min_days is not None:
        mask &= cases["duration_days"] >= f.min_days
        applied.append(f"duration >= {f.min_days:g} days")
    if f.max_days is not None:
        mask &= cases["duration_days"] <= f.max_days
        applied.append(f"duration <= {f.max_days:g} days")
    if f.include_activity:
        has = df[df["activity"] == f.include_activity]["case_id"].unique()
        mask &= cases.index.isin(has)
        applied.append(f"case includes '{f.include_activity}'")
    if f.exclude_activity:
        has = df[df["activity"] == f.exclude_activity]["case_id"].unique()
        mask &= ~cases.index.isin(has)
        applied.append(f"case excludes '{f.exclude_activity}'")
    if f.only_rework:
        mask &= cases["rework"]
        applied.append("only cases with rework")

    keep = cases.index[mask]
    return df[df["case_id"].isin(keep)], applied
