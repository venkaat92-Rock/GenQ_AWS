# PROGRESS.md — ProcessIQ

Read this at the start of every session before doing anything. Append at the end of every
session: what was built, decisions made, what's next, open questions.

---

## Phase 0 — Skeleton (DONE — 2026-07-04)

Checklist:
- [x] Repo scaffolded per CLAUDE.md §6 (`engine/ mining/ modelling/ extraction/ api/ ui/ fixtures/ specs/ tests/`)
- [x] `CLAUDE.md` + `PROGRESS.md` in place
- [x] Env: Python 3.12 venv at `.venv/` with duckdb, pm4py, pytest
- [x] Synthetic P2P event log fixture (`fixtures/p2p_event_log.csv`, 20 cases / 107 events, 4 variants incl. rework + skip)
- [x] DuckDB up: fixture loads into `event_log` table (L3) — 107 events / 20 cases
- [x] PM4Py variant analysis runs end-to-end, top variants printed (L4) — happy path = 12 cases (60%)
- [x] pytest test asserting expected top variant on the fixture — 3 passed
- [x] Session logged below; STOPPED before Phase 1 pending review

### Session log

**2026-07-04 — Session 1 (Phase 0)**
- Built: repo skeleton, `EventLogStore` (DuckDB, L3) in `engine/db.py`, `variant_analysis`
  (PM4Py wrapper, L4) in `mining/variants.py`, deterministic fixture generator
  (`fixtures/generate_p2p_log.py`, seed 42) + committed CSV, `run_phase0.py` spine script,
  `tests/test_variants.py`.
- Decisions:
  - **Python 3.12** (not system 3.14) for the venv — better compiled-wheel compatibility for
    PM4Py's dependency chain.
  - **Location**: scaffolded as `processiq/` inside the user's existing desktop folder
    (`Fitfamly` git repo also lives there). NOT committed to that repo. Open question below.
  - Fixture is generated-then-committed (seeded, deterministic) so tests never depend on
    regeneration.
  - `EventLogStore` validates required columns (`case_id`, `activity`, `timestamp`) at load —
    first small guard against silent semantic errors (§1.1).
- Next: finish Phase 0 run + tests (this session), then STOP and show results. Phase 1 (mining
  core: DFG, conformance, bottleneck/rework/throughput) only after explicit go-ahead.
- Open questions: none.
  - (Resolved 2026-07-04) `processiq/` is now its own git repo, pushed to
    https://github.com/venkaat92-Rock/processIQ_Venkat.git (branch `main`).

---

## Phases 1–3 + SaaS packaging (DONE — 2026-07-04)

**2026-07-04 — Session 2 (user-directed override: build Phases 1–3 + API + UI in one push,
"run as a SaaS product not desktop"). Non-negotiables (§1) fully preserved.**

- Built:
  - **Phase 1 (L4)**: `mining/dfg.py` (frequency+performance DFG), `mining/conformance.py`
    (token replay vs reference sequences + exact-variant share), `mining/metrics.py`
    (throughput, bottlenecks, rework). 5 fixture tests.
  - **SAP fixtures**: `fixtures/generate_fixtures.py` emits the (byte-identical) flat log +
    8 SAP-style source tables (EKKO/EKPO/LFA1/CDHDR/MSEG/RBKP/RSEG/BKPF) from one seeded model.
  - **Phase 2 (L2, the crux)**: `modelling/spec.py` (versioned YAML spec, confidence levels,
    blocking open questions, named-reviewer confirm), `modelling/priors.py` (deterministic SAP
    P2P drafter), `modelling/llm.py` (Anthropic provider seam; product fully works without a
    key), `modelling/agent.py` (draft → enforce-escalation → redraft-on-answers; LLM may
    refine language but can never change SQL or drop the granularity question),
    `modelling/materialize.py` (hard confirm gate; generated SQL stored on the spec for audit).
    6 tests incl. the §4 first milestone: materialized log == hand-built reference log.
  - **Phase 3 (L5)**: `api/nlq.py` — NL question → fixed intent table (variants/bottlenecks/
    rework/throughput/conformance/dfg) + vendor filter; answers carry citations. LLM routes
    intents when configured but can only pick from the same auditable intents.
  - **SaaS backend**: `api/main.py` + `api/storage.py` — multi-tenant workspaces with API
    keys, per-workspace DuckDB + versioned spec history, CSV upload, demo-data endpoint,
    mapping draft/answers/confirm endpoints (409 on unconfirmed materialize), analysis
    endpoints, /api/ask. 3 API journey tests.
  - **UI**: `ui/` Vite+React+TS, served by FastAPI from `api/static`. Steps: workspace →
    data → mapping review (confidence-sorted activity cards, inspectable SQL, blocking
    question UX, named-reviewer confirm) → dashboard (KPIs, variants, bottlenecks, rework) →
    ask (citations rendered as chips).
  - **Packaging**: multi-stage Dockerfile (`/data` volume), README with local + Docker deploy.
- Verified: 17/17 pytest green; full journey driven in a real browser (workspace creation →
  demo data → draft → answer → confirm → 107 events materialized → dashboard KPIs correct →
  NL bottleneck answer with citations).
- Decisions:
  - "SaaS not desktop" = single Docker-deployable FastAPI+React service, multi-tenant via
    API-key workspaces. L1 stays CSV/table upload for v1 (per build order, connectors last).
  - LLM strictly optional and fenced: refines language/asks more questions, never writes the
    executable SQL. Reason: silent-semantic-error risk (§1.1) + product must demo without keys.
  - Conformance reference in self-serve = most frequent variant, and answers say so (traceable).
  - v1 auth is plain API keys in tenants.json — logged as a pre-public-launch swap.
- Next: Phase 4 (real SAP extraction) and hardening (hashed keys, ClickHouse seam) on go-ahead.

---

## First real-user upload: bug fixes + priors generalization (2026-07-05)

User uploaded 7 real test CSVs (workspace `test_4_july`) and hit a 500 on `/api/sources`.
- Root cause 1: all-empty columns (RBKP.ZLSPR) sampled as float NaN — strict JSON
  (`allow_nan=False`) rejected the response. Fix: `schema_summary` now emits None for
  NaN/NaT. Frontend also hardened: non-JSON error bodies no longer crash `api()`;
  uploads report per-file failures and keep going.
