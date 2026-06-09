# EAEDK — Architecture Review & Design Foundation

**Status:** Design phase — review for approval. No implementation until approved.
**Reviewer role:** Senior Software Architect / Embedded Systems Architect / AI Systems Architect / Product Designer
**Date:** 2026-06-10

---

## 0. Executive Summary

EAEDK is a **local-first structured engineering reasoning platform** for embedded
bring-up. Its value proposition is *trustworthiness under a strict truth hierarchy* —
it must never invent hardware facts.

The central finding of this review:

> **The trust comes from the deterministic engines, not the LLM.** The local LLM is
> the weakest, least reliable, most replaceable component, and it is weakest precisely
> on the data where errors are catastrophic. The architecture must therefore treat the
> LLM as a thin skin over deterministic engines and a cited knowledge base — not as the
> center of the data flow.

Everything below follows from that inversion.

Second finding: **datasheet/TRM ingestion is the single most underestimated effort in
the whole project** and must not be on the MVP critical path. Register maps and timing
tables are the hardest possible PDF-extraction problem, and a wrong extracted value is
worse than no value.

Third finding: bring-up is **stateful and multi-session**, but the current architecture
has no concept of persistent project state, decision logs, or checklist progress. This
is a missing pillar, not a feature.

---

## 1. Critique of the Current Architecture

### 1.1 What's strong
- The truth hierarchy is the right north star.
- Templates-as-mandatory-checklists is genuinely good engineering discipline.
- The risk engine and "facts vs assumptions" instinct are correct.
- Local-first is a defensible product wedge (privacy, air-gapped labs, no per-token cost).

### 1.2 Architectural weaknesses (challenged assumptions)

| # | Assumption in current design | Problem | Recommendation |
|---|---|---|---|
| A1 | Local quantized LLM is the core orchestrator | Smallest, least reliable component; hallucinates on register/timing data | Invert: deterministic engines are core; LLM is pluggable, thin |
| A2 | LLM is "offline / quantized" only | Hard embedded reasoning needs a strong model; local-only caps quality | **Pluggable LLM backend**: local (Ollama/llama.cpp) *and* cloud (a hosted model) behind one interface; offline is a *mode*, not a constraint |
| A3 | Linear pipeline (ingest → DB → template → LLM → output) | Bring-up is iterative & stateful, not a one-shot pipeline | Add a **Project/Session State** layer that all engines read/write |
| A4 | Datasheet PDF parser is one box | It's the hardest sub-system; tables/register maps need special handling + human verification | Make ingestion a **multi-stage pipeline with mandatory human confirmation** of extracted facts; keep off MVP critical path |
| A5 | "Feedback learning loop" implies model learning | You can't safely fine-tune local models on user data; risk of propagating wrong facts | Redirect feedback into **curated knowledge + retrieval**, never into model weights |
| A6 | No provenance/citation layer | Truth hierarchy is unenforceable without it | Every fact carries `(source, page/section, confidence, verified_by_human)` |
| A7 | No errata / compatibility data model | Toolchain/SDK/silicon errata are first-class bring-up blockers | Dedicated **errata + compatibility matrix** stores |
| A8 | No evaluation harness | You cannot know if advice is good or regressing | **Golden-case eval suite** from day one |
| A9 | No safety/liability framing | Wrong advice can destroy hardware | Confidence-gated output + explicit disclaimers + "verify against TRM" enforced in output schema |

### 1.3 Missing components (yours + mine)

You correctly identified: **Project Discovery Mode, Board Knowledge Engine, Validation
Engine, Confidence System, Facts/Assumptions framework, Repository Analysis Engine.**
All accepted and folded in below.

Additional gaps I'm adding:

1. **Provenance & Citation Service** — backbone of the truth hierarchy (A6).
2. **Project/Session State & Decision Log** — persistent, ADR-style record of choices,
   checklist progress, confirmed facts per project (A3).
3. **Human-in-the-loop Fact Verification UI/flow** — extracted facts are *candidates*
   until an engineer confirms (A4).
