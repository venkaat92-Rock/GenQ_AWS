"""Per-workspace AI provider for ProcessIQ's modelling agent and copilot.

The agent's semantic work is deterministic-first (priors + human confirmation,
CLAUDE.md §1.1); the LLM only narrates and drafts. This module lets each
workspace pick WHERE Claude runs: the Anthropic API, Google Vertex AI, or AWS
Bedrock — all three serve the same Messages API through the Anthropic SDK.

Secrets are stored server-side per workspace and never returned by GET (masked
to has_* booleans). v1 posture: stored as plain JSON like connector credentials;
encryption is on the pre-deploy hardening list.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from modelling.llm import (DEFAULT_MODEL, AnthropicLLM, client_for_settings,
                           llm_for_settings, default_llm)
from api.aws_models import (BEDROCK_MODELS, DEFAULT_BEDROCK_MODEL,
                            DEFAULT_AWS_REGION, bedrock_by_default)

PROVIDERS = ("anthropic", "vertex", "bedrock")

_SECRET_FIELDS = ("anthropic_key", "aws_access_key", "aws_secret_key")


def _ai_file(ws: dict):
    return ws["dir"] / "ai.json"


def load_ai_settings(ws: dict) -> dict:
    if _ai_file(ws).exists():
        return json.loads(_ai_file(ws).read_text(encoding="utf-8"))
    if bedrock_by_default():
        # AWS deploy: Bedrock on the instance role, no keys (api/aws_models.py)
        return {"provider": "bedrock", "model": DEFAULT_BEDROCK_MODEL,
                "aws_region": DEFAULT_AWS_REGION}
    return {"provider": "anthropic", "model": DEFAULT_MODEL}


def _save(ws: dict, settings: dict) -> None:
    tmp = _ai_file(ws).with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(_ai_file(ws))


def workspace_llm(ws: dict):
    """The LLM every ProcessIQ feature should use: workspace-configured provider
    first, then the server's ANTHROPIC_API_KEY environment fallback."""
    return llm_for_settings(load_ai_settings(ws)) or default_llm()


def _sdk_available(extra: str) -> bool:
    try:
        if extra == "vertex":
            from anthropic import AnthropicVertex  # noqa: F401
        elif extra == "bedrock":
            from anthropic import AnthropicBedrock  # noqa: F401
        else:
            import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


class AiSettingsIn(BaseModel):
    provider: str
    model: str = ""
    anthropic_key: str | None = None       # None = keep stored value; "" = clear
    vertex_project: str | None = None
    vertex_region: str | None = None
    aws_access_key: str | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None


def _masked(ws: dict, settings: dict) -> dict:
    active = llm_for_settings(settings) is not None
    return {
        "provider": settings.get("provider", "anthropic"),
        "model": settings.get("model") or DEFAULT_MODEL,
        "has_anthropic_key": bool(settings.get("anthropic_key")),
        "has_server_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "vertex_project": settings.get("vertex_project", ""),
        "vertex_region": settings.get("vertex_region", ""),
        "has_aws_keys": bool(settings.get("aws_access_key")),
        "aws_region": settings.get("aws_region", ""),
        "sdk": {p: _sdk_available(p) for p in PROVIDERS},
        "active": (active or bool(os.environ.get("ANTHROPIC_API_KEY"))
                   or bedrock_by_default()),
        "source": ("workspace" if active
                   else "environment" if os.environ.get("ANTHROPIC_API_KEY")
                   else "iam-role" if bedrock_by_default()
                   else "none"),
        # Curated registry (api/aws_models.py) — drives the UI dropdown.
        "bedrock_models": BEDROCK_MODELS,
        "bedrock_default": bedrock_by_default(),
        "default_aws_region": DEFAULT_AWS_REGION,
    }


def make_router(workspace_dep) -> APIRouter:
    router = APIRouter(prefix="/api/ai-provider")

    @router.get("")
    def get_ai(ws: dict = Depends(workspace_dep)):
        return _masked(ws, load_ai_settings(ws))

    @router.put("")
    def put_ai(body: AiSettingsIn, ws: dict = Depends(workspace_dep)):
        if body.provider not in PROVIDERS:
            raise HTTPException(400, f"provider must be one of {PROVIDERS}")
        settings = load_ai_settings(ws)
        settings["provider"] = body.provider
        settings["model"] = body.model.strip() or DEFAULT_MODEL
        for field in ("vertex_project", "vertex_region", "aws_region",
                      *_SECRET_FIELDS):
            value = getattr(body, field)
            if value is not None:  # None keeps the stored secret untouched
                settings[field] = value.strip()
        _save(ws, settings)
        return _masked(ws, settings)

    @router.post("/test")
    def test_ai(body: AiSettingsIn | None = None,
                ws: dict = Depends(workspace_dep)):
        """Invoke the (draft or saved) model once and return the raw outcome.

        Best practice from the DiagramIQ_AWS deploy: surface the exact
        Bedrock/SDK error text — it makes IAM-role, model-id, throughput and
        marketplace failures diagnosable straight from the UI."""
        settings = load_ai_settings(ws)
        if body is not None:
            if body.provider not in PROVIDERS:
                raise HTTPException(400, f"provider must be one of {PROVIDERS}")
            settings["provider"] = body.provider
            settings["model"] = body.model.strip() or settings.get("model") \
                or DEFAULT_MODEL
            for field in ("vertex_project", "vertex_region", "aws_region",
                          *_SECRET_FIELDS):
                value = getattr(body, field)
                if value is not None:  # None keeps the stored secret
                    settings[field] = value.strip()
        model = settings.get("model") or DEFAULT_MODEL
        try:
            client = client_for_settings(settings)
        except RuntimeError as exc:
            # misconfiguration (missing key/SDK/project) — reason, verbatim
            return {"ok": False, "model": model, "error": str(exc)}
        llm = AnthropicLLM(model=model, client=client)
        try:
            reply = llm.complete("You are a connectivity check.",
                                 "Reply with the single word OK.")
            return {"ok": True, "model": model, "reply": reply.strip()[:200]}
        except Exception as exc:  # surface provider error text verbatim
            return {"ok": False, "model": model, "error": str(exc)}

    return router