- Root cause 2 (found proactively): the priors matcher required exact SAP table names,
  but the user's export used `EKKO_PO_HEADER`, merged `MKPF_MSEG_GOODS_RECEIPT`,
  `BSAK_VENDOR_ITEMS`, YYYYMMDD integer dates, and FRGDAT-based approvals (no CDHDR).
  Priors now: resolve tables by token; fall back to EKKO.FRGDAT for approvals and BSAK
  (AUGDT clearing date) for payments; accept BUDAT/BUDAT_MKPF for GR; normalize
  ISO-or-YYYYMMDD timestamps in SQL; drop NULL case/timestamp rows at materialize.
  Every adaptation is stated in the mapping rationale (§1.1).
- Tests: +3 regression tests reproducing the user's export shape (20 total, green).
- Verified: `/api/sources` returns 200 on the user's actual workspace; on a clone of
  their warehouse the agent drafted 5 activities from the renamed tables, raised the
  granularity question, and materialized 52 events / 10 cases with 0 dropped timestamps.
  Their workspace itself was left untouched (no spec written) so they drive the
  confirmation loop themselves.

---

## Process & variant explorer (2026-07-05)

User feedback: dashboard lacked the Celonis-style process map / variant explorer.
- Added `ui/src/components/ProcessMap.tsx`: SVG directly-follows map, hand-rolled layered
  layout (longest-path layering, barycenter ordering, DFS back-edge detection). Edge width
  scales with case frequency; labels show cases + average wait; rework loops render as
  dashed orange return arcs; Start/End markers. No graph library — P2P-scale graphs are
  small (logged decision: earn the dependency).
- Added `ui/src/components/VariantExplorer.tsx`: Celonis-style variant list (share bars,
  mini path preview, all/none) driving the map — tick variants and the map redraws from
  exactly those traces; coverage line shows N of M cases. Frequencies come from the variant
  traces (exact); durations from the full performance DFG (labeled as overall averages).
- Dashboard's variants table replaced by the explorer card.
- Verified in browser on demo data: full map with both rework arcs; isolating the rework
  variant shows 4/20 cases with the re-approval loop; arrowheads fixed-size via
  markerUnits=userSpaceOnUse.

---

## Celonis/Signavio feature-parity round (2026-07-05)

User asked for a feature comparison vs Celonis Process Mining and SAP Signavio Process
Intelligence, and to close the gaps within our framework (no data engineer/analyst; business
SME as the only reviewer). Comparison logged in docs/FEATURE_COMPARISON.md.

Added (backend + UI, all filter-aware):
- **Global analysis filters** (`api/filters.py`): vendor, case-start date range, duration
  min/max, include/exclude activity, only-rework — applied at case level server-side on every
  analysis endpoint; applied filters echoed back and shown as chips (traceability).
  `/api/analysis/facets` feeds the filter bar.
- **Case explorer** (`mining/cases.py`, `/api/analysis/cases`, `/api/analysis/case/{id}`):
  per-case duration/variant/rework/conformance + attributes; sortable UI table with
  click-through event timeline.
- **SME reference process** (`/api/reference` GET/PUT, `reference.json` per workspace):
  Signavio-style to-be target as an ordered activity list, editable in the UI with mandatory
  SME sign-off; conformance + insights + case flags all use it (fallback: most frequent
  variant, and answers say which was used).
- **Insights (root-cause lite)** (`mining/insights.py`): transparent comparisons — bottleneck
  share of cycle time, vendor duration/rework comparison, cost of rework, deviation cost —
  each narrated with its basis. Deliberately no opaque ML (§1.6).
- **Performance mode** on the process map: heat-colored, duration-weighted edges.
- Explorer keeps variant selection valid when filters change.

Out of scope by design (logged): OCPM, action flows/automation, task mining, BPMN modeling
repository, ML forecasting/simulation.

Tested: 25 pytest green (5 new feature tests). Browser-tested on demo sample data with
screenshots: insights card, filter panel + vendor filter (3/20 cases), rework-only filter +
performance-mode map, case timeline drill-down, SME reference save.

---

## Data Load canvas + Ask co-pilot (2026-07-05)

User asked for a Celonis-style Data Integration view where every attribute is auto-filled by
the AI agent (human-editable), with access to the AI-created data model, plus an Ask
co-pilot chat dock (context-aware: click a canvas element, ask about it) on Data, Data load,
and Dashboard.

- `api/dataload.py` + endpoints: GET /api/dataload assembles the canvas from REAL workspace
  state (staged uploads = connections, agent SQL fragments = transformations, recorded
  materializations = model loads, DuckDB file = storage, spec = data model with
  authored-by/confirmed-by). POST /api/dataload/run = genuine Run-now re-materialization;
  runs recorded in dataload_runs.json (confirm-triggered loads recorded too). No fake
  numbers: schedules honestly shown as on-confirm+manual until Phase 4 connectors.
- POST /api/copilot: context-aware Q&A. Facts assembled from the canvas; LLM path when
  ANTHROPIC_API_KEY set (facts-only prompt), deterministic template answers otherwise;
  process-metric questions fall through to the L5 NL layer. Every answer reports scope +
  engine.
- UI: new "Data load" step — 4-lane canvas (Sources / Data Integration [AI + HUMAN badge] /
  Output / Consumers) with clickable cards that set the co-pilot context; Run now; View data
  model (inline spec with confidence + SQL) and Edit-in-Mapping-review. `Copilot.tsx` dock
  (collapsible, context chip, suggestions) rendered on Data, Data load, Dashboard.
- Tests: +3 (dataload before/after model, run-now gating + run recording, copilot context/
  general/process fallthrough). 28 total, green. Browser-verified with screenshots.

---

## Data model ERD + custom KPIs via co-pilot (2026-07-05, ultracode round)

Explored the user's Celonis sandbox (G-AP Accelerator pool): Schedules (delta-load triggers),
Data Jobs (268 hand-written transformation tasks), and the Data Model tab (ERD, foreign keys
on shared SAP keys, per-table identifiers, Missing-Identifiers warning). Replicated the
meaningful subset with the agent doing the work humans do in Celonis:

- `api/datamodel.py`: agent-derived data model — FKs parsed from the mapping's executed SQL
  joins + shared-key-column proposals; identifiers probed against real data (unique-column
  check) with Celonis-style missing-identifier warnings. Human edits (add/remove FK, set
  identifier) via PUT with SME sign-off; provenance agent/human tracked; derivation keyed to
  a fingerprint (spec version/status + staged tables) and MERGED on change so human edits
  survive and stale snapshots can't freeze (rederive endpoint resets to pure agent view).
- `mining/kpis.py` + endpoints: custom KPI engine (cycle_time between milestones,
  transition_wait, case_share, case_count, attribute_agg) computed filter-aware; NL parser
  turns "create a KPI: average days from X to Y" into a definition.
