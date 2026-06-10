"""Toolchain validation — cross-reference detected tools against a board's required profile.

`validate_toolchain` is a PURE function (detected components + board reqs in, ValidationResults
out) so PASS/FAIL/UNKNOWN is golden-testable without a host or DB. `toolchain_checks` is the
DB-backed wrapper the orchestrator calls.

Feasibility contribution (per spec): only HIGH-severity issues (missing compiler / wrong target
triple / compiler too old) gate feasibility — they `gating=True`. Debugger/build-system/SDK
issues are MEDIUM/LOW: surfaced with full PASS/FAIL/UNKNOWN detail but `gating=False`, so they
inform without declaring the whole design infeasible.
"""
from __future__ import annotations

import re
from typing import Any

from ..validation.rules import ValidationResult, PASS, FAIL, UNKNOWN

# Actionable install hints for the teach layer (beginner-mentor first step).
_INSTALL = {
    "arm-none-eabi-gcc": "apt install gcc-arm-none-eabi",
    "aarch64-linux-gnu-gcc": "apt install gcc-aarch64-linux-gnu",
    "arm-linux-gnueabihf-gcc": "apt install gcc-arm-linux-gnueabihf",
    "xtensa-esp32-elf-gcc": "install ESP-IDF (it ships the Xtensa toolchain)",
    "riscv64-unknown-elf-gcc": "apt install gcc-riscv64-unknown-elf",
    "openocd": "apt install openocd",
    "st-flash": "install stlink-tools (apt install stlink-tools)",
    "cmake": "apt install cmake",
    "make": "apt install build-essential",
    "meson": "pip install meson",
    "ninja": "apt install ninja-build",
    "pioasm": "build pioasm from the Raspberry Pi Pico SDK",
    "idf.py": "install ESP-IDF and source its export.sh",
    "west": "pip install west (Zephyr)",
}


def _machine(triple: str | None) -> str | None:
    return triple.split("-")[0] if triple else None


