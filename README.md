# EAEDK — Embedded AI Engineering Development Kit

**A local-first firmware mentor and validation engine. Deterministic engines hold the truth; an
optional local LLM only explains.** It tells a zero-experience engineer what a board can do, what
to build first and in what order, catches architectural mistakes *before* they build them, and
refuses — with the math — anything the hardware cannot do.

> ## The LLM cannot assert a hardware fact. It can only reason from what the database has verified.

Every other AI coding tool will happily tell you the STM32F407 runs at 168 MHz, invent a DDR
timing, or guess a register address — confidently, and sometimes wrong. On embedded hardware a
wrong value destroys boards. **EAEDK makes that failure mode structurally impossible.** The local
model sits *outside* a deterministic truth boundary and may only **explain, triage, and draft**
over data the engines have already verified; anything it says that isn't backed by a citation is
stripped before you read it.

---

## Getting started

New here? Copy-paste these one at a time. By the last line you'll have a sequenced project roadmap.

```bash
git clone https://github.com/Ashut90/eaedk        # 1. download
cd eaedk                                            # 2. enter the folder
python3 -m venv .venv && source .venv/bin/activate  # 3-4. private workspace
pip install -e .                                    # 5. install the `eaedk` command
eaedk db init && eaedk db seed                      # 6-7. create + load the local database
eaedk board list                                    # 8. see the 14 built-in boards
eaedk board capabilities --board bluepill           # 9. what can this board do? (fuzzy names OK)
```

Typing `bluepill` instead of `STM32F103-BluePill` is fine — EAEDK auto-coerces and tells you.
Coming back later? `cd eaedk`, `source .venv/bin/activate`, and you're ready — steps 1–7 are
one-time only.

**Prefer a browser?** `pip install -e '.[web]'` then `eaedk web` → <http://localhost:8080>.
**On Ubuntu/Debian:** `packaging/build-deb.sh` → `sudo apt install ./dist/eaedk_*_all.deb` puts the
`eaedk` command on your `$PATH` system-wide. The full CLI needs only `python3` + `python3-yaml` and
works offline.

---

## The three questions a beginner actually asks

```bash
eaedk board capabilities --board bluepill           # "What can this board do?"
eaedk mentor --board bluepill --level 0             # "What should I build first, and why?"
eaedk roadmap --board bluepill --goal firmware-job  # "How do I get a job with this?"
```

- **`board capabilities`** — architecture in plain language (Cortex-M3 = no FPU, simple ISR model),
  flash/RAM in human terms (*"64KB — fits a UART logger and a sensor driver, not a TFLite model"*),
  confirmed peripherals, **what EAEDK can and cannot validate** (UNKNOWN facts listed, never
  assumed), and the closest board by architecture + memory bracket.
- **`mentor --level 0`** — the first project *with the reason it comes first*, what it teaches, the
  next three in **dependency order**, and for each: which peripherals it exercises, the failure mode
  to expect, and how to diagnose it. A project needing an unconfirmed peripheral is flagged, never
  recommended on a guess.
- **`roadmap`** — a dependency graph (not a flat list) where each step states *what it proves to an
  interviewer*; pass several `--board` flags and it sequences across them (fundamentals on the
  smaller board, RTOS/HAL on the larger).

---

## Architectural flow

Two front doors call the same deterministic engine core; every fact flows through `repo.py` into
local SQLite. The LLM sits **outside** the trust boundary and reaches you only through an
Actor → Critic → **deterministic Arbiter** → Post-Filter pipeline.