- Co-pilot Create-KPI: /api/copilot detects the intent, returns proposal + live preview; UI
  shows Save KPI in the chat; saved KPIs render as removable dashboard tiles.
- UI: DataModelView (SVG ERD — EVENT_LOG hub, FK lines with column labels, solid=executed /
  dashed=proposed / green=human; FK table editor; identifier dropdowns; re-derive button).
- **Ultracode adversarial review** (18 agents, find→verify): 15 findings confirmed and ALL
  fixed — highlights: NL-KPI parser substring hijacking ("accounts" matched "count");
  datamodel freeze-on-first-GET; attribute_agg 500s (bad agg + NaN through strict JSON);
  SQL injection via crafted CSV headers in identifier probing; malformed filter dates and
  relationship payloads returning 500 instead of 400; transition_wait counting transitions
  as cases. +9 regression tests. 41 total, green. Browser-verified with screenshots.
- Out of scope kept out: OCPM object links, PQL preamble, business calendar, BPMN editor.

---

## Functional canvas details + real schedules (2026-07-05)

User feedback: the Schedule / Data Jobs / Data Storage / Objects-and-Events cards felt like
dummies — in Celonis each opens a working view. Now they do here too, each with its purpose
stated and human controls:

- **Schedules are real** (`api/scheduler.py`): a daemon scan (60s) re-materializes due
  workspaces hourly/daily. SME-editable (enable/frequency/name) via GET/PUT /api/schedule;
  enabling requires a confirmed data model (§1.1 — schedules never bypass the human gate);
  runs recorded with status ok/skipped/failed; next_run advances from scan time.
- **Data Jobs detail**: Celonis-style Tasks tab ("Add Activity: …" rows with source +
  confidence) + Logs tab (full run history via /api/dataload/runs, newest first) + Execute
  data job + Edit in Mapping review.
- **Storage detail**: engine, size on disk, per-table rows incl. the materialized EVENT_LOG.
- **Objects & Events detail**: case/event totals + events-per-activity breakdown.
- Clicking a card scrolls its detail into view and scopes the co-pilot to it.
- Fixed a React hooks-order crash (useRef after early return) caught during verification.
- Tests: +4 scheduler tests (gate before confirmed model, SME sign-off, due-run execution +
  logging + next_run advance, disabled/unconfirmed skip). 45 total, green. Browser-verified:
  hourly schedule enabled by "Venkat (SME)" with next-load banner; task list renders all 8
  agent transformations.

---

## Editable SQL + plain-English SQL explanations; ERD zoom/pan (2026-07-05)

- ERD: zoom controls (50–300%, Fit) + Celonis-style drag-to-pan on the data model canvas.
- `modelling/sql_explain.py`: deterministic SQL→English explainer (tables, joins, WHERE
  conditions, case id/timestamp source, DISTINCT), regenerated on every change so the
  explanation can never drift from the code. Explanations shown above every SQL block in
  Mapping review and backfilled lazily for specs drafted before the feature.
- PUT /api/mapping/sql: human SQL override per activity — test-run against staged tables
  before saving (400 with the engine's message when invalid), explanation regenerated,
  `sql_edited_by` provenance recorded, and the spec DROPS BACK TO DRAFT so edited logic
  never runs without re-confirmation (§1.1). Human SQL edits survive agent re-drafts and
  outrank fresh agent SQL. LLM refinement still can't touch SQL.
- Mapping review UI: "In plain English" panel + ✎ Edit this SQL (textarea, SME name,
  Validate & save) inside each activity's SQL details.
- Tests: +3 (explanations on all drafted activities incl. join/DISTINCT parsing; edit
  validation/re-explain/re-confirm gate/blocked run). 48 total, green. Browser-verified.

---

## Second offering: DiagramIQ (2026-07-06)

User asked to bring their Diagram.IQ repo (desktop BPMN tool) into the SaaS as a second
offering with a home-screen picker.

- Vendored the engine from github.com/venkaat92-Rock/Diagram.IQ `src/` into `diagramiq/`
  (bpmn_parser, bpmn_validator, excel_to_bpmn, bpmn_to_excel, signavio_normalize,
  local_uplift, transcription_to_excel) with package-relative imports. Deliberately
  excluded (logged): Visio/image OCR paths (OpenCV/EasyOCR/PyTorch desktop weight),
  Tkinter app, AI compliance/auspost passes.
- `diagramiq/service.py`: sync wrappers over the engine's callback API + a deterministic
  rule-based notes parser ('Role: step' lanes) so the whole pipeline works keyless;
  Anthropic used automatically when ANTHROPIC_API_KEY is set (engine badge always shown).
- `/api/diagram/*`: notes→discovery-Excel (sample included), excel→BPMN (Signavio-
  normalized + validation), validate, rule-based uplift (issues before/after), BPMN→Excel
  review round-trip, artifact list/download/xml. Per-workspace artifacts in <ws>/diagrams/.
- UI: Home offering picker (ProcessIQ | DiagramIQ, sidebar switch + brand swap), DiagramIQ
  Studio page (sample/notes/excel/bpmn uploads, artifact table with per-kind actions,
  bpmn-js NavigatedViewer, severity-grouped validation report). New dependency: bpmn-js
  (earned: standard BPMN renderer, no hand-rolled alternative is sane).
- Tests: +2 (full pipeline from sample notes incl. Signavio markers in XML, uplift
  non-regression, auth/input-validation/path-traversal). 50 total, green. Browser-verified:
  home picker → sample notes (12 steps / 7 participants, rule-based badge) → BPMN with
  swimlanes rendered in the viewer (25 shapes).

---

## DiagramIQ quality gap vs desktop — found & fixed (2026-07-06)

User reported SaaS results worse than desktop. Root causes (diff vs desktop main.py):
1. Notes analysis silently fell back to the rule-based parser (no AI key on the server) —
   the user's own Parcel Delivery transcript went through it (166 shapes, 438 findings).
   Desktop always used a configured AI provider.
2. Desktop runs `fix_layout` (bpmn_layout_fixer) after every Excel→BPMN — not vendored.
3. Desktop uplift = local_uplift + `apply_advanced_rules` (gateway pairing, verb-at-start,
   orthogonal routing) + fix_layout — only local_uplift was vendored.
4. Desktop validation = generic validator + `signavio_rules.validate_signavio` — only the
   generic one was vendored.

Fixes: vendored bpmn_layout_fixer/bpmn_advanced_rules/signavio_rules; pipeline now matches
the desktop chains exactly (convert: build→fix_layout(compact)→normalize; uplift: rules→
advanced→layout→normalize with change log; validate: both validators, source-tagged).
Added the desktop's ⚙ Settings equivalent: per-workspace AI provider (auto/anthropic/
ollama/rule-based) with a workspace Anthropic key + availability chips; honest warning
banner when only the rule-based parser is reachable. Uplift test invariant corrected to
"errors never increase" (the richer ruleset legitimately reports more info findings).
50 tests green; verified in browser against the user's own artifact.

