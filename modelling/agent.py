"""L2 — The modelling agent: schema read -> proposed spec -> (human confirms) -> re-draft.

Core loop (CLAUDE.md §1.1): agent drafts, human confirms once per process, business
self-serves. There is deliberately NO path that materializes without confirmation.

With an LLM configured, the agent asks it to refine the priors-based draft (better
rationales, customization detection). Without one — or on any LLM failure — the
deterministic priors draft ships as-is. Either way the case-granularity ambiguity is
enforced in post-processing: the agent may not silently pick a granularity.
"""

from __future__ import annotations

import json

from engine.db import EventLogStore
from modelling.llm import LLMProvider
from modelling.priors import (
    CASE_GRANULARITY_QID,
    _find_table,
    detect_process,
    draft_spec,
)
from modelling.spec import MappingSpec, OpenQuestion

_SYSTEM_PROMPT = """You are the event-log modelling agent of a process-mining product.
You are given (1) the schema and sample rows of a customer's staged source tables and
(2) a draft mapping spec produced from standard SAP P2P priors.

Improve the draft: sharpen rationales in business language, adjust confidence levels
(high/medium/low), and add open_questions for any ambiguity or customization you detect
(unusual columns, non-standard codes). NEVER remove the case-granularity open question.
NEVER change the `sql` fields. Respond with ONLY the full spec as valid JSON matching
the draft's structure exactly."""

_DOCUMENT_CLAUSE = """

You are ALSO given (3) context documents the customer uploaded — SOPs, work
instructions, screenshots. These say how the process is SUPPOSED to run; the schema
says what the system actually recorded. They are different kinds of evidence and you
must not blur them:

- Use the documents to explain codes and conditions the schema alone cannot reveal,
  and to sharpen rationales into the customer's own vocabulary.
- When a choice comes from a document, SAY SO in its rationale, naming the file:
  e.g. "per 'AP SOP v3.docx', invoices are blocked above a 2% price variance".
- Where a document DISAGREES with the data (it describes a step the tables do not
  show, or forbids something the data clearly does), do NOT pick a side. Add an
  open_question stating both, so a human decides. A document is a claim about the
  process, not proof of it.
- A document may be out of date, aspirational, or about a different entity. Never
  raise a confidence level on a document's word alone."""


class ModellingAgent:
    def __init__(self, store: EventLogStore, llm: LLMProvider | None = None,
                 documents: list | None = None):
        self._store = store
        self._llm = llm
        # Optional context documents (extraction/documents.ContextDocument). The agent
        # behaves exactly as it did before when this is empty — documents never gate a draft.
        self._documents = documents or []

    def draft(self, process: str = "auto") -> MappingSpec:
        schema = self._store.schema_summary()
        if not schema:
            raise ValueError("no source tables staged — upload source data first")

        if process == "auto":
            detected = detect_process(schema)
            if detected is None:
                raise ValueError(
                    "the staged tables match neither purchasing (EKKO family) nor sales "
                    f"(VBAK family) priors; found: {sorted(schema)}"
                )
            process = detected

        spec = draft_spec(schema, process)
        if not spec.activities:
            raise ValueError(
                f"no {process} activities recognized in the staged tables; "
                f"found: {sorted(schema)}"
            )

        readable = [d for d in self._documents if getattr(d, "status", "") == "read"]
        if self._llm is not None:
            spec = self._refine_with_llm(spec, schema, readable)
        elif readable:
            # Be explicit rather than letting an upload look like it was used.
            spec.notes = (spec.notes + f" ({len(readable)} context document(s) uploaded but "
                          "not read: no AI model is configured.)").strip()

        return self._enforce_escalation(spec, schema)

    def redraft_with_answers(self, spec: MappingSpec) -> MappingSpec:
        """Re-draft SQL after the human answers the granularity question, carrying answers over."""
        schema = self._store.schema_summary()
        granularity = None
        for q in spec.open_questions:
            if q.id == CASE_GRANULARITY_QID and q.answer:
                granularity = q.answer
        new = draft_spec(schema, spec.process, granularity=granularity)
        new.version = spec.version + 1
        new.open_questions = spec.open_questions  # keep the answered questions in the audit trail
        new.notes = spec.notes
        # human SQL overrides survive a re-draft (they outrank the agent's fresh SQL)
        edited = {a.name: a for a in spec.activities if a.sql_edited_by}
        for activity in new.activities:
            if activity.name in edited:
                prior = edited[activity.name]
                activity.sql = prior.sql
                activity.sql_explanation = prior.sql_explanation
                activity.sql_edited_by = prior.sql_edited_by
        return new

    # ---- internals -----------------------------------------------------------------

    def _refine_with_llm(self, spec: MappingSpec, schema: dict,
                         documents: list | None = None) -> MappingSpec:
        documents = documents or []
        try:
            from extraction.documents import corpus_for_prompt

            corpus = corpus_for_prompt(documents) if documents else ""
            payload = {"schema": schema, "draft_spec": spec.to_dict()}
            if corpus:
                payload["context_documents"] = corpus
            raw = self._llm.complete(
                _SYSTEM_PROMPT + (_DOCUMENT_CLAUSE if corpus else ""),
                json.dumps(payload, default=str),
            )
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            refined = MappingSpec.from_dict(json.loads(raw))
            # Guardrail: the LLM may explain, not execute — SQL stays from the priors draft
            by_name = {a.name: (a.sql, a.sql_explanation) for a in spec.activities}
            refined.activities = [a for a in refined.activities if a.name in by_name]
            for a in refined.activities:
                a.sql, a.sql_explanation = by_name[a.name]
            refined.attributes = spec.attributes
            # Audit trail: which documents actually fed this version (§1.6).
            refined.documents_considered = [d.filename for d in documents
                                            if getattr(d, "status", "") == "read"]
            cited = (f" Context documents used: "
                     f"{', '.join(refined.documents_considered)}."
                     if refined.documents_considered else "")
            refined.notes = (refined.notes + " (LLM-refined draft.)" + cited).strip()
            return refined
        except Exception as exc:  # any LLM failure -> deterministic draft still ships
            spec.notes = (spec.notes + f" (LLM refinement unavailable: {exc})").strip()
            return spec

    @staticmethod
    def _enforce_escalation(spec: MappingSpec, schema: dict) -> MappingSpec:
        """The granularity ambiguity may never be silently dropped — not even by the LLM."""
        line_table = _find_table(schema, "EKPO") or _find_table(schema, "VBAP")
        has_question = any(q.id == CASE_GRANULARITY_QID for q in spec.open_questions)
        if line_table and not has_question:
            doc = "purchase order" if spec.process == "procure_to_pay" else "sales order"
            spec.open_questions.append(
                OpenQuestion(
                    id=CASE_GRANULARITY_QID,
                    question=f"Should a case be a {doc} or a {doc} line item?",
                    why_it_matters=f"{line_table} contains line items; the case notion "
                    "changes every downstream metric.",
                    options=(
                        ["Purchase order (header, EKKO.EBELN)",
                         "Purchase order line item (EKPO.EBELN + EBELP)"]
                        if spec.process == "procure_to_pay"
                        else ["Sales order (header, VBAK.VBELN)",
                              "Sales order line item (VBAP.VBELN + POSNR)"]
                    ),
                )
            )
        return spec
