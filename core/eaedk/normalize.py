"""Front-door term normalization — the FIRST box in the mentor flow.

Fix OBVIOUS typos and aliases in the user's text BEFORE routing, so a misspelled domain term
('bootlaoder') still grounds and reaches the answer-shape contract instead of being declined as an
unknown subject. Deliberately CONSERVATIVE and meaning-preserving:

  1. an explicit alias / common-misspelling map (deterministic), and
  2. a single-edit (Damerau ≤ 1) correction toward a SHORT, curated list of DISTINCTIVE embedded
     terms that have no common-English near-neighbours (bootloader, firmware, peripheral, …), so an
     ordinary word like "schedule" is never rewritten to "scheduler".

Only the latest user message is normalized for ROUTING; the raw conversation history the model sees is
untouched, so this never rewrites what the user actually said in the transcript.
"""
from __future__ import annotations

import re

# 1) Explicit, safe aliases / common misspellings → canonical term (case-insensitive, whole word).
_ALIASES = {
    "bootlaoder": "bootloader", "bootloder": "bootloader", "bootloadr": "bootloader",
    "boatloader": "bootloader",
    "firmare": "firmware", "firwmare": "firmware", "fimware": "firmware",
    "perihperal": "peripheral", "periferal": "peripheral", "periphery": "peripheral",
    "regsiter": "register", "reigster": "register", "registor": "register",
    "intterupt": "interrupt", "interupt": "interrupt", "intrrupt": "interrupt",
    "yokto": "yocto", "yacto": "yocto", "bitbacke": "bitbake", "bitbak": "bitbake",
    "freetos": "freertos", "freertso": "freertos", "free-rtos": "freertos",
    "linekr": "linker", "toolchian": "toolchain", "datsheet": "datasheet",
    "i2x": "i2c", "iic": "i2c",
}

# Multi-word phrases that should collapse to one canonical token (case-insensitive).
_PHRASES = {"boot loader": "bootloader", "boot-loader": "bootloader"}

# 2) DISTINCTIVE embedded terms safe for single-edit correction — each is long and has no common
#    English word within one edit, so correcting toward it cannot corrupt an ordinary word.
_CANON_FUZZY = ("bootloader", "bootloaders", "firmware", "peripheral", "peripherals",
                "interrupt", "interrupts", "datasheet", "toolchain", "watchdog", "semaphore")

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")


def _damerau_le1(a: str, b: str) -> bool:
    """True iff the Damerau-Levenshtein distance between a and b is at most 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:                                            # substitution or adjacent transposition
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        if (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]):
            return True
        return False
    longer, shorter = (a, b) if la > lb else (b, a)         # one insertion / deletion
    i = j = 0
    skipped = False
    while i < len(longer) and j < len(shorter):
        if longer[i] == shorter[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            i += 1
    return True


def _correct(word: str) -> str | None:
    lw = word.lower()
    if lw in _ALIASES:
        return _ALIASES[lw]
    # Fuzzy correction only for a long-enough token that is not already a canonical term.
    if len(lw) >= 7 and lw not in _CANON_FUZZY:
        for c in _CANON_FUZZY:
            if _damerau_le1(lw, c):
                return c
    return None


def normalize_terms(text: str) -> str:
    """Return the text with obvious domain-term typos/aliases corrected. A no-op for clean input."""
    if not text:
        return text
    out = text
    for bad, good in _PHRASES.items():
        out = re.sub(re.escape(bad), good, out, flags=re.IGNORECASE)
    return _WORD.sub(lambda m: _correct(m.group(0)) or m.group(0), out)