---

## Second standard process: Order-to-Cash + business demo (2026-07-06)

User asked for a realistic order-management demo "acting as a business". Prerequisite
built first (within §1.2 — "a small set of standard processes"):
- `draft_o2c_spec` in priors: VBAK/VBAP/KNA1/CDHDR(VERKBELEG)/LIKP+LIPS/VBRK+VBRP/BSAD →
  Order Created / Order Changed / Delivery Created / Goods Issued / Invoice Created /
  Payment Received; customer + net_value attributes; sales-order granularity question.
- Agent auto-detects the process family (EKKO→P2P, VBAK→O2C; both staged → explicit error).
  Re-drafts route by spec.process.
- Analytics generalized vendor→party (vendor|customer): case table carries all attributes,
  filters/facets/insights/NLQ/CaseExplorer/FilterBar use the detected party column.
- Realistic O2C dataset generator (fixtures/o2c, seed 2026): 80 Northwind Trading orders,
  variants incl. credit-hold rework, partial deliveries (2 waves), open receivables,
  parked orders, slow fulfilment; customers with payment-behaviour profiles (Evergreen =
  slow payer). Demo endpoint gained ?dataset=p2p|o2c; Data page has both demo buttons.
- Fixed hardcoded "Procure-to-pay" dashboard subtitle (summary now returns the process).
- Tests: +2 O2C (auto-detect + full pipeline incl. customer facets/filters/insights/NLQ/
  KPI parser). 52 total, green.
- Demo executed in browser as "Maria Chen (Ops Manager)": 412 events / 80 orders
  materialized; dashboard found invoice→payment as 98% of cycle time (29.4d avg),
  Evergreen 3.2x slower than Alpine, rework concentrated on Borealis; DSO KPI created via
  co-pilot (30.04d) and pinned.

---

## Visio -> BPMN (from DiagramIQ-Lonza) (2026-07-06)

- Vendored `visio_to_bpmn.py` (42kb, pure stdlib zipfile+ET) and `ai_visio_image_check.py`
  from venkaat92-Rock/DiagramIQ-Lonza, plus its sample .vsdx fixtures (ecc_workflow,
  purchase_requisition).
- `/api/diagram/visio`: .vsdx upload (or bundled sample) → Signavio-ready BPMN with
  **preserve-layout mode** (original Visio geometry + swimlane bands carried into BPMN DI,
  Lonza's fidelity mode; off = re-grid via layout fixer) + validation report + optional
  **AI vision cross-check** against an attached screenshot (Anthropic key from the studio
  settings; gracefully reports "skipped" without one). Binary .vsd → clear 400 pointing at
  .vsdx/desktop (needs Visio COM).
- Studio UI: "⬆ Visio (.vsdx)" opens an options panel (preserve layout toggle, optional
  screenshot attach, convert) + vision-check result card.
- Tests: +3 (engine converts both Lonza samples w/ lanes preserved; .vsd rejected; endpoint
  flow incl. sample shortcut and keyless vision-check skip). 55 total, green.
  Browser-verified: sample converts to 28 shapes across 3 preserved swimlanes in bpmn-js.

---

## Ready-made process connectors (Celonis-catalog equivalent) (2026-07-06)

User asked to replicate Celonis' process-connector catalog (their sandbox pool page +
docs.celonis.com/process-connector-installation). A Celonis process connector installs
extractions + transformations + a data model per system/process; ours keeps the §1.1
twist — transformations/data model come from the agent + SME confirm, the connector
supplies the extraction knowledge.

- `connectors/catalog.py`: 12 connectors grounded in the Celonis standard catalog —
  SAP P2P + O2C (agent support: FULL), SAP AP + AR (PARTIAL), Oracle EBS P2P + O2C,
  Salesforce Opportunity + Case, ServiceNow Incident, Jira Issues, Dynamics 365 F&O P2P,
  Coupa P2P (TEMPLATE: guided extraction; dedicated priors roadmap). Each: table list w/
  descriptions, key columns, required flags, export hints.
- `connectors/extract.py`: LIVE extractors, genuinely executable — sqlite / postgres /
  mysql via DuckDB scanners (ATTACH READ_ONLY, per-table copy into src_*), CSV-by-URL
  (100MB cap). Credentials used per request, never persisted.
- Endpoints: GET /api/connectors (+detail), GET /api/connectors/{id}/templates (zip of
  header-only CSVs + README), POST /api/connectors/extract.
- Data page UI: connector chip grid w/ support badges, detail panel (tables/columns/
  required + CSV template download), live-connection form (kind/DSN/tables/schema).
- Tests: +3 — catalog+zip; sqlite end-to-end (extract -> /api/sources -> agent drafts
  P2P from the live-extracted tables); URL fetch via a local http server + error paths.
  58 total, green. Browser-verified: 12-connector grid + SAP P2P detail panel.
- Honest boundaries: postgres/mysql code paths identical to the tested sqlite path but
  not exercised against live servers here; SAP RFC/OData live pulls remain Phase 4.

---

## Live API connectors + saved connections + scheduled re-extraction (2026-07-06)

User push-back: "I'm trying to make a live connection with the source system, why still
CSV?" — fair. Built real API extractors (`connectors/live_api.py`):
- **ServiceNow** Table API (instance + basic auth, paginated), **Jira Cloud** REST v2
  (issues + changelog flattened to event-ready tables), **Salesforce** REST/SOQL
  (FIELDS(ALL) with nextRecordsUrl paging, token auth), **generic OData v2/v4**
  (SAP Gateway / Dynamics 365; handles d.results/__next and value/@odata.nextLink).
  All read-only GETs, 50k-row caps, engine.stage_rows() lands records as src_* exactly
  like uploads. Nested attributes flattened; __metadata/attributes stripped.
- **Saved connections** per workspace (connections.json; secrets masked in every API
  response, stored plain server-side in v1 — flagged in UI, least-privilege advised):
  save & re-run by name, delete.
