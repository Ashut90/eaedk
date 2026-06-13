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
# size: a number + K/M/G + a byte unit (KB, Kbytes, KiB, Mbyte, ...). Case-insensitive.
_SIZE = re.compile(r"\b(\d+)\s*([KMG])(?:i?B|bytes?)\b", re.IGNORECASE)
_CLOCK = re.compile(r"up to\s*(\d+)\s*MHz|(\d+)\s*MHz\s*(?:max|maximum)", re.IGNORECASE)
_CLOCK_CTX = re.compile(r"clock|frequency|sysclk|hclk|\bhsi\b|\bhse\b|\bpll\b|\bcpu\b|core",
                        re.IGNORECASE)
_FLASH_KW = re.compile(r"flash", re.IGNORECASE)
_RAM_KW = re.compile(r"\bs?ram\b", re.IGNORECASE)
_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*\s+[A-Z]")
_SENT = re.compile(r"(?<=[.;])\s+")
_WORD = re.compile(r"[A-Za-z]+")


def _bytes(num: str, unit: str) -> int:
    f = {"K": 1024, "M": 1024 * 1024, "G": 1024 ** 3}[unit.upper()]
    return int(num) * f


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


def _nearest_mem(text: str, lo: int, hi: int) -> str | None:
    """Which memory ('flash'|'ram') keyword is closest to the span [lo,hi) in ``text``, by absolute
    distance (before or after). None if neither appears. This stops a flash label earlier in the
    sentence from claiming a value that actually sits next to 'SRAM'."""
    mid = (lo + hi) / 2
    best_mem, best_dist = None, None
    for kw, mem in ((_FLASH_KW, "flash"), (_RAM_KW, "ram")):
        for m in kw.finditer(text):
            d = abs((m.start() + m.end()) / 2 - mid)
            if best_dist is None or d < best_dist:
                best_dist, best_mem = d, mem
    return best_mem


def _scan_sentence(cands: dict, sent: str, page: int, section: str | None) -> None:
    has_flash, has_ram = bool(_FLASH_KW.search(sent)), bool(_RAM_KW.search(sent))
    # A "table" line is mostly a label + hex (few words once the hex is removed) -> HIGH; prose -> MEDIUM.
    nohex = _HEX8.sub("", sent)
    is_table = bool(_HEX8.search(sent)) and len(_WORD.findall(nohex)) <= 3
    method, conf = ("table", "HIGH") if is_table else ("text", "MEDIUM")

    for m in _HEX8.finditer(sent):
        mem = _nearest_mem(sent, m.start(), m.end())
        if mem == "flash":
            _emit(cands, Candidate("MEMORY", "memmap", "flash_base", m.group(1),
                                   method, conf, page, section, sent))
        elif mem == "ram":
            _emit(cands, Candidate("MEMORY", "memmap", "ram_base", m.group(1),
                                   method, conf, page, section, sent))

    for sm in _SIZE.finditer(sent):
        mem = _nearest_mem(sent, sm.start(), sm.end())
        if mem == "flash" or (mem is None and has_flash and not has_ram):
            _emit(cands, Candidate("MEMORY", "memmap", "flash_bytes",
                                   str(_bytes(sm.group(1), sm.group(2))), "text", "MEDIUM",
                                   page, section, sent))
        elif mem == "ram" or (mem is None and has_ram and not has_flash):
            _emit(cands, Candidate("MEMORY", "memmap", "ram_bytes",
                                   str(_bytes(sm.group(1), sm.group(2))), "text", "MEDIUM",
                                   page, section, sent))

    if _CLOCK_CTX.search(sent):
        mc = _CLOCK.search(sent)
        if mc:
            mhz = mc.group(1) or mc.group(2)
            _emit(cands, Candidate("CLOCK", "clock", "sysclk_max_hz",
                                   str(int(mhz) * 1_000_000), "text", "MEDIUM", page, section, sent))


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
            for sent in _SENT.split(line):
                if sent.strip():
                    _scan_sentence(cands, sent.strip(), page.number, section)
    return list(cands.values())


# Keys the deterministic pass targets — used to decide what LLM-assist may attempt to fill.
TARGET_KEYS = ("flash_base", "flash_bytes", "ram_base", "ram_bytes", "sysclk_max_hz")