4. **Errata & Compatibility Matrix** — toolchain↔SDK↔silicon (A7).
5. **Log Signature Database** — deterministic known-error matching before any LLM triage.
6. **Template Authoring / Versioning** — templates as versioned data, not hardcoded.
7. **Evaluation & Regression Harness** — golden cases (A8).
8. **Knowledge Curation Pipeline** — where board/errata data comes from and how it's
   updated and trusted.
9. **Observability** — structured logs/metrics of EAEDK itself (which engine fired, LLM
   token/latency, validation outcomes).
10. **Packaging/Distribution** — local-first means shipping models + seed DBs + installer.

---

## 2. Deterministic Rule Engine vs LLM Reasoning

This split is the heart of the design. **Maximize deterministic coverage; minimize LLM
surface area.**

### Deterministic (rules / math / lookups — never the LLM)
- **Validation Engine**: flash capacity math, RAM/stack budget, partition layout overlap
  checks, load-address vs memory-map conflict checks, A/B partition sizing.
- **Board Profile lookup**: flash/RAM/boot modes/known SDKs from the Board DB.
- **Template selection & checklist state tracking**.
- **Risk rules**: data-driven rule set (`if estimated_size > flash * 0.9 → HIGH`).
- **Toolchain/SDK compatibility matrix** lookups and errata matches.
- **Log signature matching** (known panic/error patterns → known causes).
- **Provenance/citation resolution** and confidence aggregation.
- **RAG retrieval & ranking** (the retrieval itself; embeddings are ML but deterministic
  given inputs).

### LLM reasoning (guidance layer — always cited, always confidence-tagged)
- Natural-language understanding of the engineer's goal/intent.
- **Explaining** *why* (the "treat every bring-up as first time" mandate).
- Drafting the **architecture narrative** from validated inputs.
- **Log triage hypotheses** — only *after* deterministic signature matching, and must
  quote the exact log lines.
- Turning extracted datasheet text into **structured fact candidates** (then human-verified).
- **Project Discovery** suggestions (capabilities → project ideas).
- Code/scaffold generation (V1+), template-constrained.

### The rule
> The LLM may **explain, draft, retrieve, and suggest**. It may **never assert a hardware
> fact** that isn't backed by a citation from the knowledge base or engineer input. If
> the deterministic layer says UNKNOWN, the LLM must surface UNKNOWN — not fill the gap.

This is enforced structurally (output schema requires citations for factual claims), not
just by prompt wording.

---