- **Scheduled re-extraction**: due schedules now re-extract every auto_refresh connection
  BEFORE materializing — the full Celonis loop (schedule → extract → transform → load).
  Run log records refreshed_connections / refresh_errors.
- UI: live panel with per-kind forms (8 kinds), save-as + auto-refresh, saved-connections
  table with re-extract/delete.
- Tests: +3 files worth — protocol-faithful mock servers for all four APIs (shape-exact
  JSON), saved-connection masking/re-run/delete, and the loop test: source CSV changes →
  schedule fires → EXTRA_FEED grows 1→3 rows → run log shows the refresh. 61 total, green.
- Boundary unchanged and stated: SAP RFC needs SAP's licensed SDK (desktop/on-prem
  territory); SAP over OData Gateway is now genuinely supported.

---

## Enterprise UI/UX overhaul (2026-07-06)

User feedback: UI should be enterprise-grade, predefined demo files must not be pushed,
and the "In plain English … both work" transformation phrasing was unprofessional.

- Design system rewrite (styles.css, class names preserved): restrained enterprise
  palette (slate + deep blue), refined type scale w/ uppercase card headers and tabular
  numerals, consistent 6-8px radii, subtle shadows, focus rings, denser tables with row
  hover, professional sidebar (left-border active state), refined copilot dock/banners.
