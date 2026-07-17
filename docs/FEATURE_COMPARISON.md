# Feature comparison — Celonis Process Mining / SAP Signavio Process Intelligence / ProcessIQ

Scope rule (CLAUDE.md §1.2): ProcessIQ builds the **used subset**, not parity. The pledge that
matters for our segment: **no data engineer or data analyst required** — the modelling agent does
the data work; a business SME reviews and signs off; everything downstream is self-serve.

| Capability | Celonis | Signavio PI | ProcessIQ | Notes |
|---|---|---|---|---|
| Process discovery (DFG map) | ✅ | ✅ | ✅ | SVG map, frequency-scaled edges, rework arcs |
| Frequency / performance view toggle | ✅ | ✅ | ✅ | Performance mode: heat-colored edges, duration-first labels |
| Variant explorer (select variants → map updates) | ✅ | ✅ | ✅ | Checkbox list, share bars, coverage line |
| **Global analysis filters** (attribute, date, duration, activity, rework) | ✅ | ✅ | ✅ | Case-level filters applied server-side to every analysis; applied filters echoed back for traceability |
| Case explorer + single-case timeline | ✅ | ✅ | ✅ | Sortable table, rework/deviates flags, click-through timeline |
| Conformance checking | ✅ (model-based) | ✅ (BPMN to-be model) | ✅ | Token replay + exact-path share vs reference |
| SME-editable reference (to-be) process | partial | ✅ core strength | ✅ | Ordered activity list, SME sign-off required — no BPMN editor (deliberate) |
| Root cause / correlation insights | ✅ (ML miner) | ✅ (correlation miner) | ✅ lite | Fixed transparent comparisons (vendor, rework cost, deviation cost, bottleneck share) with the basis stated — no opaque ML |
| Throughput / bottleneck / rework KPIs | ✅ | ✅ | ✅ | |
| NL self-serve querying | ✅ (Process Copilot) | ✅ (AI features) | ✅ | Intent-routed, answers cite analysis + scope + filters |
| Agent-drafted event-log modelling w/ human confirm | ❌ (data engineers build transformations) | ❌ (analysts configure ETL) | ✅ **differentiator** | Priors + LLM refinement; blocking open questions; named-reviewer confirm |
| CSV / table upload ingestion | ✅ | ✅ | ✅ | |
| Certified live connectors (SAP, Oracle, ...) | ✅ huge catalog | ✅ SAP-native | ⏳ Phase 4 | Deliberately last (labor, not intelligence) |
| Multi-tenant SaaS | ✅ | ✅ | ✅ v1 | API-key workspaces; hashed keys before public launch |
| OCPM (object-centric) | ✅ | ➖ | ❌ out of scope | §0 |
| Action flows / automation / task mining | ✅ | ➖ | ❌ out of scope | §0 |
| BPMN process modeling & repository | ➖ | ✅ (Process Manager) | ❌ out of scope | We keep the reference as a plain activity sequence an SME can edit |
| ML forecasting / simulation | ✅ | ✅ | ❌ out of scope | Revisit only with demand |

## The staffing model (the point of the product)

| Task | Celonis / Signavio typical | ProcessIQ |
|---|---|---|
| Source-to-event-log transformation | Data engineer writes SQL/ETL | **Agent drafts; SME confirms in plain language** |
| Conformance target definition | Analyst/consultant models BPMN | **SME edits an ordered activity list** |
| Building analyses & dashboards | Analyst builds views | **Preset dashboard + filters + NL questions** |
| Root-cause investigation | Analyst runs miners | **Auto-insights with stated basis** |
