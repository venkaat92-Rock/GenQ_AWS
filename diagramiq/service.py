"""DiagramIQ service layer: synchronous wrappers around the vendored engine.

Pipeline (same as the desktop app):
  notes (.txt/.docx/.pdf) -> Process Discovery Excel -> BPMN 2.0 XML
  -> Signavio validation -> rule-based uplift
  and BPMN/XML upload -> editable Excel review.

Notes analysis uses Anthropic when ANTHROPIC_API_KEY is set; otherwise a deterministic
rule-based parser (one step per sentence/bullet, "Role: step" lanes) keeps the whole
pipeline usable without any AI key — clearly labeled so nobody mistakes it for AI output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from diagramiq.bpmn_advanced_rules import apply_advanced_rules
from diagramiq.bpmn_layout_fixer import fix_layout
from diagramiq.bpmn_parser import parse_bpmn_xml
from diagramiq.bpmn_to_excel import bpmn_to_process_dict
from diagramiq.bpmn_validator import validate_bpmn
from diagramiq.excel_to_bpmn import build_bpmn_from_excel
from diagramiq.local_uplift import uplift_local
from diagramiq.signavio_normalize import normalize_for_signavio
from diagramiq.signavio_rules import validate_signavio
from diagramiq.transcription_to_excel import (
    _parse_ai_json,
    build_excel_from_transcription,
    read_transcription,
    save_excel_from_ai_response,
)


class DiagramError(Exception):
    pass


def _run_callbacks(fn, *args, **kwargs) -> str:
    """Run one of the engine's callback-style functions synchronously."""
    result: dict = {}

    def on_complete(value: str) -> None:
        result["value"] = value

    def on_error(message: str) -> None:
        result["error"] = message

    fn(*args, on_chunk=None, on_complete=on_complete, on_error=on_error,
       **kwargs)
    if "error" in result:
        raise DiagramError(result["error"])
    if "value" not in result:
        raise DiagramError("engine returned no result")
    return result["value"]


# ---- notes -> Process Discovery Excel --------------------------------------------------

_STEP_SPLIT = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s+(?=[A-Z])")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_ROLE = re.compile(r"^([A-Z][\w /&]{2,30}?):\s+(.*)$")


def _rule_based_analysis(text: str, process_name: str) -> dict:
    """Deterministic fallback: bullets/sentences become steps; 'Role: step' sets the lane."""
    steps = []
    current_role = "Process team"
    for fragment in _STEP_SPLIT.split(text):
        line = _BULLET.sub("", fragment.strip())
        if len(line) < 8:
            continue
        role_match = _ROLE.match(line)
        if role_match and len(role_match.group(1).split()) <= 4:
            current_role = role_match.group(1).strip()
            line = role_match.group(2).strip()
            if len(line) < 8:
                continue
        steps.append(
            {
                "activity": line[:80].rstrip(".") ,
                "description": line,
                "participant": current_role,
                "it_systems": "",
                "input_document": "",
                "output_document": "",
                "templates": "",
                "dependency": "",
                "frequency": "",
                "pain_points": "",
            }
        )
    if not steps:
        raise DiagramError("no process steps could be extracted from the notes")
    return {
        "process_name": process_name,
        "trigger_event": steps[0]["activity"],
        "successful_outcome": steps[-1]["activity"],
        "unsuccessful_outcome": "",
        "steps": steps,
    }


def notes_to_discovery_excel(notes_path: Path, output_path: Path, process_name: str,
                             provider: str = "auto", api_key: str = "",
                             client=None, model: str | None = None) -> dict:
    """Analyse a transcript into a Process Discovery Excel. Returns a summary.

    provider: 'anthropic' | 'ollama' | 'rule-based' | 'auto' (anthropic if a key is
    available, else ollama if reachable, else rule-based). The AI providers extract
    dependencies, IT systems, and decision points that the rule-based parser cannot —
    this is what drives gateway inference and desktop-grade output quality.

    `client`/`model`: pre-built Anthropic-SDK client (e.g. AnthropicBedrock on the
    service's IAM role — the workspace AI configured in the AI Model card) and its
    model id; used for the anthropic path when no key is configured.
    """
    text = read_transcription(str(notes_path))
    if not text.strip():
        raise DiagramError("the uploaded file contains no readable text")

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if provider == "auto":
        if key or client is not None:
            provider = "anthropic"
        elif _ollama_reachable():
            provider = "ollama"
        else:
            provider = "rule-based"

    if provider == "anthropic":
        if not key and client is None:
            raise DiagramError("Anthropic selected but no API key configured — "
                               "set one in the studio settings")
        raw = _run_callbacks(
            build_excel_from_transcription, text, process_name, "anthropic", key,
            client=client, model=model,
        )
        ai_output = _parse_ai_json(raw)
        engine = "anthropic (AI)"
    elif provider == "ollama":
        raw = _run_callbacks(
            build_excel_from_transcription, text, process_name, "ollama", api_key
        )
        ai_output = _parse_ai_json(raw)
        engine = "ollama (local AI)"
    elif provider == "rule-based":
        ai_output = _rule_based_analysis(text, process_name)
        engine = ("rule-based (no AI provider configured — dependencies, systems and "
                  "decision gateways are NOT extracted; configure a provider for "
                  "desktop-grade results)")
    else:
        raise DiagramError(f"unknown provider {provider!r}")

    save_excel_from_ai_response(ai_output, str(output_path))
    return {
        "excel": output_path.name,
        "engine": engine,
        "process_name": ai_output.get("process_name", process_name),
        "steps": len(ai_output.get("steps", [])),
        "participants": sorted(
            {s.get("participant", "") for s in ai_output.get("steps", []) if s.get("participant")}
        ),
    }


