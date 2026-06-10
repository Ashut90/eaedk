# EAEDK — Embedded AI Engineering Development Kit

> ## The LLM cannot assert a hardware fact. It can only reason from what the database has verified.

Every other AI coding tool will happily tell you the STM32F407 runs at 168 MHz, invent a DDR
timing, or guess a register address — confidently, and sometimes wrong. On embedded hardware a
wrong value destroys boards. **EAEDK makes that failure mode structurally impossible.**

The local model never sees raw hardware truth and never gets to claim it. It sits *outside* a
deterministic truth boundary and may only **explain, triage, and draft** over data the engines
have already verified. Anything it says that isn't backed by a citation in the database is
stripped before you ever read it.

That inversion — deterministic engines hold the truth, the LLM is a thin, replaceable
convenience layer — is the whole product.

## The two guardrails

EAEDK is built around two deterministic gates the LLM cannot bypass:

1. **Validation Engine (input guard).** 18 pure-function rules return `PASS / FAIL / UNKNOWN`
   over typed board data — flash/RAM capacity, VTOR alignment (architecture-derived),
   partition fitment, DDR-timing-verified, power sequencing, pin-mux conflicts, and more —
   plus a **Toolchain Engine** that makes the build environment first-class: a host with no
   `arm-none-eabi-gcc` makes a Cortex-M project `NOT FEASIBLE`, with a one-line fix.
   `UNKNOWN` is a hard blocker, not a soft pass. Infeasible designs never get recommended.
2. **Post-Filter (output guard).** Every LLM response is scanned; any hex address, memory
   size, clock, or timing **not in the SQLite-cited allowlist** is removed and replaced with
   `[uncited claim removed — verify against TRM]`. Frequencies and timings are never in the
   DB, so the model can never sneak one through.

See **[docs/architecture.md](docs/architecture.md)** for the trust-boundary diagram.

## The bring-up chain

A complete, auditable workflow — every step writes through one fact layer with structured
provenance, no raw SQL:

```
board add  ──►  project init  ──►  validate / risk  ──►  log analyze  ──►  risk resolve
(onboard HW)   (auto-template,     (deterministic       (signatures +     (close with
               assess at minute 0)  PASS/FAIL/UNKNOWN)    cited triage)     provenance)
```

The standout capability: **project-aware log triage**. A vague U-Boot hang with no smoking gun
is correlated against the project's *own* unverified gaps and triaged to a specific
architectural assumption — then written back as a tracked risk **with zero manual correlation.**
The LLM proposes; the deterministic layer decides and records.

## Try it (offline)

```bash
python3 -m pip install pyyaml          # the only runtime dependency
export PYTHONPATH=core                 # or: pip install -e .

python3 -m eaedk.cli db init           # apply migrations (single local SQLite file)
python3 -m eaedk.cli db seed           # 5 templates, 9 boards, risk rules, 7 log signatures
python3 -m eaedk.cli eval run          # -> PASSED 11/11 (deterministic golden cases)

# Optional LLM layer (off by default):
ollama pull qwen2.5-coder:3b
./demo.sh                              # full STM32MP157 DDR triage, end to end
```

`./demo.sh` runs the headline scenario: a U-Boot hang → DDR triage → write-back → resolve.
Record it with `asciinema rec eaedk-demo.cast -c "DEMO_PAUSE=2 ./demo.sh"`.

## Commands

```bash
eaedk board add --interactive          # guided onboarding: live fitment + VTOR checks + cited facts
eaedk project init                     # guided: name, board, goal -> auto-template + immediate assess
eaedk toolchain detect                 # inventory the host build environment
eaedk toolchain validate --project <p> # cross-check tools vs board arch + goal (with fixes)
eaedk validate <project>               # cited PASS/FAIL/UNKNOWN (incl. toolchain) + Facts/Assumptions/Unknowns
eaedk log analyze --file <log> --project <p> --project-aware --llm
eaedk risk show <project>              # live risk-engine + tracked + resolved risks
eaedk risk resolve <id> --note "..."   # close a tracked risk (warns if item still unverified)
eaedk --llm ask <project> "..."        # cited explanation; post-filtered; off by default
```

Goal types / templates: `bootloader`, `uboot`, `linux`, `ota`, `driver` (the five embedded
bring-up templates, encoded as versioned YAML), plus custom (template-less) projects.

## Design decisions that matter

- **Local-first, offline-only.** One SQLite file; no per-token cost; works air-gapped. The
  quality ceiling of a small local model is *fine* precisely because it isn't the source of
  truth.
- **Evolve, not fork.** Facts live in one polymorphic layer (`facts` + the
  `engineering_facts` view) with structured citations — board *identity* stays typed for fast,
  safe rule lookups. (See [docs/03-truth-layer.md](docs/03-truth-layer.md).)
- **Confidence is capped, not configurable.** A board with any UNKNOWN core field can never be
  marked HIGH confidence.

## Layout

```
core/eaedk/
  store/              SQLite + forward-only migrations
  engines/
    validation/       18 pure-function rules (the trust core)
    risk/             data-driven rules over a sandboxed mini-DSL (no eval())
    logs/             format detection, signature matching, async triage, write-back
  llm/                gateway (Ollama) + post-filter + constrained prompts
  orchestrator/       deterministic-first assembly of the fixed response schema
  onboard.py          interactive board wizard      project_init.py  interactive project setup
  repo.py             one place for DB access + record_fact() write-through
packages/             5 templates, 9 seed boards, risk rules, log signatures, eval cases
docs/                 architecture review, MVP spec, truth-layer, log-engine, this README's diagram
demo.sh               end-to-end STM32MP157 DDR demo
```

## Status

MVP through V1 complete and green: **54 pytests, eval 11/11.** Tags `v0.1.0` → `v1.0.0`.
Deterministic core (validation, risk, signatures, **toolchain**), unified truth layer, offline
LLM with post-filter, project-aware triage with write-back, and the full interactive
onboarding chain.

```bash
PYTHONPATH=core python3 -m pytest -q
```

> Reason first. Build second. Verify always.
