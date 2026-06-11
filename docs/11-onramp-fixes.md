# EAEDK — On-Ramp Fixes (v1.6.0) — Findings & Design

**Status:** Doc-first (mandate), before code. Builds on v1.5.0. Pure data / seeding / error
handling — the engines and the trust hierarchy are unchanged.

## The finding (from the zero-experience dogfood)

The mentor layer, learning path, validate-teach, and export all depend on **board geometry +
capabilities being in the database.** The only way to add a new board is the wizard, which:
1. demands expert knowledge (core type, flash base, RAM size) a beginner cannot have,
2. **crashes with a Python traceback** on EOF/invalid input, and
3. on an unknown board name just says "unknown board" — a dead end.

So *every* beginner with *any* board not already seeded hits the same three walls. This is
structural, not a Blue-Pill problem. The fix is to remove the data-entry burden for common
boards and make every dead end a guided path.

## Fix 1 — Seed the common beginner boards (full geometry + capabilities)

New seed boards, each with flash/RAM geometry, arch, a toolchain profile, and capabilities
(incl. the universal `gpio` + `timer`):
`STM32F103-BluePill`, `Raspberry-Pi-Pico`, `Arduino-Uno`, `Arduino-Mega`, `Nucleo-F103RB`.
Already seeded boards (`Nucleo-F411RE`, `ESP32-DevKitC`, WIZnet Pico) get `gpio`/`timer` added.
A beginner who owns any of these never touches the wizard.

## Fix 2 — The wizard must never crash

- `cmd_board_add` / `project init` wrap the wizard in `try/except (EOFError, KeyboardInterrupt)`
  → a plain "onboarding cancelled" message, never a traceback.
- The architecture prompt accepts **blank** (no infinite loop): it records the arch as unknown
  with a note, instead of re-prompting forever. Invalid input re-prompts with a plain hint.

## Fix 3 — Unknown board offers a path forward

A shared helper: when any command gets an unknown board name, look for a **near-match** in the
seed DB (token overlap) and suggest it; if none, print
*"not in the database yet — run `eaedk board add --interactive` to onboard it, or
`eaedk ingest --file <datasheet.pdf> --board <name>` to extract its facts."* Wired into `mentor`,
`board show`, `project new`.

## Fix 4 — Export must offer standard values for a known SoC

New `soc_defaults` table (seeded YAML: SoC name → standard flash/RAM geometry). When export is
blocked by `no_geometry` **and the board's SoC is recognized**, the refusal names the standard
values and offers a one-command fix:
*"For STM32F103C8 the standard values are 64 KB flash @ 0x08000000, 20 KB RAM @ 0x20000000 —
apply with `eaedk board fill-geometry <board>`, then re-export."*
New command `eaedk board fill-geometry <board>` applies the SoC defaults to the typed columns
(via a repo helper). A beginner reaches starter code without a datasheet. (Non-interactive +
script-safe; one extra command rather than an in-export Y/n prompt.)

## Fix 5 — The wizard collects capabilities

The wizard asks, in plain language, what the board can do — a checklist of UART, SPI, I2C,
Timers, GPIO, USB, Wi-Fi, Bluetooth. **Default = the common MCU set** (UART/SPI/I2C/GPIO/Timer)
so a beginner who just presses Enter still gets a non-empty capability map (and a non-empty
learning path). Stored via a `repo.add_board_capability` write-through.

## Fix 6 — The learning path never silently drops steps

`mentor` shows the satisfied steps in order **and** lists the steps it filtered out, with why
and the fix: *"UART Logger — requires `uart`; add it with
`eaedk board capability add <board> uart`."* New command `board capability add`. Silent removal
is worse than no filter.

## Verification (golden cases per fix)

- Fix 1: each seeded beginner board has geometry + capabilities; `mentor` shows a full path.
- Fix 2: feeding EOF / an exhausted input to the wizard returns gracefully (no exception escapes).
- Fix 3: an unknown near-match name suggests the seeded board; a true unknown gives the path.
- Fix 4: `soc_defaults_for` resolves STM32F103C8; `fill-geometry` makes export succeed.
- Fix 5: a wizard run with default capabilities yields a non-empty capability map.
- Fix 6: a board missing `uart` shows UART Logger as a dropped step with the add command.

Feature branch `feature/onramp`, tag `v1.6.0-onramp`. No raw SQL outside repo helpers.
