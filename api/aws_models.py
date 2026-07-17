"""AWS Bedrock model registry — the single place to switch/curate AI models.

This file is GenQ_AWS's analog of DiagramIQ_AWS's `amplify/backend.ts`: the
curated, verified list of Bedrock models plus the operational best practices
learned shipping that app. Edit BEDROCK_MODELS (or set the MODEL_ID env var on
the App Runner service) to change what the AI card offers; the UI dropdown is
served from here, so no frontend rebuild is needed.

Best practices carried over from the DiagramIQ_AWS deploy:

1. ALWAYS use cross-region inference-profile IDs (`us.` / `global.` prefix,
   dated form, e.g. `us.anthropic.claude-opus-4-5-20251101-v1:0`). Newer
   Claude models reject base IDs with "on-demand throughput isn't supported".
2. The Bedrock "Model access" console page is RETIRED — models auto-enable on
   first invoke; Anthropic models need a one-time use-case submission (a
   Marketplace-permitted invoke) per account.
3. Auth is the service's IAM role (`bedrock:InvokeModel`) — no API keys
   anywhere. The Anthropic SDK's Bedrock client falls back to the AWS default
   credential chain automatically (see modelling/llm.py).
4. Keep the model user-switchable at runtime (dropdown + custom field): every
   model issue in DiagramIQ_AWS was fixed without redeploying.
5. Surface backend/Bedrock error text verbatim in the UI — it makes every
   failure (role, model id, throughput, marketplace) diagnosable on sight.
"""

from __future__ import annotations

import os

# Verified working in the target account (us-west-2), listed in quality order.
# The first entry is the default. `note` is shown alongside the id in the UI.
BEDROCK_MODELS: list[dict] = [
    {"id": "us.anthropic.claude-opus-4-5-20251101-v1:0",
     "label": "Claude Opus 4.5", "note": "best quality — default"},
    {"id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
     "label": "Claude Sonnet 4.5", "note": "balanced"},
    {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
     "label": "Claude Haiku 4.5", "note": "fast / cheap"},
    {"id": "us.amazon.nova-2-lite-v1:0",
     "label": "Amazon Nova 2 Lite", "note": "cheap fallback"},
    {"id": "us.mistral.pixtral-large-2502-v1:0",
     "label": "Mistral Pixtral Large", "note": ""},
    {"id": "us.meta.llama4-maverick-17b-instruct-v1:0",
     "label": "Llama 4 Maverick", "note": ""},
    {"id": "us.meta.llama4-scout-17b-instruct-v1:0",
     "label": "Llama 4 Scout", "note": ""},
    {"id": "us.amazon.nova-pro-v1:0",
     "label": "Amazon Nova Pro", "note": ""},
]

# Service-level overrides (set on the App Runner service if desired).
DEFAULT_BEDROCK_MODEL = os.environ.get("MODEL_ID", BEDROCK_MODELS[0]["id"])
DEFAULT_AWS_REGION = (os.environ.get("AWS_REGION")
                      or os.environ.get("AWS_DEFAULT_REGION")
                      or "us-west-2")


def bedrock_by_default() -> bool:
    """True when the service should run Bedrock-first (App Runner sets
    PROCESSIQ_BEDROCK=1 via apprunner.yaml); local dev keeps today's
    Anthropic-key behaviour unless the flag is exported."""
    return os.environ.get("PROCESSIQ_BEDROCK", "").strip() in ("1", "true", "yes")
