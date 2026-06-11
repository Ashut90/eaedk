# 14 — Multi-Agent (Actor-Critic) Stress Test

**Date:** 2026-06-12
**Type:** read-and-run audit. No code, schema, seed, CLI, or test was modified.
**Baseline:** `pytest` 157 passed; `eaedk eval run` 14/14 — confirmed clean before and after.
**LLM:** Ollama live, `qwen2.5-coder:3b` (the loop's agents actually ran; outputs are real).

> **Verdict up front:** the *deterministic* layer (Validation Engine → `validate` / `export`)
> is the hero of every scenario — it catches and **blocks** every real fault and refuses to
> emit unbuildable/dangerous artifacts. The *Actor-Critic multi-agent loop* is the weak link:
> it reviews the wrong artifact, is blind to the project's actual inputs, and emits unverified
> advisory claims that were frequently **wrong** (calling correct code broken, calling an
> oversized stack "too small"). The trust hierarchy held at the *confirmation* boundary (the
> arbiter never CONFIRMED a false claim), but the *advisory* channel is an unfiltered firehose
> of LLM guesses shown next to real analysis, and the Actor's fix text is **not post-filtered**.

---

## How the loop actually works (ground truth from the code)

`core/eaedk/actor_critic.py`, invoked by `eaedk mentor --review-code --project <P>` (note: the
prompt said `eaedk export --review-code`, which does not exist — the verb is under `mentor`):

1. It builds a scaffold with `codegen.render_main_c(data)` — **always a bare-metal C file**,
   regardless of the project's goal_type. For families without a register template
   (Cortex-A, Xtensa) this is a TODO **skeleton**.
2. **Critic (LLM)** reviews that scaffold for a fixed, narrow list: *missing clock enable,
   wrong init order, stack too small, buffer larger than RAM*. It returns JSON issues.
3. **Arbiter (deterministic)** re-checks **only memory claims** that carry numeric sizes,
   through the real `RAM_BUDGET` rule against the board's verified RAM. A `FAIL` → **CONFIRMED**;
   anything else (including every structural claim) → **advisory**.
4. **Actor (LLM)** runs only if something was CONFIRMED, and writes a plain-language fix.

Three structural consequences, true before any scenario was run:

- **It never reads the project's inputs.** The arbiter re-checks the *Critic's* hallucinated
  sizes, not the engineer's `stack_size`/`partitions`/etc. The loop is blind to the very values
  `validate` is built to check.
- **It only deterministically verifies memory.** Clock-enable, init-order, partition overlap,
  DTB correctness, register bases — none can be confirmed; they live or die as LLM advisory.
- **The Actor's fix text is not post-filtered.** `filter_text` is applied to `mentor ask/explain`
  and to log triage, but **not** to `_actor()` output — an invented address/clock in a "fix"
  would reach the user verbatim. (Not triggered in these runs, but latent.)

---

## Scenario 1 — Beginner, UART Logger, STM32F103 Blue Pill

Setup: `project init` → Blue Pill, `bare_metal_app`. Ran `mentor --review-code` three times
(LLM is non-deterministic at temp 0.2).

- **What the Actor generated:** In runs 1 & 3, no Actor fix (nothing CONFIRMED). In run 2 the
  Critic emitted numeric sizes that the arbiter CONFIRMED, so the Actor wrote:
  *"Fix 1: Enable the clock for GPIOC before using it. Fix 2: Increase the buffer size of the
  UART transmit function to accommodate larger data transmissions, reducing RAM usage and
  preventing overflow errors."*
- **What the Critic flagged:** `missing_clock_enable: The clock for GPIOC is not enabled before
  using it` and `wrong_peripheral_init_order: UART init should be done after the USART1 clock is
  enabled` (runs 1 & 3); a memory/buffer claim (run 2).
- **What the Validation Engine confirmed/rejected/advisory:** CONFIRMED **nothing** in runs 1 & 3
  (correct — there is no memory fault). The structural claims were correctly relegated to
  **advisory** (the arbiter has no rule to prove them).
- **What the engineer received:** Two confident-sounding "advisory" defects — about code that is
  **correct**. Ground-truth check of the scaffold: `clocks_init()` runs before `led_init()`,
  `IOPCEN` (GPIOC clock) **is** enabled, and `USART1_BRR = 0x45` is the correct 8 MHz/115200
  divisor. Every Critic claim was a **false positive**. Run 2's Actor "fix" was worse:
  self-contradictory ("increase buffer size … reducing RAM usage") and again about an
  already-present clock enable.
- **Could a beginner act on it?** Net-negative. A beginner cannot tell that the advisory claims
  are false; following run 2's advice (enlarge a buffer to "reduce RAM") teaches a wrong mental
  model. There is **no teach string** explaining *why* a fix was made (the prompt asked for it);
  the Actor's one-liners assert, they don't teach against the cited registers.
- **Where it failed/stayed silent:** The Critic invents defects in correct code; the system has
  no way to refute a structural false-positive (only memory is arbitrated), so noise reaches the
  user unchallenged.

---

## Scenario 2 — Mid-level, Linux bring-up, STM32MP157

Setup: `project init` → STM32MP157, `linux`. `validate`, then two kernel-panic logs, then the
DTB request.

- **`validate`:** FEASIBLE. (Minor copy bug noted: the v1.8.0 reassurance line prints *"you do
  not need them to blink an LED"* on a **Linux** project.)
- **Deterministic log match:** A realistic panic (`i2c probe failed` → `phandle` → null deref →
  `Kernel panic`) matched the **downstream** signature `Unable to handle kernel NULL pointer
  dereference` (fix points at `probe()/of_match_table` — relevant). But the **root cause** (the
  i2c phandle / missing DTB node on lines 2–4) matched **no** signature, and because *any*
  deterministic match short-circuits the LLM, the richer triage never ran on the real panic.
- **LLM triage (on a no-match variant):** Correctly diagnosed *"Missing device tree node"* and
  *"Incorrect compatible string,"* quoting `/soc/i2c@40012000/sensor@68` and `0x68` **from the
  log**. **Post-filter removed 0** — correct, because those addresses are cited by the source
  (`numbers_in_text`). This path is genuinely useful to a mid-level engineer.
- **The actual ask — "Actor generates a device tree node":** **The capability does not exist.**
  `--review-code` for STM32MP157 (Cortex-A7) generated a bare-metal C `int main()` **skeleton**
  with register TODOs — meaningless for a Linux board — and the Critic "reviewed" it with
  *"stack: Stack size is too small / heap: Heap size is too small."*
- **Did the Actor cite the correct register base from SQLite?** N/A — it produced no DTB and no
  register node. STM32MP157 has no flash/RAM geometry in the DB, so even the C skeleton is empty.
- **Did the Critic catch a wrong compatible string / missing interrupt-parent?** **No** — those
  are out of the Critic's fixed scope, and it wasn't looking at a DTB anyway.
- **Could a mid-level engineer act on it?** The **log triage**: yes, it points at the DTB node and
  compatible string. The **Actor-Critic review**: no — it hands a Linux engineer a bare-metal C
  skeleton and stack/heap comments unrelated to their problem. Actively misleading.
- **Where it failed:** No DTB/devicetree generation path exists; `--review-code` applies the
  bare-metal codegen to every goal, so for Linux/MPU targets it produces the wrong artifact and
  the Critic critiques the wrong thing.

---

## Scenario 3 — Failure handling, RP2040 Pico, oversized stack

Setup: `project init` → Pico (`bare_metal_app`), `input set stack_size 524288` (RAM is 270336 B),
heap/static 0.

- **`eaedk validate`:** Caught it exactly — `RAM_BUDGET: FAIL — RAM use 524288 B exceeds
  270336 B (193.9%)`, feasibility **NOT FEASIBLE**, clear next step.
- **`eaedk export`:** **Refused** (`NOT_FEASIBLE`), listed the blocker, and — a nice touch —
  offered the correct RP2040 standard geometry. The engineer cannot walk away with a bad build.
- **Actor-Critic:** CONFIRMED **nothing**. Advisory: *"stack: Stack size is too small"* — the
  **exact opposite** of reality (it's 2× too large) — and *"buffer: Buffer larger than available
  RAM."* The loop never ingested the engineer's `stack_size`, so it could not arbitrate the real,
  deterministically-provable overflow; it relied on the Critic's guesses, which carried no numeric
  `check`, so the arbiter couldn't confirm them.
- **What the engineer received / can they act:** From `validate`/`export`: a precise, correct,
  blocking error — fully actionable. From `--review-code`: contradictory, unconfirmed noise that
  inverts the actual problem.
- **Where it failed/stayed silent:** The multi-agent loop stayed silent on the real fault
  (because it can't see project inputs) while emitting a directionally-wrong advisory. Only the
  deterministic engine spoke correctly.

---

## Scenario 4 — Edge case, ESP32 OTA, overlapping partitions

Setup: `project init` → ESP32-DevKitC (`ota`); `partitions` with slot_a `[0x10000, 0x110000)`
and slot_b `[0x100000, 0x200000)` (overlap), `primary_storage_bytes 4194304`.

- **`eaedk validate`:** Caught it — `PARTITION_NO_OVERLAP: FAIL — slot_a overlaps slot_b`, with
  the teach string ("overlapping ranges corrupt adjacent images"), feasibility **NOT FEASIBLE**.
  (`PARTITION_LAYOUT_FITS`, `PARTITION_AB_SYMMETRY`, `RECOVERY_PRESENT` correctly PASS.)
- **`eaedk export`:** **Refused** (`NOT_FEASIBLE`) with the overlap blocker — the engineer is
  warned **before** flashing corrupted firmware. This is exactly the safety the scenario asked for.
- **Actor-Critic:** Generated a bare-metal C **skeleton** for ESP32 (Xtensa) — **no OTA function,
  no partition awareness**. The Critic did **not** flag the overlap (out of scope) and instead
  emitted advisory *"stack too small / heap too small."* CONFIRMED nothing.
- **Could an engineer act on it?** Via `validate`/`export`: yes — clear, blocking, correct. Via
  `--review-code`: no — it is silent on the catastrophic overlap and irrelevant otherwise.
- **Where it failed/stayed silent:** The multi-agent loop is **silent on the actual dangerous
  fault** while the deterministic engine catches and blocks it.

---

## Cross-cutting findings (severity ordered)

**F1 (High) — The Critic produces confident false positives on correct code, unrefutable.**
Scenario 1: "GPIOC clock not enabled / wrong init order" on a scaffold that does both correctly.
Because only memory claims are arbitrated, structural false-positives cannot be disproven and
reach the user as plausible "advisory" defects. A beginner cannot distinguish them from real
ones. *Impact: erodes trust / teaches wrong mental models — the opposite of the mentor goal.*

**F2 (High) — The Actor-Critic loop is blind to project inputs.** It reviews a generic scaffold
and arbitrates the Critic's *hallucinated* sizes, never the engineer's actual
`stack_size`/`partitions`. In Scenario 3 it called a 2×-oversized stack "too small." The only
reason the engineer is safe is that `validate`/`export` run a *different*, deterministic path.
*Impact: the multi-agent loop cannot catch the very faults the engine is designed to catch.*

**F3 (High) — `--review-code` applies bare-metal C codegen to every goal_type.** For Linux
(Cortex-A) and ESP32 OTA it produces the wrong artifact entirely (a blink-style C skeleton) and
the Critic critiques the wrong thing. There is **no** DTB or OTA generation capability, so
Scenario 2's and 4's core requests cannot be fulfilled by the agents. *Impact: actively
misleading for mid-level targets.*

**F4 (Medium) — Critic scope is too narrow for the scenarios it's pointed at.** It checks only
clock-enable / init-order / stack / buffer. Partition overlap (S4), DTB compatible string /
interrupt-parent (S2), and baud-rate correctness (S1, explicitly asked) are all out of scope and
silently uncovered by the loop.

**F5 (Medium) — The Actor's fix text is not post-filtered.** `filter_text` guards
`mentor ask/explain` and log triage but not `_actor()`. An invented address/clock in a fix would
reach the user verbatim. Latent (the small model didn't emit one here), but the trust-hierarchy
guarantee that holds elsewhere is absent on this path.

**F6 (Low) — Deterministic short-circuit can hide the root cause in log analysis.** Any
signature match suppresses the LLM triage; in S2 a generic downstream null-deref matched, so the
more useful "missing DTB node" triage never ran on the real panic.

**F7 (Low, cosmetic) — Validate reassurance copy is hardcoded to blink.** "you do not need them
to blink an LED" prints on a Linux bring-up project (v1.8.0 Fix 4 text).

## What worked (do not regress)

- **The Validation Engine arbitration held at the confirmation boundary in all 4 scenarios** —
  it never CONFIRMED a false Critic claim; unprovable claims were correctly demoted to advisory.
- **`validate` caught every real fault** (RAM overflow, partition overlap) with correct numbers
  and teach strings.
- **`export` refused every not-feasible project**, protecting the engineer from bad artifacts —
  and offered corrective geometry (S3).
- **The post-filter correctly allowed log-cited addresses and removed 0 false positives** (S2).
- **The LLM log triage was genuinely useful** for the mid-level DTB problem (S2).

## Recommendation (for a separate fix branch — NOT done here)

The deterministic spine is production-grade; the multi-agent loop needs to be brought up to it:
feed real project inputs into the Critic/arbiter; arbitrate (or suppress) structural claims
instead of surfacing them unverified; branch codegen by goal_type (or refuse `--review-code`
for goals without a register template, pointing to the right tool); and run `filter_text` over
the Actor's output. Until then, `--review-code` should be treated as experimental and the
deterministic `validate`/`export` path as the source of truth.