```mermaid
flowchart TB
    user([Engineer / Beginner])
    CLI["CLI — eaedk …"]:::door
    WEB["Web UI — :8080"]:::door
    user --> CLI
    user --> WEB
    CLI --> ORCH
    WEB --> ORCH
    ORCH["Orchestrator<br/>deterministic-first assembly · one unified verdict"]:::core

    subgraph TRUTH["DETERMINISTIC TRUTH BOUNDARY — offline, local SQLite"]
        direction TB
        VAL["Validation Engine — GUARDRAIL<br/>23 pure rules · PASS / FAIL / UNKNOWN"]:::guard
        RISK["Risk Engine<br/>10 hazard rules · sandboxed mini-DSL"]:::core
        SEM["Semantic Intent<br/>cost table + peripheral prerequisites"]:::core
        TOOL["Toolchain Engine<br/>build-environment as a first-class check"]:::core
        SIG[("Log Signature DB")]:::db
        MENT["Beginner Mentor<br/>capabilities · recommendation · roadmap"]:::core
        DB[("Truth DB<br/>facts · boards · citations / provenance")]:::db
    end
    ORCH --> VAL
    ORCH --> RISK
    ORCH --> SEM
    ORCH --> TOOL
    ORCH --> MENT
    VAL --> DB
    RISK --> DB
    SEM --> DB
    TOOL --> DB
    MENT --> DB

    subgraph OUT["LLM — OUTSIDE the boundary · explains only"]
        direction LR
        ACT["Actor<br/>propose"]:::llm --> CRIT["Critic<br/>self-review"]:::llm --> ARB["Arbiter — GUARDRAIL<br/>deterministic · final say"]:::guard --> PF["Post-Filter — GUARDRAIL<br/>strip uncited hardware numbers"]:::guard
    end
    ORCH -->|grounding context| ACT
    DB -.->|cited allowlist| PF
    TRUTH -.->|feasibility + cost verdict| ARB
    PF -->|filtered, cited prose| user
    ARB -->|hard fail → discard LLM text| user

    classDef door fill:#e3f2fd,stroke:#1565c0,color:#0d3b66;
    classDef core fill:#e8f5e9,stroke:#2e7d32,color:#1b4d2e;
    classDef db fill:#e8f5e9,stroke:#2e7d32,color:#1b4d2e;
    classDef guard fill:#fff3e0,stroke:#e65100,stroke-width:3px,color:#7a3b00;
    classDef llm fill:#fce4ec,stroke:#c2185b,stroke-width:2px,stroke-dasharray:6 4,color:#7a1438;
```