- Transformation summaries restructured (modelling/sql_explain.py): labeled
  specification rows — Source / Join / Filter / Case reference / Event timestamp /
  Deduplication — rendered as a definition table in Mapping review ("Transformation
  detail"). Regenerated lazily for existing specs. Tests updated to the new format.
- Demos demoted: Data page now leads with "Connect a source" (live connection chip
  first, then the connector catalog), uploads second; sample datasets collapsed under an
  understated "Evaluation datasets (synthetic)" disclosure.
- Copy sweep: emoji/casual labels removed across all components (Copilot, Run now,
  Upload…, AI provider, Decisions required, Confirm and build event log, etc.).
- Incident note: a PowerShell-piped replace script mangled unicode and corrupted every
  .tsx ("?"->"H"); recovered via git checkout and re-applied edits through safe paths
  (Write-tool script / Edit tool). Lesson recorded: never pipe unicode source edits
  through the PS console.
- 61 tests green; browser-verified (Data, Mapping transformation detail, Dashboard).

## 2026-07-06 (later) - Explorer zoom/pan + DiagramIQ editor & Signavio-style repository

- Process & variant explorer: zoom controls (50%-300%, Fit) and drag-to-pan canvas,
  mirroring the data-model ERD interaction (VariantExplorer.tsx, ProcessMap.tsx).
- DiagramIQ process repository (api/diagram.py): governed model store with the Signavio
  L1-L4 hierarchy (landscape / group / end-to-end / detailed), parent links enforced to
  point at a numerically higher level, per-item versions (author + revision comment),
  statuses draft -> in_review -> approved/rejected, per-element custom attributes stored
  with each version, and a full audit trail. Conversions (Excel-to-BPMN, Visio, uplift)
  auto-register as L4 drafts owned by "DiagramIQ agent".
- BPMN editor (BpmnEditor.tsx): bpmn-js Modeler embedded in the studio - draw/edit
  elements via the palette, select an element to edit its name, documentation, and
  custom attributes (System, RACI, risks, KPIs...), save as a new version, submit for
  approval, approve/reject with a signed decision comment; post-approval edits drop the
  model back to draft for re-approval.
- Studio page: repository table (level/status badges, version, owner, edit/view/
  validate/uplift/download) + inline "New diagram" form (name, L1-L4, author).
- Gotcha fixed: Pydantic models nested inside make_router were unresolvable under
  `from __future__ import annotations` (FastAPI treated the body as a query param ->
  422); hoisted to module level.
- 63 tests green (2 new in test_diagram_repo.py). Browser-verified end-to-end on the
  persistent server: created "Order to Cash" (L3), renamed the start event, attached
  System=SAP SD, saved v2, submitted to Maria, approved -> APPROVED badge + audit.
- Follow-up: artifacts converted before the repository existed showed no edit action.
  Added POST /api/diagram/repo/adopt (idempotent; brings a stored BPMN under governance
  as an L4 draft) and an "edit" action on every BPMN artifact row that adopts + opens
  the editor. Test added (adopt, idempotency, 404 on missing). 64 tests green.

## 2026-07-06 (later still) - Explorer folders + folder-level access provisioning

- Process explorer (DiagramIQ): Signavio-style folder tree with breadcrumbs, create/
  delete folders (delete blocked while non-empty), move diagrams between folders,
  conversions/adoptions land in the folder currently open.
- Workspace members with roles admin/editor/viewer (first member must be the admin;
  only admins manage members; the last admin cannot be removed).
- Folder access provisioning: an admin grants view/edit/manage per member per folder;
  restriction inherits down the tree (nearest restricted ancestor wins); unrestricted
  folders fall back to the member role (editor->edit, viewer->view); admins always
  have full access. Restricted folders and their diagrams are filtered out of the
  listing for anyone without a grant, and every write (create, version, submit,
  decide, move, folder ops) is enforced server-side with 403s.
- "Working as" identity in the studio header (persisted in localStorage) drives both
  listing scope and the signature on write operations. v1 posture: identity is
  honor-based (no login yet); flagged for the pre-deploy hardening pass alongside key
  hashing.
- Internal store files (repo/folders/members.json) no longer clutter the artifacts list.
- Also fixed: rejected decisions were audited as "rejectd"; audit now records
  approved/rejected.
- 66 tests green (2 new: folder tree/placement/move/delete, member bootstrap + ACL
  enforcement incl. 403s and listing filtration). Browser-verified: Finance folder
  created and RESTRICTED (Maria=edit), Order to Cash moved inside, Vik (viewer, no
  grant) sees neither the folder nor the diagram, Maria sees it but has no admin
  "access" action.

## 2026-07-06 (evening) - Workspace members w/ email+licenses; AI provider tile in Data Load

- Members promoted to a workspace-level directory (api/members.py) shared by BOTH
  offerings, reachable from a "Members" item in each sidebar. Member = name + email
  (validated, unique, lowercased) + role (admin/editor/viewer) + licenses
  (processiq/diagramiq). Rules: first member must be an admin (bootstrap); afterwards
  only admins add/update/remove; names immutable (folder grants key on them); email
  unique + format-checked; last admin can be neither removed nor demoted; every
  mutation audited (capped trail, shown on the page). Legacy DiagramIQ members.json
  auto-migrates on first read (verified live: 3 members moved with roles intact).
- License gating: a member without the diagramiq license gets NO access to the
  process repository (listing empty, writes 403) even if folders are unrestricted;
  admins bypass. ProcessIQ license shown for seat management (no per-user identity in
  ProcessIQ endpoints yet -> not enforced there; noted for the auth hardening pass).
- DiagramIQ studio member panel is now read-only (management moved to the Members
  page); folder access dialog unchanged and still keyed by member name.
- Data Load canvas gained an "AI Model" square tile (like Schedule/Data Jobs) in the
  integration lane: shows provider + model + credential source, click opens an editor
  with provider select (Anthropic API / Google Vertex AI / AWS Bedrock), model input
  with suggestions, and provider-specific credentials. Backend api/ai.py stores
  per-workspace ai.json (secrets never echoed back - has_* booleans only; None keeps
  stored secret, empty string clears). modelling/llm.py gained client_for_settings /
  llm_for_settings - one AnthropicLLM class over anthropic.Anthropic, AnthropicVertex,
  AnthropicBedrock (same Messages API); SDK-extra availability is probed and surfaced.
  workspace_llm(ws) now feeds mapping draft, copilot, and Ask (env key remains the
  fallback). The LLM stays curator-only per CLAUDE.md 1.1 - stated on the tile.
- Incident avoided: a PowerShell -replace pipeline mangled unicode in main.py again
  (BOM + em-dashes); caught immediately via git diff, reverted, re-applied with the
  Edit tool. The no-PS-text-pipeline rule now applies to ALL source edits, not just
  bulk ones.
- 72 tests green (6 new: member bootstrap/validation/admin-only/last-admin/license
  gating/migration; AI settings roundtrip/masking/factory guards). Browser-verified:
  Members in both sidebars, migration banner, admin add "Priya" (email, ProcessIQ-only
  seat), editor sees read-only page; AI Model tile selected shows Bedrock fields.

## 2026-07-07 - Dictionary (Signavio), credential-first connectors, KPI builder

- DiagramIQ Dictionary (api/dictionary.py + Dictionary page + editor integration),
  replicated from the live Signavio glossary the user opened for reference:
  category tree with subcategories, colored dots and counts (Org Units > Roles/
  Departments/External participants, Documents, Activities, Events, IT Systems,
  Risks, Controls, Others + custom categories), A-Z letter bar, search, New/Edit/
  Delete, Merge Dictionary Entries (first selected survives, absorbs descriptions +
  attributes), Import Excel/CSV (header-row detection; title/name column required,
  description optional, other columns -> custom attributes; per-import duplicate
  mode: skip vs update) and Export Excel. In the BPMN editor the Name field now
  suggests dictionary entries matched to the element type (Task->Activities,
  Event->Events, Lane/Pool->Org Units, DataObject->Documents), shows the linked
  term's description, offers "apply its attributes", and un-known names get an
  "Add to dictionary" shortcut.
- Credential-first connectors: every catalog connector now carries a live block
  (kind + credential form + prefilled tables/entities) - SAP x4 via OData Gateway,
  Dynamics via OData, Salesforce x2, ServiceNow, Jira, Oracle EBS x2 via reporting-
  replica DSN, Coupa via export URL. The Data page connector panel now leads with
  "Connect your instance" (URL + credentials + editable entity list + save-as-
  connection + scheduled re-extract) and demotes CSV templates to a collapsed
  fallback, mirroring the Celonis instance+credentials flow.
- Dashboard KPI builder (New KPI button): metric picker (case count, cycle time
  with agg + optional from/to milestones, hand-off wait, share of cases w/ rework
  or containing an activity, attribute sum/mean/median), breakdown (none, by
  attribute, over time per week/month), representation constrained per breakdown
  (KPI card; Bar/Table; Line/Column) - the taxonomy lifted from the Celonis Studio
  components panel (KPI card / category charts / time-series charts / tables).
  mining/kpis.py gained compute_kpi_series (top-12 categories ranked by volume,
  chronological trend buckets, dropped-category note); /api/kpis/options + /preview
  endpoints; saved chart KPIs render as SVG bar/column/line/table cards on the
  dashboard and respect global filters.
- 78 tests green (6 new: dictionary lifecycle/merge/import/export, kpi series +
  builder endpoints + connector live metadata). Browser-verified: SAP P2P
  "Connect your instance" with prefilled entity sets; Dictionary entry created in
  Activities; KPI builder previewed and saved "Mean cycle time by customer" as a
  bar chart on the dashboard.

## 2026-07-07 (later) - GenQ rebrand + co-pilot visibility

- Co-pilot: was only docked on Data / Data load / Dashboard, so it was absent on
  Mapping review (and the newer pages). Added 'mapping' to COPILOT_TABS - the dock
  now appears across the ProcessIQ analytical flow (Data, Mapping review, Data load,
  Dashboard). Kept off Ask (already a full-page chat) and Members (admin). Verified
  live on Mapping review (dock present, 320px, main .with-copilot).
- Rebrand: the whole tool is now GenQ, caption "Gentic Mining". ProcessIQ and
  DiagramIQ remain the two apps within GenQ. Changed: browser <title>, FastAPI app
  title, sidebar wordmark (GenQ + "Gentic Mining" caption + active-app sub-label),
  Home hero, Workspace/create-workspace hero, README top. Left ProcessIQ/DiagramIQ
  as offering names (license labels, offering cards, ProcessIQ integration lane) and
  internal script/docstring references untouched. Caption used verbatim as the user
  wrote it ("Gentic Mining").
- 78 tests still green; UI rebuilt; server restarted.

## 2026-07-07 (later) - Data Load box detail as modal popup

- Clicking a Data Load canvas box (Schedule, Data Jobs, Data Storage, AI Model,
  Objects & Events, Connections) now opens its detail as a centered modal popup over
  a dimmed backdrop instead of rendering inline below the canvas. Close via the x
  button, backdrop click, or Escape. Both detail code paths (ElementDetail and the AI
  model provider panel) share the .dl-modal-backdrop / .dl-modal shell; nested .card
  chrome is stripped inside the modal. Co-pilot "Asking about: <box>" scoping is
  preserved (still driven by the same selected state). Removed the old
  scroll-into-view + detailRef. Browser-verified: Data Jobs and AI Model open as
  popups; x/backdrop/Esc all dismiss.

## 2026-07-07 (later) - Co-pilot history, DiagramIQ model report, dictionary where-used

- Co-pilot: conversation now persists per workspace in localStorage, so past requests
  (and the KPIs you asked for) survive navigation and stay browsable by scrolling. Added
  a "New chat" control in the header to clear/start over (the "back" the user wanted).
  Applies on every co-pilot page (Data, Mapping, Data load, Dashboard). Browsing your
  prior KPI inputs = scroll the persisted chat; saved KPIs also remain as dashboard tiles.
- DiagramIQ Model Reporting (Signavio Model Reporting equivalent): GET
  /api/diagram/repo/report aggregates the repository - totals, by level, by status,
  approval rate, total revisions, by owner, created-over-time, pending approvals,
  recently approved (with approver+timestamp from the audit trail), and recent activity.
  New "Reports" page in the DiagramIQ sidebar with KPI tiles + tables + CSV export.
  Route declared before /repo/{item_id} so "report" is not read as an id.
- Dictionary "Show usages" / where-used (the defining Signavio dictionary feature, the
  main part of the ~20% gap): scans every repository model's latest BPMN for elements
  whose name matches a dictionary term (the same title-match the editor uses). Listing
  now carries usage_count per entry; GET /entries/{id}/usages returns the referencing
  models + element types. Dictionary page shows "used in N models", expands to the model
  list (name/level/status + element type: name), and adds a Refresh button. Element tags
  are prettified (participant->Pool, startEvent->Start event, task->Task, ...).
- Deferred (lower-value Signavio glossary bits, noted not built): entry translations
  (globe), category subscribe-to-updates, "Export Job Profile Report".
- 81 tests green (5 new: model report counts/funnel + empty; dictionary usages count +
  detail + zero + case-insensitive). Browser-verified all three.

## 2026-07-07 (later) - DiagramIQ workflow automation (Signavio Process Governance)

- New "Automation" area in DiagramIQ: build editable, executable workflows and run
  them as cases - the Process Governance equivalent.
- Backend api/workflow.py: workflow definitions with a trigger (manual / start form),
  roles, and ordered steps of type task (user form) / approval (approve-reject routing)
  / notification / automation / condition (branch on case variables) / end. CRUD +
  activate; POST /from-model/{id} generates a skeleton (one user-task step per BPMN
  activity, wired in order); runnable cases: start (captures trigger variables),
  complete-step (merges form data, approval decisions route, auto steps run through a
  small engine until the next human step or the end), full history. Bulletproof
  validation: unique step ids, every routing target references a real step or 'end',
  approvals need both branches, only active workflows start cases, editing drops to
  draft, a workflow with running cases can't be deleted.
- UI ui/src/pages/WorkflowAutomation.tsx: workflow list (status/steps/cases),
  New/Generate-from-model, a full step editor (name, type, assignee, per-type config:
  task form-field builder, approval approve/reject targets, next-step selects,
  condition builder with var/op/value branches + default), reorder/add/remove, start
  step + trigger form editor, Save/Activate/Delete, a run panel (start a case with the
  trigger form) and a case runner (current step + form, Approve/Reject or Complete,
  live history + data). Fixed: models dropdown must pass ?user= so folder access
  control does not hide the repository list.
- 85 tests green (4 new: authoring/validation/activation, run-through-approval,
  reject-path + condition branch, from-model + delete guard + edit-to-draft).
  Browser-verified: generated a 17-step workflow from "Ecommerce Flow Visio",
  activated it, started a case, completed "Search Items" -> engine advanced to
  "Review Recommended items" with history logged.

## 2026-07-07 (later) - Mapping: "Start a new version" action on confirmed specs

- The confirmed-mapping banner said "re-draft to start a new version" but had no
  control. Added it: MappingSpec.reopen() unlocks a confirmed spec back to draft,
  bumps the version, and clears reviewer/confirmed_at while KEEPING all content
  (activities, SQL edits, attributes, answered questions) as the new version's
  starting point. The previously confirmed version stays live (dashboards keep
  running on the materialized log) until the new one is re-confirmed. New endpoint
  POST /api/mapping/reopen (409 if not confirmed). Mapping review banner now shows
  version + two buttons: "Start a new version" (reopen, keep content) and "Re-draft
  from agent" (regenerate). 85 tests green (journey test extended: confirm -> reopen
  bumps + keeps content -> re-confirm -> reopen-draft refused 409). Browser-verified
  on Northwind O2C: v2 confirmed -> Start a new version -> v3 draft editable ->
  re-confirm -> v3 confirmed (397 events).

