# 12 — The Last Mile (v1.7.0-last-mile)

## Finding

The v1.6.0 beginner audit (a 6-CGPA EE grad, $2 Blue Pill, no terminal experience, no
Googling) proved EAEDK gets a clueless beginner **~80% of the way** — excellent mentor, a goal
picker that carries them past jargon, and a genuinely strong `export` that hands them correct,
commented firmware with the linker script auto-filled. Then it drops them at the **single
hardest cliff**: touching real silicon.

The failure is **narrow and concentrated in two places**, plus four smaller papercuts that make
a nervous beginner quit *earlier*:

1. **Build cliff** — `START_HERE.md` says `cmake -B build …`, but the beginner has no
   `arm-none-eabi-gcc`/`cmake`/`openocd` installed. The tool *names* the missing tool (via
   `toolchain validate`) but never tells them **how to get it**. "Install an ARM cross-compiler"
   with no command and no terminal experience = board back in the drawer.
2. **Flash cliff** — `FLASH.md` emits `openocd -f interface/<probe>.cfg -f target/<target>.cfg`
   with **unfilled placeholders** for the single most common beginner setup on Earth
   (Blue Pill + ST-Link clone). EAEDK knew the chip down to the byte for the linker, but punts
   on the one command that puts code on it.
3. `--llm` only works **before** the subcommand; the hint the tool prints
   (`add --llm for a conversational answer`) produces an argparse usage wall when followed
   literally. Did-I-break-it panic.
4. `validate` shows **11 UNKNOWNs + 13 "Missing Information"** lines and then says
   **"Validation clean."** A beginner reads 11 errors and a contradiction; no single clear
   "you're fine, do this next."
5. The goal prompt **aborts on Enter** instead of honoring the obvious "start here" default —
   violating the v1.6.0 promise that a knowable default never loops/aborts on Enter.
6. `log analyze` explains a HardFault but stops at "decode CFSR / inspect the stacked PC/LR" —
   no **concrete command** a beginner can actually run.

> **Mandate:** close the last 20%. Don't name the missing tool — tell them how to get it.
> Don't print placeholders for the most common setup — fill them in.

## The six fixes

### Fix 1 — Host-OS-aware install commands in START_HERE.md
Detect the host OS at export time (`platform.system()`, passed into the pure generator so it
stays golden-testable) and emit the concrete install command for the project's required tools:

- **Linux (Debian/Ubuntu):** `sudo apt install gcc-arm-none-eabi cmake openocd`
- **macOS:** `brew install arm-none-eabi-gcc cmake openocd`
- **Windows:** link to the ARM GNU Toolchain installer + CMake + OpenOCD downloads.

Package names are mapped per tool per OS; a tool with no clean package degrades to a labelled
link, never a guess. The required tool list comes from the board's toolchain profile (compiler /
build_system / flash_tool), so it's correct for AVR (`gcc-avr`) and ESP32 too, not Blue-Pill-only.

### Fix 2 — Real flash command from a seeded probe→config map
Seed two small, diffable tables (forward-only migration `0009`, loaded from YAML like every
other seed; **no raw SQL outside repo/seed**):

- `debug_probes(name, interface_cfg, summary)` — ST-Link, J-Link, CMSIS-DAP, Raspberry Pi Pico
  (picoprobe). These are the OpenOCD `interface/*.cfg` files.
- `soc_flash_profiles(soc_name, openocd_target, default_probe)` — the OpenOCD `target/*.cfg`
  per SoC (e.g. STM32F103C8 → `target/stm32f1x.cfg`, default probe `st-link`) and the probe a
  beginner most likely has for that board.

`export.gather` joins the project's board → SoC → flash profile → default probe's interface cfg
and passes a `flash_profile` dict into the (still pure) `render_flash`. For an OpenOCD board with
a known profile it emits the **filled** command:

```
openocd -f interface/stlink.cfg -f target/stm32f1x.cfg \
        -c "program build/<project>.elf verify reset exit"
```

…plus a short "other probes" table from the seeded map. Unknown SoC/probe still degrades to the
honest placeholder — the truth hierarchy is preserved (we never invent a target cfg).

### Fix 3 — Accept `--llm` after the subcommand
Add `--llm`/`--no-llm` (same `dest="llm"`, `default=argparse.SUPPRESS`) to every subparser that
consumes it (`ask`, `explain`, `mentor`, `ingest`, `log analyze`). SUPPRESS means the subparser
only sets the flag when explicitly passed after the subcommand, never clobbering the global
default. The printed hint and what actually works now match.

### Fix 4 — Validate leads with one clear status
In `AssessResponse.to_markdown`, immediately after the verdict, print a single plain-language
status line. When `feasible` with non-blocking unknowns present:

> ✅ **Your project is feasible and ready to export.** The items below are optional — you do not
> need them to blink an LED. Next: `eaedk export`.

And change the orchestrator's feasible `next_step` so it never says "Validation clean" while
unknowns are listed — it points at `export` and frames the unknowns as optional.

### Fix 5 — Goal prompt Enter = default 1
`_select_goal`: a blank answer returns the first ordered goal (`bare_metal_app`, the "start here"
option). Prompt becomes `Goal [1-N, Enter = 1 "start here"]:`. Honors the v1.6.0 default promise.

### Fix 6 — Log analyze emits a concrete next action
After a Cortex-M fault match (HardFault/CFSR), extract the crash address (`PC=0x…`) from the log
and append one ready-to-run line:

> Find the crash location with: `arm-none-eabi-addr2line -e build/<project>.elf 0x08000abc`

Pure helper (`crash_locate_hint`), gated on an actual MCU fault + a real address; degrades to
nothing when there's no address to act on. Project name fills the `.elf`; absent a project it
uses `build/<your-firmware>.elf`.

## Verification
- Golden cases per fix in `core/tests/test_last_mile.py`; no raw SQL.
- Full pytest suite green, eval 11/11, and a re-run of the beginner build/flash path showing the
  filled install + flash commands.