## 3. High-Level Architecture (revised)

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENTS                                         │
│   CLI (Typer)      Desktop (Tauri + React)      Web UI (React)          │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │  (local HTTP / IPC)
┌────────────────────────────────▼───────────────────────────────────────┐
│                      EAEDK CORE (FastAPI, local)                         │
│                                                                          │
│  ┌────────────────┐   ┌──────────────────────────────────────────────┐  │
│  │ Session/Project │   │           ORCHESTRATOR                        │  │
│  │ State + Decision│◄──┤  Routes a request through deterministic       │  │
│  │ Log             │   │  engines FIRST, LLM LAST. Assembles cited     │  │
│  └────────────────┘   │  response in the fixed response schema.       │  │
│                        └───┬───────────┬───────────┬───────────┬───────┘  │
│   DETERMINISTIC ENGINES    │           │           │           │          │
│  ┌──────────┐ ┌──────────┐ │ ┌────────▼──┐ ┌──────▼─────┐ ┌───▼────────┐ │
│  │Board     │ │Validation│ │ │Template   │ │Risk Engine │ │Toolchain & │ │
│  │Knowledge │ │Engine    │ │ │Engine     │ │(rule set)  │ │Errata/Compat│ │
│  │Engine    │ │(cap/mem) │ │ │+ checklist│ │            │ │Matrix      │ │
│  └────┬─────┘ └────┬─────┘ │ └─────┬─────┘ └─────┬──────┘ └────┬───────┘ │
│       │            │       │       │             │             │         │
│  ┌────▼────────────▼───────▼───────▼─────────────▼─────────────▼──────┐  │
│  │   KNOWLEDGE / RETRIEVAL LAYER (with PROVENANCE on every fact)       │  │
│  │   SQLite (relational+facts)  +  Vector store (chunks+embeddings)    │  │
│  └────┬───────────────┬───────────────┬───────────────┬───────────────┘  │
│       │               │               │               │                  │
│  ┌────▼─────┐  ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼───────────┐      │
│  │Datasheet │  │ Log Analysis│  │ Repo       │  │ LLM Gateway       │      │
│  │Ingestion │  │ Engine      │  │ Analysis   │  │ (pluggable:       │      │
│  │Pipeline  │  │(sig DB+LLM) │  │ Engine     │  │  Ollama | cloud ) │      │
│  │(+human   │  │             │  │ (V2)       │  │  + Embeddings     │      │
│  │ verify)  │  │             │  │            │  │                   │      │
│  └──────────┘  └─────────────┘  └────────────┘  └───────────────────┘      │
│                                                                          │
│  Cross-cutting: Provenance/Citation svc · Confidence svc · Eval harness  │
│                 · Observability/logging                                   │
└──────────────────────────────────────────────────────────────────────────┘
```

**Key change vs your diagram:** the LLM is no longer in the center of the pipe. The
Orchestrator runs deterministic engines first, and only calls the LLM Gateway to explain
/ draft / triage over already-validated, cited data.

---

## 4. Component Architecture

### 4.1 Orchestrator
Stateless coordinator. For each request: load project state → run relevant deterministic
engines → gather facts+citations+confidence → (optionally) call LLM with a constrained
context → assemble the fixed response schema (Feasibility / Risk / Template / Plan /
Missing Info / Next Step). **Refuses to emit a factual claim without a citation.**

### 4.2 Board Knowledge Engine
Seeded DB of boards/SoCs (flash, RAM, boot modes, known SDKs, debuggers, examples,
limitations, errata refs). Lookup is deterministic. New boards added via curation
pipeline or per-project manual entry. Solves "every session starts from zero."

### 4.3 Validation Engine (the most important deterministic engine)
Pure functions returning `PASS / FAIL / UNKNOWN` + reason + inputs used:
- Flash capacity vs estimated image size (+ margin).
- RAM/stack budget vs available.
- Partition layout: no overlaps, fits storage, A/B symmetry, recovery present.
- Load-address vs memory-map conflict (e.g., kernel load addr vs DDR init region).
- Toolchain/SDK availability & arch match.
- Boot-flow consistency (bootloader → kernel → init addresses line up).
`FAIL` halts the workflow; `UNKNOWN` forces a "Missing Information" prompt.

### 4.4 Template Engine
Templates are **versioned data** (YAML/JSON), not code. Each item has id, text,
category, required-inputs, and links to validation rules. Tracks per-project checklist
state. Supports authoring new templates without redeploy.

### 4.5 Risk Engine
Data-driven rule set evaluated against project facts. Each rule: condition →
severity (HIGH/MED/LOW/UNKNOWN) + explanation + suggested mitigation + citation. LLM
may *explain* a risk but cannot *invent* one.

### 4.6 Toolchain & Errata / Compatibility Matrix
Stores toolchain versions, SDK versions, compatibility edges, and known errata
(silicon + SDK + tool). Deterministic lookups feed validation and risk.

### 4.7 Datasheet Ingestion Pipeline (multi-stage; human-in-the-loop)
```
PDF → layout/text extraction (PyMuPDF) → table extraction (pdfplumber/Camelot;
vision model fallback for register maps) → chunking + embeddings → store with
PROVENANCE (page, section, bbox) → CANDIDATE structured facts (registers, memory
map, clocks) → ENGINEER VERIFICATION → confirmed facts (verified_by_human=true)
```
No extracted register/timing value is treated as fact until confirmed. Unconfirmed =
`confidence: LOW`, surfaced as assumption.

### 4.8 Log Analysis Engine
Deterministic first: per-format parsers (U-Boot, kernel dmesg, panic/oops, crash dumps)
+ **Log Signature DB** (known pattern → known cause + fix). LLM triage only for unmatched
logs, and must quote exact lines. Honors "analyze logs before advising."

### 4.9 Repository Analysis Engine (V2)
Reads project source + git history + build logs; compares implementation against the
applicable template; flags missing modules/architectural drift. LLM-assisted, template-
grounded.

### 4.10 LLM Gateway (pluggable)
Single interface; backends: local (Ollama/llama.cpp) and cloud (a hosted model). Per-task model
selection (cheap/local for explanation, strong for hard reasoning). Enforces the
"citations required for facts" contract; strips/blocks uncited factual assertions.

### 4.11 Cross-cutting
Provenance/Citation service, Confidence aggregation, Eval harness (golden cases),
Observability (which engines fired, validation results, LLM latency/tokens).

---

## 5. Data Flow Diagrams

### 5.1 New project / goal request
```
User goal ──► Orchestrator ──► Board Knowledge Engine (lookup board)
                   │
                   ├─► Template Engine (select template, init checklist)
                   ├─► Validation Engine (capacity/memory/boot checks)  ──► PASS/FAIL/UNKNOWN
                   ├─► Risk Engine (rule eval over facts)
                   ▼
        Assemble facts + citations + confidence
                   │
                   ├─(if explanation/plan needed)─► LLM Gateway (constrained, cited)
                   ▼
        Response schema  ──►  persist to Project State + Decision Log
