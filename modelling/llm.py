"""L2 — LLM provider seam (CLAUDE.md §3: model provider behind an interface).

AnthropicLLM is used when ANTHROPIC_API_KEY is set; otherwise the agent runs on
deterministic SAP priors alone, so the product works end-to-end without a key.
"""

from __future__ import annotations

import os
from typing import Protocol

DEFAULT_MODEL = os.environ.get("PROCESSIQ_MODEL", "claude-sonnet-5")


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class AnthropicLLM:
    """Claude via the Anthropic SDK. The same Messages API shape is served by the
    native API, Google Vertex AI, and AWS Bedrock, so one class covers all three —
    only the client construction differs (see client_for_settings)."""

    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        if client is None:
            import anthropic  # lazy: only needed when a key is configured

            client = anthropic.Anthropic()
        self._client = client
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


def client_for_settings(settings: dict):
    """Build the SDK client for the configured provider, or raise with a clear
    reason (missing SDK extra, missing credentials)."""
    provider = settings.get("provider") or "anthropic"
    if provider == "anthropic":
        import anthropic

        key = settings.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("no Anthropic API key configured")
        return anthropic.Anthropic(api_key=key)
    if provider == "vertex":
        try:
            from anthropic import AnthropicVertex
        except ImportError as exc:
            raise RuntimeError(
                "Vertex support needs `pip install anthropic[vertex]`") from exc
        project = settings.get("vertex_project", "").strip()
        if not project:
            raise RuntimeError("Vertex AI needs a Google Cloud project id")
        return AnthropicVertex(project_id=project,
                               region=settings.get("vertex_region", "").strip()
                               or "us-east5")
    if provider == "bedrock":
        try:
            from anthropic import AnthropicBedrock
        except ImportError as exc:
            raise RuntimeError(
                "Bedrock support needs `pip install anthropic[bedrock]`") from exc
        from api.aws_models import DEFAULT_AWS_REGION

        kwargs = {"aws_region": settings.get("aws_region", "").strip()
                  or DEFAULT_AWS_REGION}
        # explicit keys optional — the SDK falls back to the AWS default chain
        if settings.get("aws_access_key"):
            kwargs["aws_access_key"] = settings["aws_access_key"]
            kwargs["aws_secret_key"] = settings.get("aws_secret_key", "")
        return AnthropicBedrock(**kwargs)
    raise RuntimeError(f"unknown AI provider {provider!r}")


def llm_for_settings(settings: dict) -> LLMProvider | None:
    """Workspace-configured LLM, or None when nothing usable is configured."""
    try:
        client = client_for_settings(settings)
    except RuntimeError:
        return None
    return AnthropicLLM(model=settings.get("model") or DEFAULT_MODEL,
                        client=client)


def default_llm() -> LLMProvider | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    from api.aws_models import bedrock_by_default, DEFAULT_BEDROCK_MODEL

    if bedrock_by_default():
        # AWS deploy (PROCESSIQ_BEDROCK=1): Bedrock on the service's IAM role
        # via the AWS default credential chain — active with zero configuration.
        return llm_for_settings({"provider": "bedrock",
                                 "model": DEFAULT_BEDROCK_MODEL})
    return None
