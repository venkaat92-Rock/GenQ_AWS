"""L2 — The event-log mapping spec: versioned, human-readable, auditable (CLAUDE.md §4).

The spec is the contract between the modelling agent and the human reviewer. It is stored
as YAML, diff-able, and carries per-choice rationale + confidence. Materialization is
blocked until every open question is answered and a named reviewer confirms.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import yaml

CONFIDENCE_LEVELS = ("high", "medium", "low")


class MappingNotConfirmed(Exception):
    """Raised when materialization is attempted on an unconfirmed or blocked spec."""


@dataclass
class ActivityMapping:
    name: str
    source_table: str
    condition: str          # business-readable condition, e.g. "change docs with TCODE = ME29N"
    timestamp: str          # business-readable timestamp choice, e.g. "CDHDR.UDATE"
    rationale: str          # WHY the agent chose this — plain language, mandatory
    confidence: str = "high"
    sql: str = ""           # the executable SELECT (case_id, timestamp) — inspectable, never hidden
    sql_explanation: str = ""       # auto-generated plain-English reading of the SQL
    sql_edited_by: str | None = None  # set when a human overrides the agent's SQL

    def __post_init__(self):
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")


@dataclass
class AttributeMapping:
    name: str
    expression: str         # business-readable description of the derivation
    rationale: str
    sql: str = ""           # SELECT (case_id, <name>)


@dataclass
class OpenQuestion:
    id: str
    question: str
    why_it_matters: str
    options: list[str] = field(default_factory=list)
    answer: str | None = None


@dataclass
class CaseIdMapping:
    source: str
    description: str


@dataclass
class MappingSpec:
    process: str
    case_id: CaseIdMapping
    activities: list[ActivityMapping] = field(default_factory=list)
    attributes: list[AttributeMapping] = field(default_factory=list)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    version: int = 1
    status: str = "draft"               # draft | confirmed
    reviewer: str | None = None
    created_at: str = ""
    confirmed_at: str | None = None
    generated_sql: str = ""             # full materialization SQL, stored for audit (§1.6)
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---- confirmation workflow ------------------------------------------------------

    def unanswered_questions(self) -> list[OpenQuestion]:
        return [q for q in self.open_questions if not q.answer]

    def answer_question(self, question_id: str, answer: str) -> None:
        for q in self.open_questions:
            if q.id == question_id:
                q.answer = answer
                return
        raise KeyError(f"no open question with id {question_id!r}")

    def confirm(self, reviewer: str) -> None:
        """One-time human confirmation. Blocked while any question is unanswered."""
        if not reviewer or not reviewer.strip():
            raise MappingNotConfirmed("a named reviewer is required to confirm a mapping")
        pending = self.unanswered_questions()
        if pending:
            raise MappingNotConfirmed(
                "cannot confirm: unanswered open questions: "
                + "; ".join(q.question for q in pending)
            )
        self.status = "confirmed"
        self.reviewer = reviewer.strip()
        self.confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def reopen(self) -> None:
        """Unlock a confirmed mapping into a new, editable version — keeping its
        activities, SQL edits, attributes and answers as the starting point. The
        previously confirmed version stays live (dashboards keep running on the
        materialized log) until this new version is re-confirmed."""
        if self.status != "confirmed":
            raise MappingNotConfirmed(
                "only a confirmed mapping can be reopened for a new version")
        self.version += 1
        self.status = "draft"
        self.reviewer = None
        self.confirmed_at = None

    # ---- (de)serialization ----------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, width=100)

    @classmethod
    def from_dict(cls, data: dict) -> "MappingSpec":
        return cls(
            process=data["process"],
            case_id=CaseIdMapping(**data["case_id"]),
            activities=[ActivityMapping(**a) for a in data.get("activities", [])],
            attributes=[AttributeMapping(**a) for a in data.get("attributes", [])],
            open_questions=[OpenQuestion(**q) for q in data.get("open_questions", [])],
            version=data.get("version", 1),
            status=data.get("status", "draft"),
            reviewer=data.get("reviewer"),
            created_at=data.get("created_at", ""),
            confirmed_at=data.get("confirmed_at"),
            generated_sql=data.get("generated_sql", ""),
            notes=data.get("notes", ""),
        )

    @classmethod
    def from_yaml(cls, text: str) -> "MappingSpec":
        return cls.from_dict(yaml.safe_load(text))
