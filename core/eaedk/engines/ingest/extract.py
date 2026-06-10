"""Pure deterministic extractors: datasheet page text -> fact candidates with provenance.

No PDF, no DB, no LLM — golden-testable on synthetic page text. Confidence is assigned by
method: a value sitting in a structured memory-map line (label + 0x........) is HIGH; a value
matched from prose ("512 Kbytes of Flash") is MEDIUM. Anything uncertain yields no candidate —
never a guess. (DDR/AC-timing tables are intentionally out of deterministic scope -> LLM/LOW.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CONF_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


@dataclass
class Page:
    number: int
    text: str


@dataclass
class Candidate:
    domain: str
    kind: str
    fact_key: str
    fact_value: str
    method: str          # table | text | llm
    confidence: str      # HIGH | MEDIUM | LOW
    page: int
    section: str | None
    snippet: str


_HEX8 = re.compile(r"\b(0x[0-9A-Fa-f]{6,8})\b")
_SIZE_FLASH = re.compile(r"(\d+)\s*([KM])\s*bytes?\s+(?:of\s+)?(?:embedded\s+|on-chip\s+)?flash",
                         re.IGNORECASE)
_SIZE_RAM = re.compile(r"(\d+)\s*([KM])\s*bytes?\s+(?:of\s+)?(?:s?ram)\b", re.IGNORECASE)
_CLOCK = re.compile(r"up to\s*(\d+)\s*MHz|(\d+)\s*MHz\s*(?:max|maximum)", re.IGNORECASE)
_CLOCK_CTX = re.compile(r"clock|frequency|sysclk|\bcpu\b|core", re.IGNORECASE)
_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*\s+[A-Z]")


def _bytes(num: str, unit: str) -> int:
    return int(num) * (1024 if unit.upper() == "K" else 1024 * 1024)


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if _HEADING.match(s):
        return True
    return s.isupper() and any(c.isalpha() for c in s)


def _emit(cands: dict, c: Candidate) -> None:
    """Keep the single best candidate per fact_key (highest confidence, first seen)."""
    cur = cands.get(c.fact_key)
    if cur is None or _CONF_RANK[c.confidence] > _CONF_RANK[cur.confidence]:
        cands[c.fact_key] = c


def extract_from_pages(pages: list[Page]) -> list[Candidate]:
    cands: dict[str, Candidate] = {}
    for page in pages:
        section = None
        for raw in page.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _is_heading(line):
                section = line
                continue
            low = line.lower()

            # structured memory-map lines: label + 0x........ -> HIGH (table-ish)
            hx = _HEX8.search(line)
            if hx:
                addr = hx.group(1)
                if "flash" in low:
                    _emit(cands, Candidate("MEMORY", "memmap", "flash_base", addr,
                                           "table", "HIGH", page.number, section, line))
                if "sram" in low or re.search(r"\bram\b", low):
                    _emit(cands, Candidate("MEMORY", "memmap", "ram_base", addr,
                                           "table", "HIGH", page.number, section, line))

            # prose sizes -> MEDIUM
            m = _SIZE_FLASH.search(line)
            if m:
                _emit(cands, Candidate("MEMORY", "memmap", "flash_bytes",
                                       str(_bytes(m.group(1), m.group(2))), "text", "MEDIUM",
                                       page.number, section, line))
            m = _SIZE_RAM.search(line)
            if m:
                _emit(cands, Candidate("MEMORY", "memmap", "ram_bytes",
                                       str(_bytes(m.group(1), m.group(2))), "text", "MEDIUM",
                                       page.number, section, line))

            # clock ceiling, only on clock-context lines -> MEDIUM
            if _CLOCK_CTX.search(line):
                mc = _CLOCK.search(line)
                if mc:
                    mhz = mc.group(1) or mc.group(2)
                    _emit(cands, Candidate("CLOCK", "clock", "sysclk_max_hz",
                                           str(int(mhz) * 1_000_000), "text", "MEDIUM",
                                           page.number, section, line))
    return list(cands.values())


# Keys the deterministic pass targets — used to decide what LLM-assist may attempt to fill.
TARGET_KEYS = ("flash_base", "flash_bytes", "ram_base", "ram_bytes", "sysclk_max_hz")
