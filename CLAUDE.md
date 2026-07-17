# CLAUDE.md — ProcessIQ (working name)

Persistent contract for every session. Update as decisions are made; never let it drift.
`ProcessIQ` is a placeholder name — swap it project-wide when branding is decided.

## 0. What we are building (and what we are NOT)

A lean, mid-market process-mining product that an agent can stand up with minimal
data-engineering effort. The agent drafts the extraction and the event-log model by reading the
customer's source schema and understanding their process; a human confirms the semantic choices
once per process; after that the business self-serves via natural language.

**Benchmark, not clone.** Celonis is the quality bar for outputs (variant analysis, conformance,
bottlenecks, self-serve querying). We do NOT rebuild Celonis's enterprise-scale in-memory engine,
its full connector catalog, OCPM, action-flow automation, or app ecosystem. Matching Celonis's
whole surface area is neither feasible nor necessary for mid-market and is explicitly OUT of scope.

**Target segment:** mid-market companies that will never afford Celonis economics. Load-bearing:
- Their event volumes fit an embedded columnar engine (no distributed engine needed).
- Their systems are more vanilla (less Z-table / bespoke-status customization), so the agent's
  domain priors hold far more often. The agentic-modelling bet is most feasible precisely here.

**Positioning constraint (do not violate in any copy, naming, or docs):** this is an accelerator
for accounts too small for Celonis, not a "Celonis replacement." The internal Genpact story is
complement/reach-extension, never displacement.

## 1. Non-negotiable design principles

These override convenience. If a task conflicts with these, stop and flag it.

