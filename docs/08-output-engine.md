# EAEDK — Engineering Output Engine (v1.1.0)

**Date:** 2026-06-11
**Command:** `eaedk export <project> [--out DIR] [--only checklist|cmake|flash] [--force]`

Turns a validated project into **real files**, not CLI text. When a project passes feasibility,
the engineer can export structured deliverables and start building.

## What it generates

```
<project>-bringup/
  BRINGUP_CHECKLIST.md     template checklist + validation + facts/assumptions/unknowns + risks
  CMakeLists.txt           project scaffold (configure with the toolchain file)
  cmake/toolchain.cmake    cross-compiler + -mcpu flags from the board's arch + toolchain profile
  linker/memory.ld         MCU only: MEMORY regions from the VERIFIED board geometry
  src/main.c               MCU only: minimal bare-metal entry stub
  FLASH.md                 board-aware flashing commands (from the board's flash-tool profile)
```

- **MCU vs SoC aware.** Cortex-M projects get a full bare-metal scaffold (linker MEMORY from
  the typed flash/RAM columns, `-mcpu=cortex-m4 -mthumb`, etc.). Linux/SoC projects get a
  cross-toolchain scaffold with a note that the on-target build uses U-Boot/kernel/SDK.
- **Flash instructions** are picked from the board's `flash_tool` profile (openocd / st-flash /
  esptool / picotool …) and include the verified flash base.

## Feasibility-gated

Export is **refused** unless `feasibility == feasible` — you can't ship a scaffold for a design
the engines have flagged as infeasible. The refusal lists the gating blockers and exits non-zero.
`--force` emits a **DRAFT** with a warning banner in every file. This composes with the toolchain
engine: on a host with no `arm-none-eabi-gcc`, exporting a Cortex-M project is refused on
`TOOLCHAIN_TARGET_TRIPLE`.

## Truth hierarchy carries into artifacts

The generators never invent a value. Known geometry is emitted verbatim
(`ORIGIN = 0x08000000, LENGTH = 524288`); anything missing from the database becomes an explicit
`<UNKNOWN ...>` placeholder with a "fill from the datasheet/TRM" note — never a guess. (Verified
against RTL8722DM, whose memory map is intentionally NULL in the seed.) Likewise FLASH.md uses
`<probe>/<target>` placeholders rather than fabricating an OpenOCD config name.

## Design

- `engines/output/generators.py` — pure render functions (golden-testable, no I/O).
- `engines/output/export.py` — `gather()` (via repo + orchestrator) → feasibility gate → write
  files. Reads through repo helpers; writes files only; no raw SQL.

## Verification

- `pytest` → **62 passed** (8 new: cpu-flag/mcu detection, CMake+linker from verified data,
  UNKNOWN placeholders, board-aware flash, refusal-when-not-feasible, write-when-feasible,
  force-draft, only-filter).
- `eval run` → **11/11**.
- Live: STM32F411RE bootloader exported 6 real files; after `toolchain detect` (host x86_64 gcc
  only) the same export was refused on the toolchain triple, and `--force` produced a DRAFT.
