# GenQ_AWS — Gentic Mining on AWS (Bedrock, no API keys)

AWS-deployable build of **GenQ** (ProcessIQ process mining + DiagramIQ diagram
intelligence). One App Runner service runs the whole tool; **AWS Bedrock is the
AI backend**, authenticated by the service's IAM role — no API keys anywhere,
the same best practice proven by DiagramIQ_AWS's `amplify/backend.ts`.

- **Where models live:** [`api/aws_models.py`](api/aws_models.py) — the curated,
  verified Bedrock model list + operational best practices (this repo's analog
  of `amplify/backend.ts`). The UI dropdown is served from it.
- **Where you switch models:** in the app — **Data Load → AI Model card**
  (appears once a workspace exists). Pick from the dropdown or type any
  inference-profile ID, then **Test model** shows the model's reply or the
  verbatim Bedrock error. No redeploy needed.
- **One AI everywhere:** the model chosen there powers mapping drafts, the
  copilot, NL Q&A, *and* DiagramIQ notes/vision features.

## Deploy to AWS (App Runner) — one time, ~10 minutes

1. **Instance role** (IAM console → Roles → Create):
   - Trusted entity: **Custom trust policy** with service `tasks.apprunner.amazonaws.com`:
     ```json
     {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
       "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
       "Action": "sts:AssumeRole"}]}
     ```
   - Inline permission policy (name it e.g. `bedrock-invoke`):
     ```json
     {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
       "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
       "Resource": "*"}]}
     ```
2. **App Runner console** (region **us-west-2**) → Create service:
   - Source: **GitHub** → connect → repository `GenQ_AWS`, branch `main`,
     **automatic deploys ON**.
   - Build settings: **Use a configuration file** (`apprunner.yaml` — already in
     the repo; it sets runtime, port 8000 and env vars).
   - Service settings: 1 vCPU / 2 GB is enough to start. **Min & max size 1
     instance** (the scheduler and DuckDB are single-instance by design).
   - Security → **Instance role**: pick the role from step 1.
3. Create & wait for the first deploy → open the service URL → **Create a
   workspace** and go.

Every `git push` to `main` now redeploys automatically.

### Bedrock model access (once per AWS account)

The old Bedrock "Model access" console page is **retired** — models auto-enable
on first invoke. Anthropic (Claude) models need a **one-time use-case
submission** in the Bedrock console before the first call succeeds. If a model
errors, the **Test model** button in the AI card shows the exact Bedrock error
text (role, model ID, throughput, marketplace) so it's diagnosable on sight.

**Always use inference-profile IDs** (the `us.` prefix, dated form, e.g.
`us.anthropic.claude-opus-4-5-20251101-v1:0`) — newer Claude models reject base
IDs with "on-demand throughput isn't supported".

## Switching / curating models

- **Runtime (no deploy):** Data Load → AI Model card → pick or type a model →
  Save. `Test model` verifies it live.
- **Change the curated list or default:** edit
  [`api/aws_models.py`](api/aws_models.py) and push, or set the `MODEL_ID`
  env var on the App Runner service.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows (bin/ on Linux/macOS)
cd ui && npm install && npm run build && cd ..       # rebuild api/static after UI edits
.venv/Scripts/python -m uvicorn api.main:app --port 8000
# open http://localhost:8000
```

Locally the app behaves like upstream GenQ (Anthropic key or per-workspace
credentials). To exercise the AWS behaviour locally set `PROCESSIQ_BEDROCK=1`
(uses your local AWS credential chain).

Tests: `.venv/Scripts/python -m pytest`

## v1 caveats (deliberate)

- **Ephemeral workspaces:** data lives on the instance disk
  (`PROCESSIQ_DATA=/tmp/processiq-data`); a redeploy resets it — re-upload CSVs
  or reload the demo dataset. S3 persistence is the natural next step.
- Workspace API keys and provider settings are plain JSON on that disk (shown
  once at creation) — fine for a private POC, harden before public sign-ups.
- Single instance by design (in-process scheduler, single-writer DuckDB).
- Ollama-based diagram options don't apply on AWS (no local Ollama).

## Architecture

```
L5  self-serve   api/nlq.py + ui/          NL -> intent -> cited answer
L4  mining       mining/                   PM4Py: variants, conformance, DFG, metrics
L3  store        engine/db.py              DuckDB (ClickHouse seam)
L2  modelling    modelling/                agent drafts -> human confirms -> materialize
L1  extraction   CSV/table upload
AI  provider     modelling/llm.py          one Messages API: Bedrock (IAM) / Anthropic / Vertex
                 api/aws_models.py         curated Bedrock registry ("backend.ts of GenQ_AWS")
```

Upstream design contract: [CLAUDE.md](CLAUDE.md); session history: [PROGRESS.md](PROGRESS.md).