1. **Silent semantic errors are the primary risk.** A wrong activity/event-log definition does NOT
   throw — it produces a confident, incorrect dashboard. Therefore:
   - The modelling agent must never ship an event-log mapping without surfacing its semantic
     choices in plain language for human confirmation ("I mapped Invoice Cleared to `RBKP` rows
     where … — confirm / correct").
   - Every generated mapping is stored as an auditable, versioned, human-readable spec — never as
     opaque code.
   - `agent-drafts → human-confirms-once-per-process → business-self-serves` is the core loop.
     Do not build a "fully autonomous, nobody checks" path. Ever.
2. **Scope discipline.** Build the used subset of Celonis: variant analysis, conformance checking,
   throughput/bottleneck/rework, a small set of standard processes (start with P2P), and NL
   self-serve query. Reject scope creep toward parity.
3. **Engine is infrastructure, not the moat.** Do not hand-roll a columnar engine. Use DuckDB
   (default) as the embedded analytical engine; leave a clean seam to swap to ClickHouse for
   larger tenants.
4. **Algorithms are off-the-shelf.** Use PM4Py for core mining (variants, conformance, DFG,
   performance). Wrap and productize; do not reimplement published algorithms.
5. **The AI is capable; the system is the hard part.** The frontier model is not the constraint.
   Effort goes to connectors, the confirmation UX, and correctness — not to "making the model
   smarter."
6. **Everything the agent decides is inspectable.** No black boxes in the data path. Mappings,
   generated SQL, and event-log definitions are all viewable and diff-able by a human.

## 2. Architecture (layers, bottom → top)

```
┌─────────────────────────────────────────────────────────────┐
│  L5  Self-serve interface  — NL question → PQL-equivalent →  │
│      analysis + narration + visualization                    │
├──────────────────────────────────────────────────────────────┤
│  L4  Analysis / mining     — PM4Py: variants, conformance,   │
│      bottleneck, rework, throughput                          │
├──────────────────────────────────────────────────────────────┤
│  L3  Event-log store       — DuckDB (swap seam → ClickHouse) │
├──────────────────────────────────────────────────────────────┤
│  L2  *** Agentic modelling layer ***  (THE CRUX — §4)        │
│      schema read → propose event-log design → surface        │
│      semantic choices → human confirms → materialize log     │
├──────────────────────────────────────────────────────────────┤
│  L1  Extraction runtime    — owned code: connectors, delta/  │
│      CDC loads, staging. Agent CONFIGURES; runtime is ours.  │
├──────────────────────────────────────────────────────────────┤
│  L0  Sources               — start: SAP (P2P tables), CSV/DB │
└──────────────────────────────────────────────────────────────┘
```

Build order is **L3 → L4 → L2 → L5 → L1** (see §5). L1 real connectors come last because they are
labor, not intelligence, and we can develop everything above them against sample/extracted data first.

## 3. Tech stack (decided — change only with a logged reason)

- **Language / runtime:** Python 3.11+ (backend, agent, mining). TypeScript + React for the UI later.
- **Analytical engine:** DuckDB (embedded). Abstraction seam so ClickHouse can back larger tenants.
- **Process mining:** PM4Py.
- **Agent / LLM:** Anthropic API (Claude). Design the modelling agent as tool-using; keep the model
  provider behind an interface so the reasoning model is swappable.
- **Orchestration:** plain Python + a thin agent loop first. Do NOT pull in a heavy agent framework
  in Phase 1 — earn the dependency.
- **Storage of specs:** event-log mapping specs as versioned YAML/JSON (human-readable, diff-able).
- **API:** FastAPI.
- **Tests:** pytest. Every generated-mapping code path needs a fixture-based test.

## 4. The agentic modelling layer (L2) — the piece that is uniquely ours

Prototype first and get right. Everything else is commodity.

**Contract.** Input: a connection to source tables (schema + sample rows) + a target process
("P2P"). Output: a proposed event-log mapping spec + a plain-language explanation of every semantic
choice, presented for one-time human confirmation. On confirm → materialize the event log in DuckDB.

**The spec object (versioned, human-readable).** For each process, produce a mapping like:

```yaml
process: procure_to_pay
case_id:
  source: EKKO.EBELN            # purchase order as the case notion (confirm!)
activities:
  - name: "PO Created"
    source_table: EKKO
    condition: "row exists"
    timestamp: EKKO.AEDAT
    rationale: "PO header creation date is the natural start event"
  - name: "Invoice Received"
    source_table: RBKP
    condition: "..."
    timestamp: RBKP.CPUDT
    rationale: "..."
    confidence: medium          # <-- flags choices most likely to be wrong
attributes: [...]
open_questions:
  - "Is a PO or a PO-line the correct case granularity for this customer?"
```

**Rules for the agent**
- State priors, then check them. Standard SAP P2P has known patterns — use them as the draft, but
  every table/condition/timestamp choice must be justified and flagged with a confidence level.
- Escalate ambiguity, never guess silently. Case granularity, status-code meanings, and any
  customization go into `open_questions` and block materialization until answered.
- Explain in business language, not SQL, at the confirmation step.
- Confidence surfacing is mandatory. Low/medium-confidence mappings are visually distinct so the
  human reviews them first.

**First milestone for L2.** Given a fixture SAP P2P schema + sample rows → agent emits a correct,
fully-explained mapping spec, with at least the case-granularity ambiguity correctly raised as an
open question. That single behavior is the proof-of-concept for the whole thesis.

## 5. Build phases (each phase = shippable, testable increment)

- **Phase 0 — Skeleton.** Repo, `CLAUDE.md`, `PROGRESS.md`, env, DuckDB up, load a synthetic P2P
  event log (hand-built CSV), run one PM4Py variant analysis end-to-end. Proves the L3→L4 spine.
- **Phase 1 — Mining core (L4).** Variant analysis, DFG, conformance vs. a reference model,
  bottleneck + rework + throughput metrics, over the synthetic log. Clean Python API. Tests on
  known fixtures.
- **Phase 2 — Agentic modelling (L2) [priority].** Build the mapping-spec object, the modelling
  agent, the confirmation loop, and materialization into DuckDB. Deliver the §4 first-milestone
  behavior. This is the differentiator — spend the most care here.
- **Phase 3 — Self-serve (L5).** NL question → generated query over the event log → analysis +
  narrated answer + basic visualization. Guardrail: answers cite which activities/filters were
  used (traceable, not magic).
- **Phase 4 — Extraction runtime (L1).** Real SAP P2P extraction (start with a single certified
  path: table reads / delta). Agent configures the extraction from schema; runtime is owned code.
  Heaviest labor — last.
- **Phase 5 — Hardening + packaging.** Multi-tenant seam, ClickHouse backing option, deployment,
  demo dataset.

Do not start a later phase until the earlier one is green and logged in `PROGRESS.md`.

## 6. Repo conventions

```
/processiq
  /engine        # DuckDB access, schema, materialization
  /mining        # PM4Py wrappers, metrics
  /modelling     # L2: agent, spec object, confirmation loop  <-- crown jewel
  /extraction    # L1: connectors, staging (later phases)
  /api           # FastAPI
  /ui            # React (later)
  /fixtures      # synthetic P2P schema + sample rows + reference logs
  /specs         # materialized mapping specs (versioned)
  CLAUDE.md
  PROGRESS.md
```

- `PROGRESS.md` is mandatory. At the end of every session append: what was built, decisions made,
  what's next, and any open question. Read it at the start of every session before doing anything.
- Log every stack/scope decision with a one-line reason. Silent drift from this file is a defect.
- Tests before moving on. No phase is "done" without passing fixture tests, especially for any
  agent-generated mapping or query.
- Small commits, one concern each.