```

### 5.2 Datasheet ingestion
```
Upload PDF ─► extract text+layout ─► extract tables ─► chunk+embed ─► store w/ provenance
        └─► derive CANDIDATE facts ─► ENGINEER VERIFY ─► confirmed facts (HIGH confidence)
```

### 5.3 Log analysis
```
Upload log ─► detect format ─► parse ─► match Signature DB
   ├─ match  ─► known cause + fix (HIGH confidence, cited)
   └─ no match ─► LLM triage (quotes log lines, MED/LOW confidence) ─► suggest signature
```

---

## 6. Database Design (SQLite-centric, local-first)

Core tables (abridged; types omitted for brevity):

```
-- Knowledge: boards & hardware
socs(id, name, vendor, arch, notes)
boards(id, soc_id, name, flash_bytes, ram_bytes, ddr_type, ddr_bytes,
       primary_storage, boot_modes_json, source_id, confidence)
board_capabilities(id, board_id, capability, details_json)

-- Provenance (every fact points here)
sources(id, type[datasheet|trm|sdk_doc|errata|manual|web], title, uri, hash)
citations(id, source_id, page, section, bbox_json, snippet)

-- Extracted / confirmed hardware facts
facts(id, board_id, kind[register|memmap|clock|pinmux|timing], key, value,
      citation_id, confidence[HIGH|MED|LOW], verified_by_human, created_at)

-- Datasheet RAG
documents(id, source_id, n_pages)
doc_chunks(id, document_id, page, section, text, citation_id)   -- embeddings in vector store, keyed by chunk id

-- Toolchains / SDKs / errata
toolchains(id, name, version, arch, host_os)
sdks(id, name, version, requires_host_os)
compatibility(id, a_type, a_id, b_type, b_id, status[ok|broken|unknown], note, source_id)
errata(id, scope[silicon|sdk|tool], target_id, summary, workaround, source_id)

-- Templates (versioned data)
templates(id, key, name, version, goal_type, active)
template_items(id, template_id, item_key, text, category, required_inputs_json,
               validation_rule_keys_json)

-- Projects (the missing state pillar)
projects(id, name, board_id, goal_type, status, created_at, updated_at)
project_inputs(id, project_id, key, value, source[user|extracted], citation_id, confidence)
project_checklist(id, project_id, template_item_id, status[todo|done|na|blocked], note)
project_facts(id, project_id, fact_id, status[confirmed|assumed|unknown])
decisions(id, project_id, title, rationale, alternatives_json, made_at)   -- ADR-style
risks(id, project_id, rule_key, severity, explanation, mitigation, citation_id, status)

-- Logs
log_files(id, project_id, format, uri, hash)
log_signatures(id, format, pattern_regex, cause, fix, severity, source_id)
log_analyses(id, log_file_id, signature_id, llm_hypothesis, confidence, created_at)

-- Risk rules (data-driven)
risk_rules(id, key, goal_type, condition_dsl, severity, explanation_tmpl, mitigation_tmpl)

