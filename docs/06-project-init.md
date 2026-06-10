# EAEDK — Interactive Project Init

**Date:** 2026-06-10
**Command:** `eaedk project init`

Completes the onboarding chain: **board add → project init → assess → log analyze → resolve.**

## Guided flow

1. **Project name** (unique).
2. **Board** — pick from onboarded boards (by number or name). Aborts with guidance if none
   are onboarded yet.
3. **Goal type** — menu of the available templates plus *Custom*:
   bootloader / U-Boot / Linux / OTA / driver, or a custom (template-less) goal.
4. **Auto-template** — the matching template is selected and its checklist seeded
   (`repo.create_project`). Custom goals create a template-less project; global validation
   rules still apply.
5. **Immediate assessment** — runs `assess_project` on creation and prints feasibility, every
   `FAIL`, every engaged `UNKNOWN`, and the open risks *before any code is written*.
6. **Blockers banner** — if any **HIGH-severity** rule is `FAIL` or engaged-`UNKNOWN`, an
   explicit `⚠ YOU HAVE BLOCKERS` summary is printed. Not a hard stop — but the engineer
   cannot miss it.

Writes through repo helpers only — no raw SQL.

## Example

`project init` → STM32MP157 → U-Boot bring-up auto-selects `U-Boot Bring-Up@v1`, reports
`feasibility: BLOCKED`, lists `DDR_TIMING_VERIFIED [UNKNOWN]` and the `DDR_GUESSED` risk, and
raises the blockers banner — surfacing the DDR verification gap at minute zero.

## Verification

- `pytest` → **42 passed** (5 new: uboot-blockers, bootloader-clean, custom-templateless,
  no-boards-abort, duplicate-name-reject).
- `eval run` → **11/11**.
