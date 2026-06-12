# 15 — Multi-Agent Fixes (v1.9.0-multiagent-fix)

Closes the seven findings from `docs/14-multiagent-stress-test.md`. The deterministic spine
(Validation Engine → `validate`/`export`) was production-grade; this release brings the
Actor-Critic loop up to it. **Additive only**: new functions/fields/flags; no existing CLI verb,
schema, seed, validation rule, or test behaviour changes. The 157-test suite and `eval 14/14`
must pass before and after. The two existing Actor-Critic tests
(`test_arbiter_confirms_overbudget_stack_rejects_within_budget`,
`test_actor_critic_loop_terminates_and_arbiter_governs`) pin contracts that are preserved:
`arbitrate(issues, board, soc)` keeps its signature; the Critic system prompt keeps the words
"review"/"JSON" and the Actor prompt keeps neither (the fake `_LoopProvider` switches on them).

## Root-cause summary

The loop reviewed a generic bare-metal C scaffold **in isolation** — it never read the
project's real inputs from SQLite, only deterministically arbitrated *memory* claims, and left
the Actor's text unfiltered. So it invented defects in correct code (F1), missed the engineer's
actual oversized stack / overlapping partitions (F2), produced the wrong artifact for Linux/OTA
goals (F3), under-scoped the Critic (F4), and skipped the trust filter on the Actor (F5).

## Fixes

### F1 + F2 — Ground the loop in the project's real inputs (same root cause)
Before the Critic runs, `run_actor_critic` now:
1. Loads the project's inputs (`repo.load_inputs`) and **injects them into the Critic prompt** as
   `VERIFIED PROJECT INPUTS` + `BOARD GEOMETRY`, instructing the Critic to critique against what
   the engineer actually provided, not what it imagines.
2. Runs the **real Validation Engine** over the project (`assess_project`) and turns every
   gating `FAIL` and engaged-`UNKNOWN` into a **deterministically CONFIRMED** issue (carrying the
   rule's reason + teach). These `grounded` confirmations are merged ahead of any LLM-arbitrated
   ones, so the loop now *always* surfaces the real, provable faults (oversized stack, partition
   overlap) regardless of what the Critic LLM says — and a false Critic claim can no longer
   masquerade as confirmed (only the engine confirms).

`arbitrate()` is untouched (still re-checks the Critic's numeric memory claims). A new
`grounded_confirmations(conn, project)` helper does the deterministic pass.

### F3 — Goal-aware review artifact
New `codegen.render_review_artifact(data) -> (kind, content)` branches on `goal_type`:
- `bare_metal_app` / `bootloader` → `bare_metal_c` (the existing `render_main_c`, unchanged).
- `linux` / `driver` → `devicetree` — a DTB node skeleton (`render_dtb_node`) that cites a
  register base from SQLite when a verified fact exists and emits explicit `<TODO …>`
  placeholders otherwise (never an invented address).
- `ota` → `partition_table` — a partition-table review (`render_partition_review`) listing the
  project's actual partitions for the Critic to inspect (overlap is confirmed deterministically
  by the grounded pass).
- anything else → `bare_metal_c` (safe default).

`run_actor_critic` reviews the goal-appropriate artifact and records `artifact_kind` on the
result; the CLI prints what was reviewed. The bare-metal path is byte-for-byte the old behaviour,
so the loop-termination test is unaffected.

### F5 — Post-filter the Actor's fix text
`_actor` now takes the board allowlist and runs the Actor's output through `filter_text` — the
same post-filter that governs `mentor ask/explain` and log triage. Any uncited address/clock in
a "fix" is stripped to the standard marker. No exceptions to the trust hierarchy.

### F4 — Broaden Critic scope
The Critic system prompt now also looks for: partition overlap (A/B slots), a missing/placeholder
DTB `compatible` string and missing `interrupt-parent`, and an implausible UART baud-rate
divisor — in addition to the original clock-enable / init-order / stack / buffer set. Scope words
"review"/"JSON" are retained for the fake-provider contract. Where a deterministic rule exists
(partition overlap), the grounded pass confirms it; the rest remain advisory but are now
in-scope.

### F6 — Opt-in deep triage (low)
New `eaedk log analyze --deep` flag: when set, the LLM triage runs **in addition to** any
deterministic signature match (instead of being short-circuited), so a generic downstream match
no longer hides the root-cause triage. Default behaviour is unchanged (no `--deep` → identical to
today), so every existing log test is unaffected.

### F7 — Goal-neutral validate copy (cosmetic)
The feasible-with-unknowns reassurance line no longer hard-codes "to blink an LED"; it now reads
"for a first build", which is correct for every goal type. No test asserts the old string.

## Testing
New `core/tests/test_multiagent_fix.py` covers each fix deterministically with a fake provider
(no live LLM): grounded confirmations catch the oversized stack and partition overlap; the Actor
filter strips an invented address; the artifact is goal-correct; the broadened Critic prompt
carries the new scope; `--deep` triages on a matched log. Existing 157 tests stay green.
