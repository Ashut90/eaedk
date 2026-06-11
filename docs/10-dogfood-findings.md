# EAEDK — Dogfood Findings (why the mentor layer exists)

**Date:** 2026-06-11
**Method:** EAEDK was run end-to-end as a 0-firmware-experience engineer, and validated against
**real device output from a Wokwi simulation** (not synthetic logs). This document is the
permanent evidence record behind the mentor-layer work (`v1.3.0`).

---

## Two real-data results

### ✅ Result 1 — the Output Engine builds real, correct firmware
Using a real cross-toolchain (`arm-none-eabi-gcc 13.2.1`):
1. EAEDK onboarded an STM32F103 (Cortex-M3, 64 KB flash @ `0x08000000`, 20 KB RAM @ `0x20000000`).
2. `eaedk export` produced `linker/memory.ld` from the verified board geometry.
3. A bare-metal firmware **linked against that exported `memory.ld`** and compiled clean.
4. The ELF was inspected: vector table at `0x08000000`, `SP = 0x20005000`, `Reset = 0x0800004f`,
   `HardFault` handler wired at `0x0800003d`.

**The generated build artifacts aren't plausible-looking text — they compile and link into
runnable firmware.** The output engine is validated on a real toolchain.

### ❌ Result 2 — the Log Engine is blind to the MCU world
A real ESP32 crash captured from Wokwi (null-pointer write):
```
Guru Meditation Error: Core  1 panic'ed (StoreProhibited). Exception was unhandled.
...
EXCVADDR: 0x00000000  EXCCAUSE: 0x0000001d
Backtrace: 0x400d1639:0x3ffb2250 ...
```
`eaedk log analyze` on it:
```
Format detected: unknown  (19 lines)
- No known signature matched.
```
Total silence. The crash is a textbook null-pointer write (`EXCVADDR: 0x0`) — the most common
embedded bug — and EAEDK offered no recognition, no teach, no "try `--llm`", no next step.

It did *correctly* refuse to hallucinate a diagnosis (the truth hierarchy held — "unknown" beats
a wrong guess). But bare silence teaches a beginner nothing.

---

## The pattern

**The engines and trust model are sound; the knowledge coverage targets the wrong audience.**
EAEDK is pitched for 0-experience engineers, but its content — the 5 templates (bootloader /
U-Boot / Linux / OTA / driver), the 7 log signatures (all U-Boot/kernel), and the format
detector (`uboot`/`dmesg` only) — assumes Linux/SoC bring-up. A beginner lives in HardFaults,
Guru Meditations, and blink/UART projects.

---

## UX friction (from the full beginner walkthrough)

1. **Onboarding demands expert data.** `board add` asks for flash base (`0x08000000`), RAM base,
   core type, and partition offsets — exactly the datasheet values a beginner doesn't have.
   Result: a board with `Flash UNKNOWN, RAM UNKNOWN`, which cascades into everything downstream.
2. **Duplicate board, no warning.** STM32F411RE ships seeded, but `board add` let me create a
   worse duplicate next to it without a word.
3. **No beginner goal.** "UART logger" / "blink" has no template; the menu is all advanced.
4. **"FEASIBLE" misleads.** `project init` reported FEASIBLE for a board it knew nothing about.
5. **Filling a field made it worse.** Setting `estimated_image_size` flipped FEASIBLE → BLOCKED
   (it engaged a rule that needs the board's unknown flash size), with no explanation.
6. **`export` dead-ends.** Refused (BLOCKED on `board.flash_bytes`) — the payoff never arrives.
7. **Validation jargon, no help.** Eight raw `UNKNOWN`s (`vector_table_addr`, `estimated_image_size`)
   with no units and no teach.
8. **Teach layer barely fires.** It exists *only* for the toolchain engine and *only* for seeded
   boards — a user-onboarded board got "No toolchain profile defined," i.e. zero help.
9. **Log miss = silence** (Result 2).

---

## Prioritized build list (evidence-backed)

1. **MCU crash signatures + format detection** *(v1.3.0, building now)* — Cortex-M HardFault
   (`HFSR`/`CFSR`), ESP32 Guru Meditation (`StoreProhibited`/`LoadProhibited`/`EXCVADDR`), an
   `mcu` format detector, and "no match → run with `--llm`" instead of silence. Directly closes
   Result 2.
2. **Arch-default toolchain profile inheritance** *(v1.3.0)* — user-onboarded boards inherit a
   default toolchain profile from `soc.arch`, so the teach layer fires for *their* board. Closes
   friction #8.
3. **`bare_metal_app` template** *(v1.3.0)* — blink/UART, the beginner's actual first project.
   Closes friction #3.

Deferred (need design): teach on every validation rule (friction #7), "this board is already
seeded — use it" + ingest nudge (friction #1/#2), readiness score vs. "FEASIBLE" (friction #4/#5).

---

## v1.4.0 update — mentor-UX (the start-of-funnel friction, now closed)

A second dogfood confirmed the three v1.3.0 fixes landed, and exposed that the deferred
start-of-funnel items were where every beginner still got stuck. v1.4.0 closes them:

- **Friction #2 — "board already seeded" nudge** ✅ The wizard now matches the new board's
  name/SoC against existing boards and, before committing, points the engineer at the seeded
  entry (`use it with project init`) or `eaedk ingest` for their own — without blocking.
- **Friction #7 — per-rule validation teach** ✅ Every non-PASS validation now carries a
  `teach` string (what the field is, units, where to find it, the consequence) via a central
  `RULE_TEACH` catalog — e.g. `board.flash_bytes: total flash in bytes — datasheet memory map,
  STM32F411RE = 524288; without it image-fit checks and export can't run`.
- **Friction #4 — "FEASIBLE" misleads** ✅ A board with no flash AND no RAM now reports
  feasibility **`no_geometry`** ("INCOMPLETE — complete board facts before relying on this"),
  not FEASIBLE.
- **New export finding — silent unbuildable files** ✅ Export now **refuses** on `no_geometry`
  with a clear message (or, with `--force`, emits files plus a loud "these will NOT build —
  run ingest" banner). A beginner can no longer walk away with placeholder artifacts.

Verified on the same beginner path: the nudge fired, project init said INCOMPLETE, validate
taught each UNKNOWN, and export refused with the geometry message. **77 tests, eval 11/11.**

Still deferred (lower frequency): friction #5 (filling a field flips feasible↔blocked) and a
beginner "what is a linker script / how to build" on-ramp in the export bundle.

---

> The mentor layer isn't a nice-to-have bolted on later. The dogfood proves EAEDK currently
> gates a beginner without teaching them past the gate — on the exact crashes and projects a
> beginner actually hits. The engines are ready; the knowledge needs to meet the audience.