-- Feedback (→ curation, NOT model weights)
feedback(id, project_id, target_type, target_id, verdict[good|wrong|missing], note, created_at)

-- Eval
eval_cases(id, name, goal_type, inputs_json, expected_json)
eval_runs(id, case_id, passed, diff_json, run_at)
```

Vector store: `doc_chunks` embeddings live in **sqlite-vec** (single-file simplicity) or
**LanceDB** (better at scale). Recommendation in §8.

---

## 7. API Design (local HTTP, versioned `/v1`)

Resource-oriented; deterministic endpoints return citations + confidence inline.

```
Projects
  POST   /v1/projects                      create (board, goal)
  GET    /v1/projects/{id}                 full state (checklist, facts, risks, decisions)
  POST   /v1/projects/{id}/inputs          add/confirm an input
  POST   /v1/projects/{id}/decisions       record an ADR

Boards / Knowledge
  GET    /v1/boards?query=                 search board DB
  GET    /v1/boards/{id}                   profile + capabilities + citations
  POST   /v1/boards                        manual board entry (curation)

Templates
  GET    /v1/templates?goal_type=
  GET    /v1/projects/{id}/checklist
  PATCH  /v1/projects/{id}/checklist/{item} status update

Validation / Risk (deterministic; the trust core)
  POST   /v1/projects/{id}/validate        → [{check, status, reason, inputs_used}]
  POST   /v1/projects/{id}/risks/evaluate   → [{rule, severity, explanation, mitigation, citation}]

Datasheets
  POST   /v1/datasheets                     upload+ingest (async job)
  GET    /v1/datasheets/{id}/candidates     extracted fact candidates for verification
  POST   /v1/datasheets/{id}/candidates/{c}/confirm

Logs
  POST   /v1/logs/analyze                   upload+analyze → matches + hypotheses

Discovery
  POST   /v1/boards/{id}/discover           project suggestions (beginner→advanced + path)

Orchestrated reasoning (the only LLM-facing endpoint)
  POST   /v1/projects/{id}/ask              NL question → cited response in fixed schema

Repo (V2)
  POST   /v1/projects/{id}/repo/analyze