def _ollama_reachable() -> bool:
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5)
        return True
    except Exception:
        return False


# ---- Excel -> BPMN ----------------------------------------------------------------------


def excel_to_bpmn_xml(excel_path: Path, process_name: str = "") -> str:
    """Same chain as the desktop Generate BPMN: build -> compact layout fix -> normalize."""
    try:
        xml = build_bpmn_from_excel(str(excel_path), process_name)
        xml = fix_layout(xml, compact=True)
    except Exception as exc:
        raise DiagramError(f"could not convert the Excel to BPMN: {exc}")
    return normalize_for_signavio(xml)


# ---- validation -------------------------------------------------------------------------


def validate_bpmn_xml(xml: str) -> list[dict]:
    """Both validators, like the desktop: generic structure + Signavio best practices."""
    try:
        model = parse_bpmn_xml(xml)
    except Exception as exc:
        raise DiagramError(f"not a readable BPMN 2.0 file: {exc}")

    report = [
        {
            "severity": getattr(i.severity, "value", str(i.severity)),
            "rule_id": str(i.rule_id),
            "category": i.category,
            "source": "structure",
            "message": i.message,
            "element": i.element_id,
            "element_name": i.element_name,
        }
        for i in validate_bpmn(model)
    ]
    report += [
        {
            "severity": getattr(i.severity, "value", str(i.severity)),
            "rule_id": i.rule_id,
            "category": i.category,
            "source": "signavio",
            "message": i.message,
            "element": i.element_id,
            "element_name": i.element_name,
        }
        for i in validate_signavio(model)
    ]
    return report


# ---- rule-based uplift ------------------------------------------------------------------


def uplift_bpmn_xml(xml: str, process_name: str = "") -> tuple[str, list[dict]]:
    """Desktop-grade uplift chain: rule-engine cleanup -> advanced structural rules
    (gateway pairing, verb-at-start naming, orthogonal edge routing) -> layout fix ->
    Signavio normalize. Returns (xml, change log)."""
    model = parse_bpmn_xml(xml)
    issues = validate_bpmn(model)

    result: dict = {}

    def on_complete(value: str) -> None:
        result["value"] = value

    def on_error(message: str) -> None:
        result["error"] = message

    uplift_local(xml, issues, process_name, None, on_complete, on_error)
    if "error" in result:
        raise DiagramError(result["error"])

    uplifted, changes = apply_advanced_rules(result["value"])
    try:
        uplifted = fix_layout(uplifted)
    except Exception:
        pass  # a layout failure must not lose the structural fixes
    return normalize_for_signavio(uplifted), changes


# ---- Visio -> BPMN (vendored from DiagramIQ-Lonza) --------------------------------------


def visio_to_bpmn_xml(visio_path: Path, process_name: str = "",
                      preserve_layout: bool = True) -> str:
    """Convert a .vsdx to Signavio-ready BPMN 2.0.

    preserve_layout=True keeps the original Visio geometry and swimlane bands (the
    Lonza fidelity mode); False re-grids through the layout fixer like other paths.
    .vsd (binary) requires the desktop app, which converts via Visio itself.
    """
    from diagramiq.visio_to_bpmn import build_bpmn_from_visio

    if visio_path.suffix.lower() == ".vsd":
        raise DiagramError(
            "binary .vsd files need Microsoft Visio to convert — save as .vsdx "
            "and upload that (or use the DiagramIQ desktop app)"
        )
    try:
        xml = build_bpmn_from_visio(str(visio_path), process_name,
                                    preserve_layout=preserve_layout)
    except Exception as exc:
        raise DiagramError(f"could not convert the Visio file: {exc}")
    if not preserve_layout:
        try:
            xml = fix_layout(xml, compact=True)
        except Exception:
            pass
    return normalize_for_signavio(xml)


def visio_vision_check(bpmn_xml: str, image_path: Path, api_key: str,
                       client=None, model: str | None = None) -> dict:
    """Optional AI cross-check: does the generated BPMN match a screenshot of the
    original diagram? Needs an Anthropic key (or a pre-built workspace client,
    e.g. Bedrock on the IAM role); returns a structured verdict."""
    from diagramiq.ai_visio_image_check import check_visio_against_image

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    return check_visio_against_image(bpmn_xml, str(image_path), "anthropic", key,
                                     client=client, model=model)


# ---- BPMN -> Excel review ---------------------------------------------------------------


def bpmn_to_review_excel(xml: str, output_path: Path, process_name: str) -> dict:
    process = bpmn_to_process_dict(xml, process_name)
    save_excel_from_ai_response(process, str(output_path))
    return {
        "excel": output_path.name,
        "process_name": process.get("process_name", process_name),
        "steps": len(process.get("steps", [])),
    }
