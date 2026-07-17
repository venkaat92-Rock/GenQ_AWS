"""AI vision cross-check (Lonza version).

When the user attaches the original Visio image alongside a .vsdx, this module
asks a vision-capable AI provider (Anthropic Claude or Google Gemini) to compare
the IMAGE to the BPMN elements that were extracted from the .vsdx, and to flag
anything the parse may have missed - missing tasks, decision gateways, start/end
events, swimlanes, connections, or mislabelled shapes.

The .vsdx remains the authoritative source; this is a human-in-the-loop QA aid
(the modeller reviews the flagged items). No image -> skipped. No AI key, or a
local/ollama provider -> skipped with a clear message.

Public API
----------
    check_visio_against_image(bpmn_xml, image_path, provider, api_key) -> dict
        returns {"match","summary","issues":[{type,detail}]}
                | {"skipped": "..."} | {"error": "..."}
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import xml.etree.ElementTree as ET

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"

_SYSTEM = (
    "You are a meticulous BPMN process-modelling QA reviewer.\n"
    "You receive (1) an IMAGE of a process diagram originally drawn in Microsoft "
    "Visio, and (2) the list of BPMN elements that were automatically extracted "
    "from the matching .vsdx file.\n"
    "Compare them. Identify anything CLEARLY VISIBLE in the image that is MISSING, "
    "MIS-LABELLED, or MIS-CONNECTED in the extracted list: missing tasks/"
    "activities, missing decision gateways, missing start/end events, missing "
    "swimlanes/roles, missing or wrong connections, and shapes whose text differs.\n"
    "Be precise and conservative - only report genuine gaps a modeller should "
    "double-check; do not invent issues. If the extraction looks complete, say so.\n"
    'Return ONLY a JSON object, no prose, no markdown fences:\n'
    '{"match":"good|partial|poor","summary":"<one sentence>","issues":'
    '[{"type":"missing_task|missing_gateway|missing_event|missing_lane|missing_flow|label_mismatch|other",'
    '"detail":"<specific thing to check>"}]}'
)

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
          ".tiff": "image/tiff"}


def _media_type(path: str) -> str:
    return _MEDIA.get(os.path.splitext(path)[1].lower(), "image/png")


def _image_payload(path: str, max_dim: int = 1400):
    """Return (bytes, media_type), downscaled to keep the request light."""
    raw = open(path, "rb").read()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if max(im.size) > max_dim:
            r = max_dim / float(max(im.size))
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        return raw, _media_type(path)


def _summarise_bpmn(xml: str) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return "(BPMN could not be parsed)"
    loc = lambda t: t.rsplit("}", 1)[-1]
    task_tags = {"task", "userTask", "serviceTask", "manualTask", "sendTask",
                 "receiveTask", "businessRuleTask", "scriptTask", "subProcess",
                 "callActivity"}
    tasks, gws, events, lanes, flows = [], [], [], [], 0
    for el in root.iter():
        lt = loc(el.tag)
        nm = (el.get("name") or "").strip().replace("\n", " ")
        if lt in task_tags:
            tasks.append(nm or "(unnamed)")
        elif lt.endswith("Gateway"):
            gws.append(nm or "(unnamed)")
        elif lt.endswith("Event"):
            events.append(f"{lt}:{nm}" if nm else lt)
        elif lt == "lane":
            lanes.append(nm or "(unnamed)")
        elif lt == "sequenceFlow":
            flows += 1
    return (
        "Lanes / roles (" + str(len(lanes)) + "): " + (", ".join(lanes) or "none") + "\n"
        "Tasks / activities (" + str(len(tasks)) + "): " + (", ".join(tasks) or "none") + "\n"
        "Gateways / decisions (" + str(len(gws)) + "): " + (", ".join(gws) or "none") + "\n"
        "Events (" + str(len(events)) + "): " + (", ".join(events) or "none") + "\n"
        "Connections: " + str(flows) + " sequence flows"
    )


def check_visio_against_image(bpmn_xml: str, image_path: str,
                              provider: str, api_key: str,
                              client=None, model: str | None = None) -> dict:
    if not image_path or not os.path.exists(image_path):
        return {"skipped": "no image attached."}
    if provider == "local" or (not api_key and client is None):
        return {"skipped": "AI vision check needs an Anthropic or Google Gemini key (Settings)."}
    elements = _summarise_bpmn(bpmn_xml)
    prompt = ("BPMN elements extracted from the .vsdx:\n\n" + elements +
              "\n\nNow compare the image to this list and return the JSON object.")
    try:
        if provider == "anthropic":
            text = _call_anthropic_vision(prompt, image_path, api_key,
                                          client=client, model=model)
        elif provider == "gemini":
            text = _call_gemini_vision(prompt, image_path, api_key)
        else:
            return {"skipped": f"AI vision check is not available for provider '{provider}'."}
    except Exception as exc:
        return {"error": str(exc)}
    return _parse(text)


def _parse(text: str) -> dict:
    if not text:
        return {"error": "empty response from the AI provider."}
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"match": "partial", "summary": text.strip()[:400], "issues": []}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"match": "partial", "summary": text.strip()[:400], "issues": []}
    if not isinstance(obj, dict):
        return {"match": "partial", "summary": str(obj)[:400], "issues": []}
    obj.setdefault("issues", [])
    obj.setdefault("match", "partial")
    obj.setdefault("summary", "")
    if not isinstance(obj["issues"], list):
        obj["issues"] = []
    return obj


# ---- provider vision calls ----

def _call_anthropic_vision(prompt: str, image_path: str, key: str,
                           client=None, model: str | None = None) -> str:
    import anthropic
    data, mt = _image_payload(image_path)
    b64 = base64.standard_b64encode(data).decode()
    # pre-built client (e.g. AnthropicBedrock on an IAM role) wins over key
    c = client if client is not None else anthropic.Anthropic(api_key=key)
    r = c.messages.create(
        model=model or "claude-opus-4-5", max_tokens=2000, system=_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    return "".join(b.text for b in r.content if hasattr(b, "text"))


def _call_gemini_vision(prompt: str, image_path: str, key: str) -> str:
    import google.generativeai as genai
    data, mt = _image_payload(image_path)
    genai.configure(api_key=key)
    m = genai.GenerativeModel("gemini-2.0-flash", system_instruction=_SYSTEM)
    r = m.generate_content(
        [{"mime_type": mt, "data": data}, prompt],
        generation_config=genai.GenerationConfig(max_output_tokens=2000),
    )
    return getattr(r, "text", "") or ""
