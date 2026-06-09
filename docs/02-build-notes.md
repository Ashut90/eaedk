# EAEDK — Build Notes (MVP)

**Date:** 2026-06-10
**Tracks:** [01-mvp-specification.md](01-mvp-specification.md) §6 build order.
**Status: 🔒 LOCKED — V0/MVP baseline.** This document records the frozen MVP milestone;
V1 work (log analysis, datasheet ingestion, errata) is tracked in later notes, not here.

## Status

MVP foundation built and green. `eaedk eval run` → **PASSED 11/11**; `pytest` → **19 passed**
(engines, eval, LLM post-filter, interactive onboarding). End-to-end LLM pass proven on real
STM32F407 data: the post-filter stripped an uncited "168 MHz" clock while letting the verified
`0x08000000` flash base through.

**Rule-engine expansion (2026-06-10):** added `PINMUX_CONFLICT` (pin double-claim detection)
and `POWER_SEQUENCE` (power-rail dependency ordering), each with FAIL/PASS golden cases.
Seed boards expanded to **9** with STM32H743 (HIGH confidence, full memory map) and
RTL8722DM (MEDIUM; arch + radios seeded, on-chip memory map left NULL — a guessed value
would violate the truth hierarchy, so rules needing it correctly report UNKNOWN).

| §6 step | Component | State |
|---|---|---|
| 1 | `store/` migrations runner + `0001_init.sql` + pragmas (WAL, FK on) | done |
| 2 | `db seed`: 5 templates, 7 boards, risk rules, 6 eval cases | done |
| 3 | Validation Engine — 18 pure-function rules (incl. PINMUX_CONFLICT, POWER_SEQUENCE) | done |
| 4 | Risk Engine — sandboxed mini-DSL (no `eval()`) | done |
| 5 | Orchestrator + fixed response schema + project state CRUD | done |
| 6 | CLI command surface (§4.1) | done |
| 7 | Eval harness wired to §5 | done (6/6) |
| 8 | LLM Gateway (offline Ollama `qwen2.5-coder:3b`) + uncited-claim post-filter | done (off by default) |

## Deviations from the spec (and why)

1. **CLI: argparse instead of Typer.** The environment's Typer/Click install is broken
   (`click.Choice` not subscriptable). Rather than pin a fragile dependency into an
   offline-first tool, the CLI uses stdlib `argparse` with the exact §4.1 command surface.
   Swapping back to Typer later is mechanical. Net runtime dependency: **PyYAML only.**

2. **Vector-table alignment is architecture-derived.** The spec table implied a fixed
   512-byte alignment for `VECTOR_TABLE_PLACEMENT`. ARMv6-M (Cortex-M0+, e.g. RP2040)
   requires 256-byte VTOR alignment, not 512. The rule now derives the requirement from
   `soc.arch` (M0/M0+ → 256, other Cortex-M → 512), overridable via `vector_table_align`.
   Without this, the legitimate RP2040 case would wrongly FAIL.

3. **Feasibility "engaged" refinement.** An `UNKNOWN` rule blocks feasibility only when the
   engineer has supplied at least one of its inputs (or it has none). A rule with no inputs
   provided is "not started" → surfaced under Missing Information, not a blocker. This keeps
   strict UNKNOWN handling (Q3) without every un-filled global rule freezing a fresh project.

## Validated behaviours (manual + eval)

- `FLASH_CAPACITY` PASS/FAIL; RP2040 external-QSPI/XIP window reasoning (flash_base ≠ on-die).
- `UNKNOWN`-as-blocker: unverified DDR timing ⇒ `feasibility: blocked` (not feasible).
- Checklist `done` refused (exit 3) while a linked rule is FAIL or engaged-UNKNOWN.
- Risk DSL fires `FLASH_TIGHT`, `DDR_GUESSED`, `WATCHDOG_UNCONFIRMED`; unknown idents → UNKNOWN, never silently skipped.

## Step 8 — LLM gateway (built 2026-06-10)

Offline-only, off by default; only `ask`/`explain` touch it (everything else stays
deterministic). Files under `core/eaedk/llm/`.

- **Provider:** Ollama over stdlib `urllib` (no new dependency). Default model
  `qwen2.5-coder:3b` (`EAEDK_LLM_MODEL` / `EAEDK_OLLAMA_HOST` override). `available()` does an
  **exact** tag check — a sibling tag (e.g. `:7b`) does not count, else `generate` 404s.
- **Two-layer guardrail (defense in depth):**
  1. *Prompt* — the model is told to explain only, cite only the provided CONTEXT, and echo
     UNKNOWN rather than fill gaps. (Verified live: asked for clock/DDR values it wasn't
     given, `qwen2.5-coder:7b` correctly answered UNKNOWN.)
  2. *Post-filter* (`postfilter.py`) — the real enforcement. Builds an allowlist of cited
     numbers from SQLite (board fields, human-verified facts, engineer inputs incl. nested
     region/partition ints) and strips any sentence asserting a hex address / memory size /
     **frequency / timing** not in that set. Frequencies & timings are never in the MVP DB,
     so any such assertion is removed by design. Conservative: strips the whole sentence.
- **Graceful degradation:** if Ollama is down, the model isn't pulled, or `generate` errors,
  the deterministic assessment still prints; the LLM section prints a clear notice. Never
  tracebacks (caught `URLError`/`OSError`).
- **Tested without Ollama:** `test_llm.py` uses a fake provider; proves the allowlist build,
  cited-value pass-through, and stripping of an invented `800 MHz` timing.

## Interactive onboarding (`board add --interactive`, built 2026-06-10)

`core/eaedk/onboard.py` — a terminal wizard that loads pristine board data with **live,
in-loop validation**. I/O goes through injected `ask`/`out` callables (real `input`/`print`
in the CLI, scripted in tests), so the whole flow is unit-tested without a TTY.

- **Prompts:** board name & vendor, SoC name, **core-architecture selection that binds the
  VTOR alignment** (M0+ → 256 B, M3/M4/M7/M33 → 512 B, or a custom arch string), flash & RAM
  size+base (hex or `2MB`/`512KB`, 1024-based), and a bootloader + 3-slot (A/B/C) layout.
- **In-loop validation:** the moment partition offsets are entered, the wizard runs the
  *existing* `PARTITION_LAYOUT_FITS`, `PARTITION_NO_OVERLAP`, and `VECTOR_TABLE_PLACEMENT`
  rule functions (single source of truth). On a FAIL it prints the precise error
  (e.g. "Slot B overlaps Slot C") and re-prompts the offsets with current values as
  defaults — never deferring failure to the end.
- **Commit logic:** blank/UNKNOWN inputs are written as `null` and drop the board record to
  `MEDIUM` confidence (the high-integrity posture); a clean pass commits at `HIGH`, stores
  partitions as `facts(kind='partition')`, and prints a verified-boundary summary.
- **Bug caught during live testing:** the wizard re-inserted the SoC even when one of that
  name existed (`socs.name` UNIQUE). Fixed to reuse the existing SoC so multiple boards can
  share one. Covered by `test_two_boards_can_share_a_soc`.

## Not yet built (next, per roadmap)

- RTL8722DM memory map to be filled from datasheet (currently NULL / MEDIUM).
- V1 items remain out of scope: datasheet ingestion, log analysis, errata matrix, repo analysis.
