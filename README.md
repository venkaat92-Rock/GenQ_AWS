# GenQ_AWS — Gentic Mining on AWS (Bedrock, no API keys)

AWS-deployable build of **GenQ** (ProcessIQ process mining + DiagramIQ diagram
intelligence). One **ECS Express Mode** service runs the whole tool; **AWS
Bedrock is the AI backend**, authenticated by the task's IAM role — no API keys
anywhere, the same best practice proven by DiagramIQ_AWS's `amplify/backend.ts`.

> Why not Amplify or App Runner? Amplify can't run Python backends, and App
> Runner stopped accepting new customers on April 30 2026 — AWS's recommended
> replacement is ECS Express Mode, deployed here via GitHub Actions
> (push to `main` → build → deploy, same experience).

- **Where models live:** [`api/aws_models.py`](api/aws_models.py) — the curated,
  verified Bedrock model list + operational best practices (this repo's analog
  of `amplify/backend.ts`). The UI dropdown is served from it.
- **Where you switch models:** in the app — **Data Load → AI Model card**
  (appears once a workspace exists). Pick from the dropdown or type any
  inference-profile ID, then **Test model** shows the model's reply or the
  verbatim Bedrock error. No redeploy needed.
- **One AI everywhere:** the model chosen there powers mapping drafts, the
  copilot, NL Q&A, *and* DiagramIQ notes/vision features.

## Deploy to AWS (ECS Express Mode) — one time, ~15 minutes

1. **CloudFormation** console (region **us-west-2**) → Create stack → *With new
   resources* → **Upload a template file** → pick
   [`infra/genq-aws-setup.yaml`](infra/genq-aws-setup.yaml) from this repo →
   Next → stack name `genq-aws-setup` → Next → acknowledge the IAM checkbox →
   Create. (~2 min. If it fails saying the GitHub OIDC provider already
   exists, recreate with parameter `CreateOIDCProvider=false`.)
   This creates the ECR repo + 4 IAM roles, including **genq-task-role** —
   the app's Bedrock credentials (no API keys).
2. **GitHub** → the `GenQ_AWS` repo → Settings → Secrets and variables →
   Actions → **Variables** tab → New repository variable:
   `AWS_ACCOUNT_ID` = your 12-digit account id (shown in the CloudFormation
   stack **Outputs** tab).
3. GitHub → **Actions** tab → "Deploy to AWS (ECS Express)" → **Run workflow**
   (or just push any commit). First run takes ~10 min (Docker build + first
   service provisioning). The run's deploy step prints the **service URL**;
   it's also in ECS console → Clusters → default → Services → `genq-aws`.
4. Open the URL → **Create a workspace** and go.

Every `git push` to `main` now redeploys automatically
([.github/workflows/deploy.yml](.github/workflows/deploy.yml)).

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

- **Ephemeral workspaces:** data lives on the task's ephemeral disk (`/data`
  inside the container); a redeploy resets it — re-upload CSVs or reload the
  demo dataset. S3 persistence is the natural next step.
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
