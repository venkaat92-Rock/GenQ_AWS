"""L2 — AI Interviewer: the confirmation loop as a conversation instead of a form.

§4 already demands that the agent surface every semantic choice in business language
and block materialisation on `open_questions`. Until now that was a form: here is the
question, pick an option. A form is a poor instrument for the thing we actually need,
which is an SME's *reasoning* — "it depends, for services POs we…" has nowhere to go
in a radio button, so it gets flattened into a wrong answer and nobody notices.

This module makes that step a short interview. The agent asks one question at a time
in business language, accepts free text, asks a follow-up when the reply is ambiguous,
and only then proposes a concrete answer.

The rule that keeps this safe, and the reason this is not an autonomous path:

    THE INTERVIEWER NEVER WRITES THE ANSWER ITSELF.

It returns a *proposal*. The human still sees "I'll record: <answer>" and confirms,
and only that confirmation calls the existing /api/mapping/answers gate. That keeps
`agent-drafts -> human-confirms-once -> business-self-serves` intact (§1.1) — the
conversation improves the quality of the human's answer, it does not replace it.

Degrades to the plain form when no LLM is configured: `available()` is False and the
UI keeps rendering the option list it renders today.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from modelling.llm import LLMProvider
from modelling.spec import MappingSpec, OpenQuestion

SYSTEM = """You are a process-mining analyst interviewing a business SME to settle ONE
ambiguous modelling decision before an event log is built.

Rules:
- Speak business language. Never mention SQL, table names, joins, or column names
  unless the SME uses them first.
- Ask about how their business actually works, not about data structures.
- One question at a time. Keep every reply under 70 words.
- If the SME's answer clearly settles the question, propose the matching answer.
- If it is ambiguous, partial, or conditional ("it depends"), ask ONE focused
  follow-up instead of guessing.
- Never invent an option that was not offered unless the SME explicitly describes
  something outside the list.

Reply with ONLY a JSON object:
{"reply": "<what you say to the SME>",
 "proposed_answer": "<one of the allowed options, or null if not settled yet>",
 "rationale": "<one sentence: why this answer follows from what they said, or null>"}"""


@dataclass
class InterviewTurn:
    reply: str
    proposed_answer: str | None = None
    rationale: str | None = None
    options: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "reply": self.reply,
            "proposed_answer": self.proposed_answer,
            "rationale": self.rationale,
            "options": self.options or [],
        }


def _question(spec: MappingSpec, question_id: str) -> OpenQuestion:
    for q in spec.open_questions:
        if q.id == question_id:
            return q
    raise KeyError(f"unknown question id: {question_id}")


def opening(spec: MappingSpec, question_id: str) -> InterviewTurn:
    """The first thing the interviewer says. Deterministic: no LLM call needed to
    read out a question we already wrote, and it means the interview opens instantly."""
    q = _question(spec, question_id)
    reply = f"{q.question}\n\nWhy this matters: {q.why_it_matters}"
    if q.options:
        reply += "\n\nTell me in your own words — I'll map it to the right option."
    return InterviewTurn(reply=reply, options=list(q.options))


def available(llm: LLMProvider | None) -> bool:
    return llm is not None


def interview_turn(
    spec: MappingSpec,
    question_id: str,
    history: list[dict],
    message: str,
    llm: LLMProvider | None,
) -> InterviewTurn:
    """One exchange. `history` is [{role: 'agent'|'sme', text: str}, ...]."""
    q = _question(spec, question_id)

    if llm is None:
        # No model configured — stay useful rather than failing: echo the answer back
        # for confirmation if it matches an option, otherwise point at the list.
        match = next((o for o in q.options if o.strip().lower() == message.strip().lower()), None)
        if match:
            return InterviewTurn(
                reply=f"Recorded as “{match}”.", proposed_answer=match,
                rationale="exact match against the offered options (no AI model configured)",
                options=list(q.options))
        return InterviewTurn(
            reply="No AI model is configured, so I can't interpret free text here. "
                  "Please pick one of the options exactly as written.",
            options=list(q.options))

    transcript = "\n".join(
        f"{'Analyst' if h.get('role') == 'agent' else 'SME'}: {h.get('text', '')}"
        for h in history
    )
    user = (
        f"Process: {spec.process}\n"
        f"Case notion currently proposed: {spec.case_id.source} — {spec.case_id.description}\n"
        f"Activities drafted so far: {', '.join(a.name for a in spec.activities) or '(none)'}\n\n"
        f"QUESTION TO SETTLE: {q.question}\n"
        f"Why it matters: {q.why_it_matters}\n"
        f"Allowed options: {json.dumps(q.options) if q.options else '(free text)'}\n\n"
        f"Conversation so far:\n{transcript or '(none)'}\n\n"
        f"SME's latest message: {message}"
    )

    raw = llm.complete(SYSTEM, user)
    data = _parse(raw)
    proposed = data.get("proposed_answer")

    # Guard the model's output: an answer outside the offered options is not accepted
    # silently. This is the §1.1 "never guess" rule applied to the interviewer itself.
    if proposed and q.options and proposed not in q.options:
        near = next((o for o in q.options if o.strip().lower() == str(proposed).strip().lower()), None)
        proposed = near  # None when it genuinely invented one

    return InterviewTurn(
        reply=str(data.get("reply") or "Could you say a little more about that?"),
        proposed_answer=proposed,
        rationale=data.get("rationale"),
        options=list(q.options),
    )


def _parse(raw: str) -> dict:
    """Tolerate a model that wraps JSON in prose or a fenced block."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                value = json.loads(text[start:end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {"reply": text[:400] or "Could you say a little more about that?"}