def _ver(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def _below_min(version: str | None, minimum: str | None) -> bool:
    if not version or not minimum:
        return False
    return _ver(version) < _ver(minimum)


def _teach(req: dict[str, Any]) -> str:
    why = (req.get("why") or "").strip()
    fix = _INSTALL.get(req["name"], f"install {req['name']} and add it to PATH")
    return (f"{why}  Fix: {fix}." if why else f"Fix: {fix}.")


def _not_detected(req: dict[str, Any], check: str) -> ValidationResult:
    return ValidationResult(
        check=check, status=UNKNOWN,
        reason=f"required {req['kind']} '{req['name']}' not checked — run `eaedk toolchain detect`",
        severity_on_fail=req["severity"], engaged=False, gating=False,
        teach="Run `eaedk toolchain detect` to inventory your build environment first.")


def _check_compiler(req: dict[str, Any], compilers: list[dict]) -> ValidationResult:
    sev = req["severity"]
    gating = sev == "HIGH"
    req_machine = _machine(req.get("target_triple"))
    exact = next((c for c in compilers if c["name"] == req["name"]), None)
    by_machine = next((c for c in compilers
                       if _machine(c.get("target_triple")) == req_machine), None)
    chosen = exact or by_machine
    if chosen and _machine(chosen.get("target_triple")) == req_machine:
        if _below_min(chosen.get("version"), req.get("min_version")):
            return ValidationResult(
                "TOOLCHAIN_COMPILER", FAIL,
                f"{chosen['name']} {chosen.get('version')} is below the required "
                f"{req['min_version']}", sev, engaged=True, gating=gating, teach=_teach(req))
        return ValidationResult(
            "TOOLCHAIN_COMPILER", PASS,
            f"{chosen['name']} {chosen.get('version') or 'present'} "
            f"({chosen.get('target_triple')}) matches board", sev, gating=gating)
    if compilers:
        machines = sorted({_machine(c.get("target_triple")) or "?" for c in compilers})
        return ValidationResult(
            "TOOLCHAIN_TARGET_TRIPLE", FAIL,
            f"detected compiler target(s) {machines} do not match the board's "
            f"{req_machine} ({req.get('target_triple')})", sev, engaged=True, gating=gating,
            teach=_teach(req))
    return ValidationResult(
        "TOOLCHAIN_COMPILER", UNKNOWN,
        f"required compiler '{req['name']}' not found on host", sev,
        engaged=True, gating=gating, teach=_teach(req))


def _check_tool(req: dict[str, Any], detected: list[dict]) -> ValidationResult:
    sev = req["severity"]
    gating = sev == "HIGH"
    check = f"TOOLCHAIN_{req['kind'].upper()}"
    found = next((d for d in detected
                  if d["name"] == req["name"] and d["kind"] == req["kind"]), None)
    if found is None:  # accept the tool under any kind (openocd is debugger + flash_tool)
        found = next((d for d in detected if d["name"] == req["name"]), None)
    if found is None:
        return ValidationResult(
            check, UNKNOWN, f"required {req['kind']} '{req['name']}' not found on host",
            sev, engaged=gating, gating=gating, teach=_teach(req))
    if _below_min(found.get("version"), req.get("min_version")):
        return ValidationResult(
            check, FAIL,
            f"{req['name']} {found.get('version')} is below the required {req['min_version']}",
            sev, engaged=True, gating=gating, teach=_teach(req))
    return ValidationResult(
        check, PASS, f"{req['name']} {found.get('version') or 'present'} OK", sev, gating=gating)


def validate_toolchain(detected: list[dict], reqs: list[dict], board_arch: str,
                       goal_type: str, detection_ran: bool) -> list[ValidationResult]:
    """Pure cross-reference of detected toolchain vs the board's required profile."""
    compilers = [d for d in detected if d["kind"] == "compiler"]
    results: list[ValidationResult] = []
    for req in reqs:
        if not detection_ran:
            check = ("TOOLCHAIN_COMPILER" if req["kind"] == "compiler"
                     else f"TOOLCHAIN_{req['kind'].upper()}")
            results.append(_not_detected(req, check))
            continue
        if req["kind"] == "compiler":
            results.append(_check_compiler(req, compilers))
        else:
            results.append(_check_tool(req, detected))
    return results


# 64-bit ARM cores (ARMv8-A) need aarch64; 32-bit ARM Cortex-A use arm-linux.
_AARCH64 = ("a53", "a55", "a57", "a72", "a73", "a76", "a78", "a35", "x1")


def arch_default_reqs(arch: str) -> list[dict[str, Any]]:
    """Infer a sensible toolchain profile from the SoC architecture, so a board onboarded by
    an engineer (with no seeded profile) still gets toolchain validation + the teach layer."""
    a = (arch or "").lower()
    note = "(inferred from architecture — confirm against your SDK)"
    if a.startswith("arm-cortex-m"):
        return [
            {"kind": "compiler", "name": "arm-none-eabi-gcc", "target_triple": "arm-none-eabi",
             "min_version": None, "severity": "HIGH",
             "why": f"Cortex-M is bare-metal ARM (Thumb); the host gcc cannot build firmware "
                    f"for this MCU. {note}"},
            {"kind": "build_system", "name": "cmake", "target_triple": None, "min_version": None,
             "severity": "MEDIUM", "why": f"Most Cortex-M SDKs build with CMake. {note}"}]
    if a.startswith("arm-cortex-a"):
        is64 = any(c in a for c in _AARCH64)
        triple = "aarch64-linux-gnu" if is64 else "arm-linux-gnueabihf"
        name = ("aarch64-linux-gnu-gcc" if is64 else "arm-linux-gnueabihf-gcc")
        bits = "64-bit (ARMv8-A)" if is64 else "32-bit (ARMv7-A)"
        return [
            {"kind": "compiler", "name": name, "target_triple": triple, "min_version": None,
             "severity": "HIGH",
             "why": f"{bits} ARM Linux SoC; needs the matching cross-compiler, not host gcc. {note}"},
            {"kind": "build_system", "name": "make", "target_triple": None, "min_version": None,
             "severity": "MEDIUM", "why": f"U-Boot and the kernel build with make. {note}"}]
    if a.startswith("xtensa"):
        return [
            {"kind": "compiler", "name": "xtensa-esp32-elf-gcc", "target_triple": "xtensa-esp32-elf",
             "min_version": None, "severity": "HIGH",
             "why": f"Xtensa core; only the ESP-IDF toolchain can target it. {note}"}]
    if a.startswith("riscv"):
        return [
            {"kind": "compiler", "name": "riscv64-unknown-elf-gcc",
             "target_triple": "riscv64-unknown-elf", "min_version": None, "severity": "HIGH",
             "why": f"RISC-V core; needs a riscv cross-compiler. {note}"}]
    return []


def toolchain_checks(conn, board_name: str | None, soc: dict | None,
                     goal_type: str) -> list[ValidationResult]:
    """DB-backed wrapper: load detected components + the board's required profile, validate.
    If the board has no seeded profile, fall back to an arch-default profile so the teach
    layer still fires for engineer-onboarded boards."""
    from ... import repo
    if not board_name:
        return []
    board_arch = soc["arch"] if soc else ""
    reqs = [dict(r) for r in repo.load_board_toolchain_reqs(conn, board_name)]
    if not reqs:
        reqs = arch_default_reqs(board_arch)
    if not reqs:
        return []
    detected = [dict(r) for r in repo.load_toolchain(conn)]
    return validate_toolchain(detected, reqs, board_arch, goal_type, detection_ran=bool(detected))
