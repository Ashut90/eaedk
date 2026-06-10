# EAEDK — Toolchain Engine (v1.0.0)

**Date:** 2026-06-11
**Commands:** `eaedk toolchain detect`, `eaedk toolchain validate --project <name>`

The problem it solves: EAEDK could tell you your DDR timing was unverified, but not that your
host has no `arm-none-eabi-gcc` to build the firmware. **Most bring-up failures are environment
failures, not code failures.** The Toolchain Engine makes the build environment a first-class,
validated entity that contributes to feasibility exactly like a hardware fact.

## Flow

```
toolchain detect ──► toolchain_components (SQLite)
                         │
project board profile ──►│ (board_toolchain_reqs, seeded per board/arch)
                         ▼
            validate_toolchain (pure)  ──►  PASS / FAIL / UNKNOWN per component
                         │
                         ▼
            merged into assess_project()  ──►  feasibility (HIGH issues gate)
```

- **`toolchain detect`** probes the host (injectable `which`/`runner`, so it's unit-tested
  without real tools): compilers (+ version + `-dumpmachine` triple), debugger/probe (OpenOCD,
  ST-Link, J-Link), flash tools, build systems (CMake/Make/Meson/Ninja), SDK markers
  (`idf.py`, `west`, `pioasm`). Results stored via `repo.replace_toolchain()` — no raw SQL.
- **`toolchain validate --project`** cross-references stored detection against the board's
  required profile and reports per-component status with a teach line.
- **Detection is explicit and separate.** `assess_project` reads *stored* results — it never
  probes the host. Before any `detect`, toolchain checks are non-gating UNKNOWN ("run detect").

## Status × severity × feasibility

| Situation | Status | Severity | Gates feasibility? |
|---|---|---|---|
| Required compiler not found | UNKNOWN (engaged) | HIGH | **yes → BLOCKED** (like unverified DDR) |
| Compiler target triple ≠ board arch | FAIL | HIGH | **yes → NOT FEASIBLE** |
| Compiler below min version | FAIL | HIGH | yes |
| Debugger/flash version below min | FAIL | MEDIUM | no (surfaced, non-gating) |
| Build system missing | UNKNOWN | MEDIUM | no |
| SDK not detected | UNKNOWN | LOW | no |

Implemented with a backward-compatible `gating` flag on `ValidationResult` (defaults `True`, so
existing rules are unchanged — `feasibility()` now ignores non-gating results). Only HIGH
toolchain issues gate; MEDIUM/LOW inform without declaring the whole design infeasible.

## Teach layer (first step toward the mentor vision)

Every non-PASS toolchain check carries a one-line `teach`: *why it matters* + *what to do*,
e.g. on this host (no `arm-none-eabi-gcc`):

```
FAIL [HIGH] TOOLCHAIN_TARGET_TRIPLE: detected compiler target(s) ['x86_64'] do not match arm
   ↳ Cortex-M is bare-metal ARM (Thumb); the host gcc cannot produce firmware for this MCU.
     Fix: apt install gcc-arm-none-eabi.
```

The teach line shows in both `toolchain validate` and `eaedk validate` (mentor layer surfaced
everywhere).

## Seed profiles

All 9 seed boards carry a toolchain profile keyed to their architecture: Cortex-M →
`arm-none-eabi-gcc`; 32-bit ARM Linux (MP157, AM335x) → `arm-linux-gnueabihf-gcc`; 64-bit
(i.MX8M, BCM2711) → `aarch64-linux-gnu-gcc`; ESP32 → `xtensa-esp32-elf-gcc` + ESP-IDF; RP2040
adds `pioasm`.

## Verification

- `pytest` → **54 passed** (12 new: detection parsing, golden PASS/FAIL/UNKNOWN, and four
  assess_project feasibility integrations).
- `eval run` → **11/11**.
- Live on this host: `gcc 13.3.0 (x86_64)` only → STM32F411RE bootloader is `NOT FEASIBLE`
  on `TOOLCHAIN_TARGET_TRIPLE`, with the fix command surfaced.