## 2026-07-07 (later still) - Chaotic Ford Motors P2P sample dataset

- New evaluation dataset for stress-testing the mining/insight engines against a real
  enterprise mess, not a tidy textbook process: fixtures/generate_ford_p2p_fixtures.py
  (seed 4022) generates 360 purchase orders for a fictional "Ford Motors" across 7
  vendor categories, each with its own speed/rework/exception fingerprint -
  steel_aluminum (fast, ERS self-billing), semiconductor (chip-shortage delays,
  disputes, duplicates, maverick buys), tooling_dies (custom-fab rework cycles),
  tier1_interior (quality rejections -> re-receipt), powertrain, logistics_freight
  (invoice-before-goods-receipt violations), mro_facilities. Layered profiles: multi-
  cycle PO change/re-approval, 1-4 partial goods receipts, price-dispute credit-memo/
  reissue chains, duplicate invoices caught before payment, split payment runs, and
  ~5% stuck/cancelled POs. Same SAP table shapes as fixtures/sap/ (EKKO, EKPO, LFA1,
  CDHDR, MSEG, RBKP, RSEG, BKPF) so the existing P2P priors draft it with zero code
  changes - only api/main.py gained a third "ford_p2p" dataset entry and Data.tsx a
  third demo button. Did NOT touch fixtures/generate_fixtures.py or fixtures/sap/
  (byte-identical, load-bearing for the Phase 0/2 equivalence tests).
- Fixed a real bug while designing it: an "ERS = no invoice at all" path would have
  broken the existing priors' Payment Sent join (BKPF.AWKEY -> RSEG.BELNR requires an
  invoice/RSEG row to exist for every case). Modeled ERS instead as a near-instant,
  system-generated invoice right off the goods receipt (minutes, not days) - still
  authentic self-billing behavior, and it keeps the mined chain valid.