Full walk-through: **[docs/architecture.md](docs/architecture.md)** (includes *"What the Post-Filter
Does and Does Not Do"*) and **[docs/architecture-flow.md](docs/architecture-flow.md)**.

---

## Validation logic — how a request becomes one verdict

`eaedk validate <project> --intent "…"` runs **structural** checks and **behavioural intent**
together, aggregating hardware failures and intent-feasibility into a single report. A hard failure
anywhere — a rule FAIL, a memory overflow, or a missing peripheral — makes the whole project
`NOT FEASIBLE`.

```mermaid
flowchart TD
    IN["Project inputs<br/>(+ optional --intent)"]:::in --> CTX["build_context<br/>board facts + inputs + goal defaults"]:::core

    CTX --> VAL{"Validation rules<br/>(23, pure)"}:::core
    VAL -->|any FAIL| FAIL["NOT FEASIBLE"]:::fail
    VAL -->|engaged UNKNOWN| BLK["BLOCKED — needs info<br/>(names the missing keys)"]:::warn
    VAL -->|pass| RISK

    CTX --> RISK{"Risk rules<br/>timing · power · flash · ISR stack"}:::core
    RISK -->|HIGH / MEDIUM| WARN["Hazards + quantified fixes<br/>e.g. duty-cycle ≤ 16%"]:::warn

    CTX --> SEM{"Semantic intent<br/>(mqtt, grpc, tflite, …)"}:::core
    SEM -->|min cost > flash/RAM| FAIL
    SEM -->|requires NIC the board lacks| FAIL
    SEM -->|fits + peripheral present| OK
    RISK --> OK["FEASIBLE"]:::ok
    WARN --> OK

    FAIL --> REP["Unified report<br/>one aggregated verdict + next step"]:::rep
    BLK --> REP
    OK --> REP

    classDef in fill:#ede7f6,stroke:#5e35b1,color:#311b92;
    classDef core fill:#e8f5e9,stroke:#2e7d32,color:#1b4d2e;
    classDef ok fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b4d2e;
    classDef warn fill:#fff8e1,stroke:#f9a825,color:#7a5b00;
    classDef fail fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#7a1414;
    classDef rep fill:#e1f5fe,stroke:#0277bd,color:#013a52;
```

---

## The guardrails the LLM cannot bypass

1. **Validation Engine (input guard).** 23 pure-function rules return `PASS / FAIL / UNKNOWN` over
   typed board data — flash/RAM capacity, VTOR placement, partition fitment, DDR timing, power
   sequencing, pin-mux conflicts, secure-boot, **supply voltage vs the board minimum**, and more —
   plus a **Toolchain Engine** that makes the build environment first-class. `UNKNOWN` is a hard
   blocker, not a soft pass. Infeasible designs never get recommended.
2. **Semantic Intent + behavioural hazards.** A curated, citation-backed cost table turns *"I want
   gRPC / TLS / an AI gesture model"* into concrete flash/RAM ranges, checks them against the board,
   **and verifies the board even has the required peripheral** (a network protocol on a board with
   no NIC is a hard FAIL regardless of memory). Ten data-driven risk rules cover flash endurance,
   ISR timing deadlines, power budget, and ISR-context stack — invisible to a pure capacity checker.
3. **Actor-Critic-Arbiter + Post-Filter (output guard).** The LLM proposes (Actor) and reviews
   itself (Critic), then a **deterministic Arbiter** with the Validation Engine and cost table has
   the final say — it discards convincing-but-wrong answers ("you can run gRPC on a Blue Pill with
   optimization" → overridden, with the math). The Post-Filter then strips any hex/size/clock/timing
   not in the SQLite-cited allowlist.

See **[docs/architecture.md](docs/architecture.md)** for the trust-boundary read-through.

---

## The bring-up chain

A complete, auditable workflow — every step writes through one fact layer with structured
provenance, no raw SQL:

```
board add ─► project init ─► validate / risk ─► log analyze ─► risk resolve ─► export
(onboard)   (auto-template,   (deterministic    (signatures +   (close with    (real files
            assess @ min-0)    PASS/FAIL/UNK)     cited triage)   provenance)    when feasible)
```

The standout capability: **project-aware log triage** — a vague U-Boot hang with no smoking gun is
correlated against the project's *own* unverified gaps and triaged to a specific architectural
assumption, then written back as a tracked risk with zero manual correlation. The LLM proposes; the
deterministic layer decides and records.

## Watch the demo

[![asciicast](https://asciinema.org/a/w1gmp5g7DxaZMPnR.svg)](https://asciinema.org/a/w1gmp5g7DxaZMPnR)

The **complete chain** on an STM32F103, in one run: onboard the board → ingest its datasheet (cited
facts you confirm) → start a bare-metal project → check the build environment → validate
deterministically → export real build files → feed a **HardFault crash log** and watch EAEDK match
the fault and teach what to check — without ever guessing a hardware value.

## Commands

```bash
# Beginner mentor layer
eaedk board capabilities --board <name>          # plain-language capabilities + what can/can't be validated
eaedk mentor --board <name> --level 0            # first project + next 3, in dependency order, with failure modes
eaedk roadmap --board <name> [--board <b2>] --goal firmware-job   # job-focused dependency graph, multi-board

# Validation (structural + behavioural intent, one report)
eaedk validate <project>                         # cited PASS/FAIL/UNKNOWN + Facts/Assumptions/Unknowns
eaedk validate <project> --intent "mqtt freertos"  # ALSO costs the intent + checks peripherals — one verdict
eaedk validate --board <name> --intent "grpc tls"  # quick stand-alone intent feasibility (shows the math)

# Build environment, onboarding, triage, export
eaedk toolchain detect | validate --project <p>  # inventory + cross-check tools vs board arch + goal
eaedk board add --interactive                    # guided onboarding: live fitment + VTOR checks + cited facts
eaedk ingest --file ds.pdf --board <b> [--review]  # cited fact candidates from a datasheet PDF (human-in-the-loop)
eaedk project init                               # guided: name, board, goal -> auto-template + immediate assess
eaedk log analyze --file <log> --project <p> --project-aware --llm
eaedk risk show <p> | risk resolve <id> --note "…"
eaedk export <project> [--out DIR]               # checklist + CMake scaffold + flash steps as real files
eaedk mentor --board <name> --explain HardFault  # explain a concept (anchor offline; LLM opt-in)
```

Goal types / templates: `bare_metal_app` (the beginner's first project), `bootloader`, `uboot`,
`linux`, `ota`, `driver`, plus custom (template-less) projects.

## Design decisions that matter

- **Local-first, offline-only.** One SQLite file; no per-token cost; works air-gapped. A small
  local model's quality ceiling is *fine* precisely because it isn't the source of truth.
- **Deterministic core, replaceable LLM.** Engines decide feasibility and risk; the LLM is a thin
  convenience layer the Arbiter can overrule at any time.
- **Honest about uncertainty.** Cost estimates are flagged `UNVERIFIED` until a human confirms them;
  a board with any UNKNOWN core field can never be marked HIGH confidence; unrecognised inputs are
  warned, not silently ignored; one unparseable rule degrades to UNKNOWN instead of crashing.

## Layout

```
core/eaedk/
  store/              SQLite + forward-only migrations (17)
  engines/
    validation/       23 pure-function rules (the trust core)
    risk/             10 data-driven hazard rules over a sandboxed mini-DSL (no eval())
    toolchain/        host detection + build-environment validation (with teach layer)
    ingest/  logs/    datasheet PDF -> cited facts; format detection, signature matching, triage
  semantic_cost.py    intent -> flash/RAM cost + peripheral prerequisites
  mentor_beginner.py  capabilities / recommendation / roadmap (the beginner mentor layer)
  arbiter.py          Actor-Critic-Arbiter — Validation Engine has the final say
  llm/                gateway (Ollama) + post-filter + constrained prompts
  orchestrator/       deterministic-first assembly of the fixed response schema (+ unified intent)
  repo.py             one place for DB access + record_fact() write-through
packages/             templates, 14 seed boards, risk rules, cost table, log signatures, eval cases
docs/                 architecture, truth-layer, mentor-framework, reasoning topics
```

## Status

**380 pytests green, eval 20/20.** Tags `v0.1.0` → **`v3.1.1`**.

The deterministic core (validation, risk, signatures, toolchain, semantic intent), a unified truth
layer, the offline LLM with post-filter and the Actor-Critic-Arbiter loop, project-aware triage, the
interactive onboarding chain, and a feasibility-gated output engine that exports real build
artifacts. Recent milestones:

| Tag | What shipped |
|---|---|
| `v2.7.0` | Trust-hardening: un-bypassable NOT-FEASIBLE guard, flash-endurance hazard + DSL grammar, validation key transparency |
| `v3.0.0` | **Beginner mentor layer** (capabilities / recommendation / roadmap), **semantic intent translation**, behavioural hazards (timing / power / ISR stack), Actor-Critic-Arbiter |
| `v3.1.0` | ML/AI intent costing, **unified `validate --intent` + project**, **peripheral prerequisites**, board-name auto-coercion, quantified mitigations |
| `v3.1.1` | Risk-engine resilience (a bad rule degrades, never 500s the assessment) |

```bash
PYTHONPATH=core python3 -m pytest -q        # 380 passed
eaedk eval run                              # PASSED 20/20
```

> Reason first. Build second. Verify always.