```

Every response carries: `confidence`, `citations[]`, and an `unknowns[]` array so the
"Facts / Assumptions / Unknown" contract is structural, not prose.

---

## 8. Technology Recommendations

> Format per item: **Pick** · Benefits · Risks · Alternatives.

**Backend / Core — Python 3.12 + FastAPI**
- Benefits: best ecosystem for ML/PDF/embedded tooling; async; great for local service.
- Risks: packaging a Python app for offline desktop is fiddly; perf for heavy parsing.
- Alternatives: Go/Rust (fast, easy single-binary distribution, weaker ML/PDF libs);
  Node/TypeScript (unifies with frontend, weaker ML/PDF).

**Frontend — React + TypeScript, shipped via Tauri (desktop); same code for Web UI**
- Benefits: Tauri = small footprint, Rust shell, true local-first desktop; reuse web UI.
- Risks: Tauri younger than Electron; Rust shell learning curve.
- Alternatives: Electron (heavier, mature); pure web (loses offline desktop story).

**CLI — Python Typer**
- Benefits: shares core code; scriptable for CI/labs.
- Risks: none significant. Alternatives: Go Cobra (if backend were Go).

**Relational DB — SQLite**
- Benefits: perfect local-first; single file; zero-admin; transactional.
- Risks: single-writer concurrency (fine for single-user desktop).
- Alternatives: DuckDB (analytics-leaning); Postgres (overkill, not local-first).

**Vector store — sqlite-vec (MVP) → LanceDB (scale)**
- Benefits (sqlite-vec): everything in one SQLite file; trivial backup/ship; no server.
- Risks: scaling limits on large datasheet corpora.
- Alternatives: LanceDB (embedded, columnar, scales better); Chroma (more moving parts);
  Qdrant (server — violates local-first simplicity).

**Local LLM runtime — Ollama (default) with llama.cpp underneath; pluggable cloud model**
- Benefits: Ollama = easy local model mgmt + embeddings; llama.cpp = control/quantization;
  A cloud backend for hard reasoning when online is allowed.
- Risks: local model quality ceiling on embedded reasoning; VRAM/RAM requirements.
- Alternatives: LM Studio (GUI-oriented); vLLM (server, heavier); direct HF transformers.

**Embeddings — local (nomic-embed-text / bge-m3 via Ollama)**
- Benefits: offline, fast, good retrieval quality.
- Risks: must match embedding model between index and query time (version pinning).

**Document processing — PyMuPDF + pdfplumber/Camelot; Docling or Marker for hard layout; vision model fallback for register maps**
- Benefits: PyMuPDF fast/accurate text+layout; pdfplumber/Camelot for tables; Docling/Marker
  good at complex datasheet structure.
- Risks: **register-map tables remain hard**; expect significant manual-verification volume.
- Alternatives: unstructured.io (general but heavier); commercial OCR (violates local-first).

**Log analysis — custom deterministic parsers + Signature DB; LLM triage fallback**
- Benefits: deterministic, explainable, fast; signatures grow over time.
- Risks: parser maintenance per log format.
- Alternatives: generic log frameworks (poor fit for U-Boot/kernel specifics).

---

## 9. Repository Structure (monorepo)

```
eaedk/
├─ docs/                      # this review + ADRs + design
├─ core/                      # Python package (the brain)
│  ├─ eaedk/
│  │  ├─ orchestrator/
│  │  ├─ engines/
│  │  │  ├─ board_knowledge/
│  │  │  ├─ validation/
│  │  │  ├─ templates/
│  │  │  ├─ risk/
│  │  │  ├─ toolchain_errata/
│  │  │  ├─ ingestion/        # datasheet pipeline
│  │  │  ├─ logs/             # parsers + signature matching
│  │  │  └─ repo_analysis/    # V2
│  │  ├─ knowledge/           # retrieval, provenance, confidence
│  │  ├─ llm/                 # gateway + provider adapters (ollama, cloud)
│  │  ├─ store/               # SQLite + vector store access
│  │  ├─ api/                 # FastAPI routers
│  │  └─ schemas/             # pydantic response/contract models
│  └─ tests/
├─ apps/
│  ├─ cli/                    # Typer
│  └─ desktop/                # Tauri shell
├─ web/                       # React + TS (shared by desktop + web)
├─ packages/
│  ├─ knowledge-seed/         # seed board/errata/template/signature data (YAML/JSON)
│  └─ templates/              # versioned template definitions
├─ eval/                      # golden cases + runner
├─ models/                    # local model configs / download manifests
└─ scripts/                   # packaging, db migrations, data import
```

---

## 10. Roadmap

### MVP — "Trustworthy structured assistant, LLM optional"
The MVP must be valuable **even with the LLM disabled.** No datasheet auto-ingestion on
the critical path; manual fact entry is fine.
- Project/Session state + decision log (the missing pillar).
- Board Knowledge Engine seeded with ~5–10 popular boards (STM32F4, ESP32, RPi-class,
  a couple of NXP/TI Linux SoCs).
- Template Engine + checklist tracking (your 5 existing templates as versioned data).
- **Validation Engine** (capacity, memory map, partition, boot-flow) — the trust core.
- Risk Engine (data-driven rules) + Confidence + Facts/Assumptions/Unknown output schema.
- Provenance/citation model (even for manually entered facts).
- CLI + minimal web UI. LLM = thin explanation layer (pluggable, off by default).
- Eval harness with first golden cases.

### V1 — "Knowledge + reasoning"
- Datasheet ingestion (text + RAG with citations) + human verification flow.
- Log Analysis Engine (U-Boot + kernel) with Signature DB.
- Toolchain/SDK compatibility matrix + errata.
- Pluggable LLM (local + cloud) fully wired; Project Discovery Mode.
- Desktop (Tauri) packaging.

### V2 — "Deep & adaptive"
- Register-map / timing **table extraction** (vision fallback) + verification.
- Repository Analysis Engine.
- Code/scaffold generation (template-constrained).
- Advanced templates (OTA A/B, driver dev) hardened.
- Feedback → **knowledge curation** loop (not model weights).

### Long-term vision
- Community/shared board + errata + signature databases (with provenance & trust tiers).
- Plugin ecosystem & template marketplace.
- CI integration + hardware-in-the-loop validation.
- Team/multi-user + sync (optional, breaks pure local-first → opt-in).

---

## 11. Prompt Gaps (folded into product, not just prose)

Your prompt additions are accepted. Note how each becomes a *system feature*, not just
instructions to the model — because relying on prompt text alone is itself a hallucination
risk:

| Prompt addition | Becomes (structural) |
|---|---|
| Project Discovery Mode | `/discover` endpoint over Board Capabilities |
| Board Knowledge Engine | Board DB + seed data + curation pipeline |
| Validation Engine | Deterministic engine returning PASS/FAIL/UNKNOWN; halts workflow |
| Confidence System | `confidence` field on every fact + aggregation service |
| Facts vs Assumptions | Required `facts[] / assumptions[] / unknowns[]` in response schema |
| Repository Intelligence | Repo Analysis Engine (V2) |

Additional prompt-level gaps to close: explicit **safety/liability disclaimer** + "verify
against TRM" enforced in output; **citation-required** contract; instruction that the LLM
must echo `UNKNOWN` from the deterministic layer rather than fill it.

---

## 12. Decisions (locked 2026-06-10)

| # | Decision | Choice | Consequence |
|---|---|---|---|
| D1 | LLM backend | **Strictly offline-only** | Local model only; no cloud fallback. See §12.1 — this *reinforces* the inverted architecture and shrinks the LLM's role further. |
| D2 | MVP client | **CLI-first** | Typer CLI is the MVP surface; desktop (Tauri) deferred to V1. Fastest path to validating the engines. |
| D3 | Board data | **Seed pack + user PDFs** | Ship curated seed DB for ~5–10 popular boards; users ingest their own datasheets for the rest. |
| D4 | Scope | **Solo local tool (for now)** — recommended | SQLite + no-auth, no sync. Revisit teams/sync long-term, opt-in only. Keeps MVP lean. |

### 12.1 Implications of offline-only (D1) — read carefully

Offline-only is a defensible privacy/air-gap choice, but it has hard consequences that
the design must respect:

1. **Quality ceiling is low and permanent.** A local quantized model will be weak at
   embedded reasoning. This is *consistent* with your truth hierarchy — it just makes the
   inverted architecture mandatory rather than optional. The deterministic engines carry
   the product; the LLM is now strictly a **convenience layer**.
2. **Shrink the LLM's allowed jobs** to: (a) explaining validated/cited results in prose,
   (b) drafting *candidate* fact extractions from datasheets (always human-verified),
   (c) triaging logs the Signature DB couldn't match (must quote lines, MED/LOW confidence
   only). It may **not** be on any path that emits an unverified hardware fact.
3. **Datasheet ingestion leans harder on humans.** No strong cloud model to extract
   register/timing tables → expect *more* manual verification volume. Budget for it in V1.
4. **Embeddings are local** (already planned: nomic-embed-text / bge) — pin the version.
5. **Hardware requirements become a product constraint.** Document min RAM/VRAM for the
   bundled model; the CLI should degrade gracefully (LLM features disabled) on weak machines
   — which is fine, because the MVP is valuable with the LLM off anyway.
6. **A2 in §1.2 is overridden** by this decision; the reasoning there stays on record in
   case the stance is revisited.

---

## 13. Status & Next Deliverable

Architecture review is **complete**; the four key product decisions are **locked** (§12).
The inverted architecture (deterministic core, thin offline LLM) and the LLM-optional,
CLI-first MVP are the approved foundation.

**Next deliverable (still no app code):** detailed **MVP specification** —
1. Concrete data models (the §6 tables refined to field-level, with migrations plan).
2. **Validation rule catalog** — the exact PASS/FAIL/UNKNOWN checks and their inputs.
3. **Template schema** — the YAML/JSON shape for your 5 existing templates as versioned data.
4. **CLI command surface** — the verbs an engineer types, mapped to engines.
5. First **golden eval cases**.

App code begins only after that MVP spec is signed off.