- 87 tests green (2 new in test_ford_p2p_demo.py: stage/draft/materialize shape check,
  and a "genuinely chaotic" assertion - >=30 variants, top variant <25% share, rework
  share >20%, worst hand-off >24h, and both "Biggest bottleneck" and "Vendor with the
  slowest cycle" insights present).
- Browser-verified end-to-end: staged 360/360, confirmed the mapping (header
  granularity), materialized 360 cases / 2565 events. Dashboard: 63.1d median cycle
  time, only 19% conformant, 65% cases with rework, 67 distinct variants (top variant
  only 19% share - no dominant path). Insights: "Biggest bottleneck" Invoice Received
  -> Payment Sent averaging 40.5 days (64% of median cycle time); "Vendor with the
  slowest cycle" Bosch Electronics NA at 161.1 days median (5.7x Grainger Industrial
  Supply's 28.4 days); "Rework concentrates on one vendor" Yanfeng Interior Systems
  (94% of its 18 cases); "Cost of rework" 2.3x longer; "Deviations are slower" 75.4
  vs 38.9 days. Process & Variant Explorer at Fit zoom shows genuine spaghetti - dense
  criss-crossing dashed rework/back-edges between PO Approved, Goods Receipt, Invoice
  Received, and PO Changed, exactly the "many bottlenecks to find" the user asked for.

## 2026-10-28 - "Improve" layer: Advisor, Signals, and the SME Interviewer

Competitive input: reviewed Klarity (klarity.ai, product page titled "Within"), whose
Discover/Structure/Improve platform was raised as a benchmark. Deliberate conclusion
recorded here so it does not get re-litigated: Klarity reads a DIFFERENT SUBSTRATE
(screen recording, SME interviews, documents) than we do (systems of record). Neither
can produce the other's output, so this is not a parity exercise. We took the three
ideas that survive the translation to an event-log product and refused the fourth.

TAKEN:
- mining/advisor.py - ranked, costed improvement opportunities over numbers L4 already
  computes (worst hand-off, rework concentration, conformance penalty, variant sprawl).
  Two rules make it honest: labour value and cycle-time value are never merged into one
  figure (recovering 10 days of calendar time != saving 10 days of work), and every
  opportunity carries `based_on` + `evidence` so a sceptic can re-derive it by hand.
  Assumptions (hourly rate, hours per manual touch) are caller-supplied defaults, echoed
  back on every response and editable in the UI - they are assumptions, not measurements.
- mining/signals.py - drift detection against a human-captured baseline snapshot. Fires
  only above a 10% threshold (a noisy monitor gets muted, and a muted monitor is worse
  than none); knows which direction is bad per metric, so a conformance DROP is `warn`
  while a rise is `info`. Structural drift (an activity appeared/vanished) is reported
  separately and points the reviewer at the MAPPING, not the process - per S1.1 that is
  the silent-semantic-error case. Baseline stored per workspace, attributable.
- modelling/interviewer.py - the S4 confirmation step as a short interview instead of a
  radio-button form. A form cannot hold "it depends - for services POs we...", so that
  reasoning got flattened into a wrong answer. THE INTERVIEWER NEVER WRITES THE ANSWER:
  it returns a proposal, the human presses "Use this answer", and the existing
  /api/mapping/answers gate still does the recording. A model answer outside the offered
  options is discarded rather than accepted (test_an_invented_option_is_refused).
  Degrades to the plain form when no LLM is configured.

REFUSED: Klarity's "AI Companion" (continuous screen recording). Desktop agent on every
employee machine, consent/works-council exposure, large build, and it contradicts the
mid-market "minimal rollout" thesis. Logged so it is not revisited casually.

UI/UX placement: new "Improve" step in the ProcessIQ nav, between Dashboard and Ask.
Dashboard answers "what is happening"; Improve answers "what do I do about it" and
"what changed since I last looked". Opportunities and Signals share one page rather
than two nav items - an opportunity and the drift that created it are one conversation.
The Interviewer is NOT a new page: it opens inline under the question it settles, on
Mapping review, where the reviewer is already blocked.

API: POST /api/mapping/interview, GET /api/analysis/advisor, GET /api/signals,
POST /api/signals/baseline. New storage helpers load/save_signal_baseline.

Tests: 17 new in tests/test_advisor_signals_interview.py, all green. The load-bearing
ones are the honesty assertions, not the arithmetic: labour/cycle never merged, every
opportunity cites its basis, signals silent below threshold, structural drift names the
mapping, and the interviewer refuses an invented option. Full suite: 102 passed.
Two PRE-EXISTING failures (test_connectors.py::test_sqlite_live_extraction_feeds_the_agent,
test_live_api.py::test_servicenow_jira_salesforce_odata) - verified by stashing all of
this work and re-running: they fail identically on a clean checkout. They need a DuckDB
SQLite extension this sandbox cannot fetch. NOT introduced here, still outstanding.

Open questions:
- The Advisor prices rework via hours_per_touch, which is a guess until an SME sets it.
  Worth capturing per-workspace rather than per-request.
- Signals compare against ONE baseline. A rolling window would catch slow drift that a
  single frozen point misses.

## 2026-11-05 - Voice in the SME interview (speak the question, dictate the answer)

The interview landed and works, but reading a dense modelling question off a screen
and typing a considered answer is the slowest possible way for a busy SME to give us
the one thing we actually need. Added speech both directions. Voice is ADDITIVE: the
typed interview is unchanged and fully usable on its own, which matters because
recognition support is Chromium/Safari only.

- ui/src/lib/voice.ts - thin wrapper over the Web Speech API. Chosen over Polly/
  Transcribe deliberately: no backend route, no audio leaves the browser, no
  per-minute cost, nothing to configure per deployment. Every entry point is
  capability-checked (canSpeak/canListen) so an unsupported browser silently gets
  the text-only panel rather than a broken button.
- Output: each new agent turn is read aloud once (tracked by index in a ref, so a
  re-render cannot re-speak it). Mute toggle persisted in localStorage; a Repeat
  control and a per-message replay button on hover. Rate 0.98 - slightly under
  default because these questions are dense and unfamiliar.
- Input: continuous recognition with interim results shown live in the same text
  box, so the SME watches the words land and can correct by typing. Final phrases
  append rather than replace, so someone can think mid-sentence without losing what
  they already said.
- Two feedback traps handled explicitly: starting the mic cancels any speech (or
  the mic transcribes our own voice), and unmounting the panel stops both.
- Error text is human, not an error code: a refused mic permission says to allow it
  in the address bar; "no-speech" invites another try; a user-initiated abort is
  not surfaced at all.
- prefers-reduced-motion disables the listening pulse.

No Python touched. UI build clean; suite still 106 passed with the same two
PRE-EXISTING failures (test_connectors, test_live_api - DuckDB SQLite extension the
sandbox cannot fetch).

Open question: recognition language is hard-coded en-GB. Fine for the current
deployment, wrong the moment a non-English SME uses it - worth taking from the
browser locale or a workspace setting.
