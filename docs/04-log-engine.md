# EAEDK — Log Analysis Engine (V1)

**Date:** 2026-06-10
**Status:** Built on the canonical truth layer ([03-truth-layer.md](03-truth-layer.md)).
**Command:** `eaedk log analyze --file <path> [--project <name>] [--llm]`

## Pipeline (deterministic first, LLM last)

```
read file ─► detect format (uboot | dmesg | unknown)
          ─► match Signature DB
                ├─ match  ─► known cause + fix (HIGH confidence, cited to the signature)
                └─ no match & --llm ─► slice 100-line window around the crash vector
                                    ─► post-filtered LLM triage (structural hypotheses, MED/LOW)
```

Async by construction: file I/O and the blocking LLM call run via `asyncio.to_thread`, so
multiple logs can be analyzed concurrently. Deterministic matching never calls the LLM.

## Schema (migration `0003_log_engine.sql`)

- `log_signatures(id, format, pattern_regex, cause, fix, severity, source_id, created_at)`
- `log_files(id, project_id, format, uri, hash, created_at)` — project_id nullable
- `log_analyses(id, log_file_id, signature_id, llm_hypothesis, confidence, created_at)`
  — signature rows are HIGH; LLM-triage rows have `signature_id = NULL`.

## Signature DB (seeded, `packages/knowledge-seed/log_signatures.yaml`)

Five deterministic bring-up failures:
1. **U-Boot bad CRC / checksum** — corrupt image or wrong load address.
2. **U-Boot DDR init fail/timeout** — unverified DDR timing (ties to `DDR_TIMING_VERIFIED`).
3. **U-Boot MMC/SD init** — card/power/pin-mux.
4. **Kernel panic: VFS unable to mount root fs** — wrong `root=` or missing storage/fs driver.
5. **Kernel NULL deref / Oops / BUG** — driver fault during boot.

## Degraded LLM triage (only when nothing matches)

- A strict **100-line window** is sliced around the last crash-keyword line (the "crash
  vector"); if none, the last 100 lines.
- The window goes to the LLM with a JSON-only prompt (`LOG_SYSTEM`): output
  `{hypotheses:[{cause, evidence_line, suggested_check}], confidence}`, quote evidence
  verbatim, invent nothing.
- **Post-filter** runs on every string field. The allowlist = project-cited numbers (via the
  `engineering_facts` VIEW) **∪ numbers appearing in the log window** — so the model may quote
  the log's own addresses (cited evidence) but cannot invent new hardware values. Frequencies
  and timings are always stripped.

## Verification

- `pytest` → **28 passed** (5 new in `test_log_engine.py`: format detection, crash-window,
  U-Boot CRC + kernel-panic matches, and a fake-provider triage that strips an invented
  "168 MHz" while keeping a log-quoted `0xdeadbeef`).
- `eval run` → **11/11**.
- Live runs (`qwen2.5-coder:3b`): U-Boot CRC and kernel-panic logs matched deterministically;
  an unmatched driver log produced structured hypotheses quoting the exact failing line.

## Project-aware correlation (`--project-aware`)

`eaedk log analyze --file <path> --project-aware [--project NAME] --llm`

When the signature DB misses, the engine enriches the triage with the **project's own
architectural gaps** before calling the LLM:

- Loads project state first (`repo.active_project()` if `--project` is omitted).
- `build_correlation()` collects: validation checks currently `FAIL` or engaged-`UNKNOWN`,
  open HIGH/MEDIUM risks, and unverified (MEDIUM/LOW or not-human-verified) board facts read
  through the `engineering_facts` VIEW.
- Those gaps are injected into the triage prompt ("correlate the boot-log failure against
  these architectural gaps the engineer has NOT yet verified…"). The post-filter still
  governs the output.

Demonstrated: an unmatched "system stalled during early relocation" U-Boot hang on a project
with `DDR_TIMING_VERIFIED=UNKNOWN` + `DDR_GUESSED` was triaged to "DDR timing verification
failure" — an inference impossible from the log text alone.

## Not yet built

- More signatures (clock/PLL lock, secure-boot, eMMC ECC), per-format structured field
  extraction, and writing correlated findings back onto the project checklist.
