# EAEDK — Interactive Board Onboarding

**Date:** 2026-06-10
**Command:** `eaedk board add --interactive`
**Goal:** onboard a new board on new hardware with **no raw SQL and no internals knowledge** —
the biggest usability gap for engineers outside this project.

## Guided flow

1. **Identity & geometry:** board name, vendor, SoC name, core architecture (binds VTOR
   alignment: M0/M0+ → 256 B, others → 512 B), flash size+base, RAM size+base. Sizes accept
   hex or `2MB`/`512KB` (1024-based). Blank = UNKNOWN → `null` in the DB.
2. **Confidence level (explicit):** the engineer chooses HIGH/MEDIUM/LOW. The choice is
   **capped by the data** — if any core field is UNKNOWN, HIGH is refused and capped to MEDIUM
   (defensive posture). Default is the ceiling the data supports.
3. **3-slot partition allocation** with **live in-loop validation** (fitment, no-overlap, VTOR
   alignment) — a bad offset is caught and re-prompted immediately, never deferred.
4. **Optional initial facts:** a loop to enter known parameters (DDR timing, clocks, memory
   regions) each with `domain` (MEMORY/CLOCK/TIMING/PINMUX/POWER), `source_type`
   (DATASHEET/TRM/SDK_DOC/SCHEMATIC/USER_INPUT), citation section + page, and per-fact
   confidence.
5. **Confirm summary:** prints what was written (board geometry, partitions, facts) and what
   remains UNKNOWN before exiting.

## No raw SQL; everything through the truth layer

Onboarding holds **no hand-written board SQL**. It writes through repo helpers:

- `repo.create_manual_source()` → provenance source.
- `repo.get_or_create_soc()` → reuse-by-name (respects `socs.name` UNIQUE; multiple boards
  share a SoC).
- `repo.create_board()` → the typed board identity row (identity stays typed, never EAV).
- `repo.record_fact()` → partitions **and** the optional initial facts land in the unified
  `facts` layer with `domain`/`source_type` and structured `citations` provenance.

So a freshly onboarded fact is immediately visible through the `engineering_facts` VIEW and is
governed by the post-filter and project-aware correlation like any other fact.

## Verification

- `pytest` → **36 passed** (new: explicit-confidence-respected, HIGH-capped-to-MEDIUM,
  initial-fact-with-citation; existing wizard tests updated for the new prompts).
- `eval run` → **11/11**.
- Live: onboarded a Cortex-M0+ board at MEDIUM with a `TIMING.ddr_tRCD=13` fact cited to
  "Table 9, DDR3 AC timing" p.104 — stored through `record_fact()`, visible in the VIEW.
