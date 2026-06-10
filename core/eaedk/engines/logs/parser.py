"""Deterministic log parsing: format detection, signature matching, crash-window slicing.

All pure functions — no DB, no LLM. The engine (engine.py) orchestrates these and only
falls back to the LLM when nothing matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# dmesg/kernel lines are prefixed with a monotonic timestamp like "[    1.234567]".
_DMESG_TS = re.compile(r"^\[\s*\d+\.\d+\]")
_UBOOT_HINTS = re.compile(
    r"^U-Boot(\s+SPL)?\s+20\d\d|Hit any key to stop autoboot|"
    r"^=>\s|Loading Environment|^Net:\s|^DRAM:\s", re.MULTILINE)
# Bare-metal / RTOS MCU output: ESP32 boot ROM + panics, and Cortex-M fault dumps.
_MCU_HINTS = re.compile(
    r"Guru Meditation|HardFault|Hard Fault|HFSR|CFSR|EXCVADDR|EXCCAUSE|"
    r"rst:0x|^ets\s|SPI_FAST_FLASH_BOOT|panic'ed", re.IGNORECASE | re.MULTILINE)

# Words that mark the failure point ("crash vector") to centre the context window on.
_CRASH = re.compile(
    r"panic|oops|abort|segfault|fault|unable to|bad (data|magic|crc|header)|"
    r"error|fail|timed?\s*out|cannot|did not respond|BUG", re.IGNORECASE)


@dataclass
class SignatureMatch:
    signature_id: int | None
    format: str
    line_no: int            # 1-based
    line: str
    cause: str
    fix: str
    severity: str


def detect_format(text: str) -> str:
    """Return 'dmesg', 'uboot', 'mcu', or 'unknown' from line heuristics."""
    lines = text.splitlines()
    dmesg = sum(1 for ln in lines if _DMESG_TS.match(ln))
    uboot = len(_UBOOT_HINTS.findall(text))
    mcu = len(_MCU_HINTS.findall(text))
    if dmesg >= 3 and dmesg >= uboot and dmesg >= mcu:
        return "dmesg"
    if uboot >= 1 and uboot > dmesg and uboot >= mcu:
        return "uboot"
    if mcu >= 1:                       # bare-metal MCU / ESP32 — the beginner's world
        return "mcu"
    if dmesg > 0:
        return "dmesg"
    return "unknown"


def match_signatures(text: str, fmt: str, signatures) -> list[SignatureMatch]:
    """Match each signature (whose format is compatible) against the log; first hit per sig.

    ``signatures`` is an iterable of rows/objects with: id, format, pattern_regex, cause,
    fix, severity.
    """
    lines = text.splitlines()
    out: list[SignatureMatch] = []
    for sig in signatures:
        sig_fmt = sig["format"] if isinstance(sig, dict) else sig.format
        if sig_fmt not in (fmt, "any") and fmt != "unknown":
            continue
        pattern = sig["pattern_regex"] if isinstance(sig, dict) else sig.pattern_regex
        try:
            rx = re.compile(pattern)
        except re.error:
            continue
        for i, ln in enumerate(lines):
            if rx.search(ln):
                out.append(SignatureMatch(
                    signature_id=(sig["id"] if isinstance(sig, dict) else sig.id),
                    format=sig_fmt, line_no=i + 1, line=ln.strip(),
                    cause=(sig["cause"] if isinstance(sig, dict) else sig.cause),
                    fix=(sig["fix"] if isinstance(sig, dict) else sig.fix),
                    severity=(sig["severity"] if isinstance(sig, dict) else sig.severity)))
                break
    return out


def crash_window(text: str, size: int = 100) -> tuple[int, list[str], int]:
    """Return (start_line_1based, window_lines, crash_line_1based).

    Centres a strict ``size``-line window on the last crash-keyword line; if none, takes the
    last ``size`` lines.
    """
    lines = text.splitlines()
    n = len(lines)
    crash_idx = next((i for i in range(n - 1, -1, -1) if _CRASH.search(lines[i])), n - 1)
    half = size // 2
    start = max(0, crash_idx - half)
    end = min(n, start + size)
    start = max(0, end - size)
    return start + 1, lines[start:end], crash_idx + 1
