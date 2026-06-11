# 13 — Full Coverage (v1.8.0-full-coverage)

## Finding

v1.7.0 closed the beginner on-ramp end to end (board → understanding → build → flash). The
next gaps are the *partially covered* cases that bite both a beginner mid-project and a
mid-level engineer doing real bring-up. None of them are engine bugs — they are **coverage
gaps in the seeded knowledge** (the recurring EAEDK pattern: engines are sound, content needs
to meet the audience). This release fills them, **additively only**.

## Preservation contract (non-negotiable)

This is the governing constraint for the whole release:

- The 133-test suite and `eval 11/11` pass **before and after every change**.
- **No existing behavior changes**: no existing CLI verb, validation rule, DB schema, template,
  or seed row is modified. Every addition is new — new forward-only migration, new YAML seed
  rows/files, new CLI flags, new validation rules, new log signatures.
- One existing test legitimately updates: `test_init_custom_goal_is_templateless` hard-codes
  "custom = menu option 7 (after 6 templates)". Two new templates make the menu grow, so the
  *index of the unchanged "Custom" option* shifts to 9. This is a brittle-index update in the
  test, not a behavior change — the menu still ends in Custom. Called out explicitly here.
- `test_golden_eval_suite_all_pass` asserts `total == 11`; new eval cases raise that count. The
  invariant that matters (`failed == 0`) is unchanged; the literal is updated to the new total.

Why these two are safe: the eval harness compares only the **named** validations/feasibility a
case asserts (subset match), so new rules are invisible to existing cases **as long as they do
not engage** — and they only engage on brand-new input keys no existing case supplies. New
templates add new `goal_type`s the existing cases never reference. New log signatures use
`mcu`/`silent` formats (or sentinel patterns) that the existing log fixtures don't trigger.

## Beginner gaps

### B1 — "Board boots, nothing on serial" → guided UART debug
Add a `uart-debug` concept (new row in `concepts.yaml`) whose anchor is a short, ordered debug
flow: UART clock/init before the print? baud = 115200? TX on the right pin? ground shared? The
existing `mentor --explain <concept>` path renders it unchanged — no code change, just content.
`render_board_mentor` gains a small "if it boots but nothing prints" pointer to it (additive
output text).

### B2 — "Board doesn't boot after flashing" → silent-boot signature
A truly empty capture (no output at all) currently yields "No known signature matched." Add a
seeded signature (`format: silent`, sentinel pattern so it never auto-matches real text) with
the curated cause/fix ("No output detected. Common causes: wrong boot-mode pins, flash write
failed, clock not initialised. On STM32 check BOOT0 is LOW for normal boot."). The log engine
detects an **empty/whitespace-only** log (`text.strip() == ""`) and, only then, synthesises a
match from that seeded row. Non-empty unrecognised logs keep the existing "--llm" behaviour
(the gibberish test is unaffected: it has content, so it is not silent).

### B3 — Common first mistakes per architecture → `mentor --common-mistakes`
New table `first_mistakes(family, mistake, fix, severity)` (migration 0010) seeded from
`first_mistakes.yaml`: STM32 (BOOT0 wrong, clock not enabled before peripheral, wrong baud
divisor), RP2040 (wrong flash offset, missing boot2 stage), ESP32 (wrong partition table,
missing NVS init), AVR (fuse bits, F_CPU mismatch). `mentor --board <name> --common-mistakes`
maps the board's SoC to a family and lists them. New CLI flag, new table, new seed — additive.

### B4 — Hand-holding between projects → `mentor --next`
New table `learning_step_intro(step_key, introduces, concept)` (migration 0010) seeded from
`learning_step_intro.yaml`, cross-linking each learning step to the new concept it teaches.
`mentor --board <name> --next [COMPLETED_STEP_KEY]` shows the next project: what it introduces,
the new concept (with its anchor from the concept library), and what to set up first (reusing
the existing `before_you_start` from `learning_steps` — not modified). No arg → the first step;
with the just-finished step key → the one after it.

## Mid-level gaps

### M1 — Multi-core bring-up → `multicore` template
New `packages/templates/multicore_bringup.v1.yaml` (goal_type `multicore`) covering: core boot
order, inter-core communication setup, shared-memory regions, and per-core peripheral ownership
(plus reset/clock release). Guided checklist items (same style as existing templates; no new
rules required). Goal labels added; appended to the goal menu order so option 1 ("start here")
is unchanged.

### M2 — Secure boot chain → new validation rules
Four new HIGH rules in `rules.py`, scoped to `("bootloader", "ota")`, each engaging only on a
new input key (so existing bootloader/OTA eval cases are untouched):
`SECURE_BOOT_SIGNATURE_VERIFY`, `SECURE_BOOT_KEY_STORAGE`, `SECURE_BOOT_ROLLBACK_COUNTER`,
`SECURE_BOOT_DEBUG_LOCKED`. Each gets a `RULE_TEACH` string and golden eval cases covering
PASS, FAIL, and engaged-UNKNOWN→blocked.

### M3 — RTOS task analysis → new log signatures
Append three `mcu`-format signatures to `log_signatures.yaml` with teach strings: task
starvation (task watchdog triggered/expired), stack overflow
(`configCHECK_FOR_STACK_OVERFLOW` / `vApplicationStackOverflowHook`), deadlock (mutual mutex
wait). Patterns are specific enough not to match any existing fixture. Deterministic tests.

### M4 — Power-management sequencing → `low_power` template (checklist items only)
`POWER_SEQUENCE` logic is **left untouched** (zero eval risk). New
`packages/templates/low_power.v1.yaml` (goal_type `low_power`) adds the requested checklist
items: sleep-mode entry/exit, peripheral clock-gating before sleep, wakeup-source
configuration — plus a rail-sequencing item that *reuses* the existing `POWER_SEQUENCE` rule.

## Testing
- New `core/tests/test_full_coverage.py` covers every addition (signatures deterministic,
  rules PASS/FAIL/UNKNOWN, mentor flags, template load).
- Existing suite must stay green; only the two literal updates noted above.
