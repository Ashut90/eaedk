"""Full-datasheet digest: read EVERY page, pull the salient facts deterministically, then have the
model synthesise "what an engineer must remember" — grounded ONLY in what was extracted.

Why this shape (not "feed 200 pages to the model"): a 200-page datasheet is ~150k+ tokens and does
not fit any local model's context. So the DETERMINISTIC scan does the reading (all pages, cheap,
exhaustive) and pulls the fact-bearing lines — specs (number+unit), absolute-maximum / caution
notes, memory & clock values, the peripheral set, and the section map. The model only ever sees that
distilled, page-cited set, so it fits context and the summary is grounded (it can quote a page, and
it cannot invent numbers that were never extracted). The critical geometry (flash/RAM/clock) is the
same HIGH/MEDIUM candidate extraction the ingest engine already trusts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Page, extract_from_pages

# ── Salient-line detectors (deterministic) ───────────────────────────────────────────────────────
_UNIT = re.compile(
    r"\b\d[\d.,]*\s?(?:V|mV|kV|uV|µV|mA|uA|µA|nA|A|MHz|kHz|GHz|Hz|KiB|MiB|GiB|KB|MB|GB|"
    r"Kbytes?|Mbytes?|Gbytes?|bytes?|bits?|-bit|ns|µs|us|ms|dBm|Mbps|kbps|Kbps|baud|"
    r"°C|degC|Ω|ohm|kΩ|pF|nF|µF|uF|mW|W|MIPS|DMIPS)\b", re.IGNORECASE)
_CAUTION = re.compile(
    r"\b(note|caution|warning|important|attention|errata|restriction|limitation|must not|"
    r"should not|do not|shall not|do NOT|reserved|absolute maximum|exceeding|damage)\b",
    re.IGNORECASE)
_HEX = re.compile(r"\b0x[0-9A-Fa-f]{4,8}\b")
# Identity: the core/architecture and part family. Captured explicitly so the model never GUESSES the
# core (an un-grounded "Cortex-M4" for an M3 part is exactly the hallucination this prevents).
_IDENT = re.compile(
    r"\b(Cortex-[MAR]\d+\+?|ARM\d+|RISC-V|Thumb-2|\bMCU\b|microcontroller|microprocessor|"
    r"STM32\w+|nRF\d+\w*|ATmega\d+\w*|ATtiny\d+\w*|ESP32\w*|ESP8266|RP2040|MSP430\w*|"
    r"SAM[DESGL]\d+\w*|PIC\d+\w*|Kinetis|i\.MX\w*|AVR|8051)\b", re.IGNORECASE)

# Category keyword sets (checked in order; first match wins so a caution about power stays a caution).
_CATS: tuple[tuple[str, re.Pattern], ...] = (
    ("Identity & core", _IDENT),
    ("Cautions & limits", _CAUTION),
    ("Memory & map", re.compile(r"\b(flash|sram|\bram\b|eeprom|memory map|0x[0-9A-Fa-f]{4,}|"
                                r"\bKB\b|\bMB\b|kbytes?|mbytes?|address)\b", re.IGNORECASE)),
    ("Clocks & timing", re.compile(r"\b(clock|sysclk|hclk|pll|oscillator|\bhsi\b|\bhse\b|MHz|kHz|"
                                   r"GHz|frequency|baud|prescaler)\b", re.IGNORECASE)),
    ("Power & electrical", re.compile(r"\b(voltage|current|supply|\bvdd\b|\bvss\b|consumption|"
                                      r"\bmA\b|\buA\b|µA|power|°C|degC|temperature|brown-?out|LDO)\b",
                                      re.IGNORECASE)),
    ("Peripherals", re.compile(r"\b(UART|USART|SPI|I2C|I²C|CAN\b|USB|OTG|ADC|DAC|DMA|TIMER|TIM\d|"
                               r"PWM|RTC|GPIO|Ethernet|MAC|SDIO|QSPI|I2S|comparator|watchdog|"
                               r"WWDG|IWDG|CRC|RNG)\b", re.IGNORECASE)),
)

_PERIPH = re.compile(r"\b(USART|UART|SPI|I2C|I²C|CAN|USB|OTG|ADC|DAC|DMA|TIM\d+|TIMER|PWM|RTC|GPIO|"
                     r"Ethernet|SDIO|QSPI|I2S|WWDG|IWDG|CRC|RNG|comparator|op-?amp|DFSDM)\b",
                     re.IGNORECASE)

# Noise that must NOT be treated as a fact: table-of-contents dot leaders, figure/table captions, and
# mojibake (garbled PDF extraction from figure/diagram pages).
_DOTS = re.compile(r"\.\s*\.\s*\.")                        # "... ... ..." → a TOC / list-of-figures line
_TOC_TAIL = re.compile(r"\.{2,}\s*\d{1,3}\s*$")           # "…………… 61"
_FIGTAB = re.compile(r"^\s*(Figure|Table)\s+\d+\b", re.I)
# A real section heading: numbered ("6.3.2 Absolute maximum ratings"), NOT an all-caps diagram label.
_HEAD = re.compile(r"^\d+(?:\.\d+){0,3}\s+[A-Z][A-Za-z]")
# Cross-references to OTHER parts — the source of the "described the F407 instead of the F411"
# hallucination. Dropped from the facts so they never reach the model.
_XREF = re.compile(r"\bcompatib|\bdrop-?in\b|replacement|feature compatible|belongs to the|"
                   r"can be used as", re.I)


def _is_noise(line: str) -> bool:
    if _DOTS.search(line) or _TOC_TAIL.search(line) or _FIGTAB.match(line):
        return True
    good = sum(c.isalnum() or c.isspace() or c in ".,:;%()/-+°µΩ" for c in line)
    return bool(line) and good / len(line) < 0.7          # mostly symbols/mojibake → figure soup


def _heading(line: str) -> bool:
    return bool(_HEAD.match(line)) and len(line) <= 80 and not _DOTS.search(line)


def _clean(text: str) -> str:
    text = re.sub(r"-\n(\w)", r"\1", text)          # de-hyphenate words split across a line break
    text = text.replace("\r", "")
    return text


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in _clean(text).split("\n"):
        s = re.sub(r"[ \t]+", " ", raw).strip()
        if len(s) >= 6:
            out.append(s)
    return out


def _salient(line: str) -> bool:
    return bool(_UNIT.search(line) or _CAUTION.search(line) or _HEX.search(line)
                or _IDENT.search(line))


def _categorise(line: str) -> str:
    for name, pat in _CATS:
        if pat.search(line):
            return name
    return "Other notable"


@dataclass
class DatasheetDigest:
    title: str
    pages: int
    scanned_pages: int
    verified: list[dict]                                  # HIGH/MEDIUM geometry candidates
    facts: dict[str, list[tuple[str, int]]] = field(default_factory=dict)   # category -> [(line, page)]
    sections: list[tuple[str, int]] = field(default_factory=list)           # (heading, page)
    peripherals: list[str] = field(default_factory=list)
    summary: str = ""                                     # the model's grounded "what to remember"


def scan(pages: list[Page]) -> DatasheetDigest:
    """Deterministic pass over EVERY page. No model, no guessing."""
    facts: dict[str, list[tuple[str, int]]] = {}
    sections: list[tuple[str, int]] = []
    periph: set[str] = set()
    seen: set[str] = set()
    scanned = 0
    for p in pages:
        if not (p.text or "").strip():
            continue
        scanned += 1
        for ln in _lines(p.text):
            if _is_noise(ln):
                continue
            if _heading(ln) and len(sections) < 200:
                sections.append((ln, p.number))
            for m in _PERIPH.findall(ln):
                periph.add(m.upper().replace("I²C", "I2C"))
            if _salient(ln) and not _XREF.search(ln):
                key = re.sub(r"\s+", " ", ln.lower())[:90]
                if key in seen:
                    continue
                seen.add(key)
                facts.setdefault(_categorise(ln), []).append((ln[:240], p.number))
    verified = [c for c in extract_from_pages(pages) if c.confidence in ("HIGH", "MEDIUM")]
    vlist = [{"key": c.fact_key, "value": c.fact_value, "page": c.page,
              "confidence": c.confidence} for c in verified]
    return DatasheetDigest(title="", pages=len(pages), scanned_pages=scanned, verified=vlist,
                           facts=facts, sections=sections, peripherals=sorted(periph))


# order categories most-important-first for both the model prompt and the rendered report
_CAT_ORDER = ("Identity & core", "Cautions & limits", "Memory & map", "Clocks & timing",
              "Power & electrical", "Peripherals", "Other notable")


def _facts_block(dg: DatasheetDigest, per_cat: int, budget: int) -> str:
    """Grounded facts fed to the model — capped so they fit context, cautions/specs prioritised."""
    parts: list[str] = []
    total = 0
    for cat in _CAT_ORDER:
        rows = dg.facts.get(cat) or []
        if not rows:
            continue
        parts.append(f"\n## {cat}")
        for line, page in rows[:per_cat]:
            entry = f"- (p.{page}) {line}"
            if total + len(entry) > budget:
                break
            parts.append(entry)
            total += len(entry)
    return "\n".join(parts)


_SYNTH_SYSTEM = (
    "You are a senior embedded engineer briefing a colleague on a chip's datasheet. Below are facts "
    "extracted VERBATIM from the datasheet, grouped by category, each with its page number. Write a "
    "tight, practical 'What to remember about this part' briefing.\n"
    "RULES:\n"
    "- This datasheet is about ONE specific part (named under PART). Describe ONLY that part. Datasheets "
    "mention OTHER compatible parts — IGNORE them; their specs are NOT this chip.\n"
    "- The VERIFIED SPECS are ground truth measured from THIS datasheet. NEVER state a number that "
    "contradicts them (e.g. if verified flash is 512KB, do NOT write 1MB; if max clock is 100 MHz, do "
    "NOT write 168 MHz).\n"
    "- Use ONLY the facts given. Do NOT invent numbers, registers, peripherals, or limits. Do NOT recall "
    "specs of similar chips from memory.\n"
    "- State the core ONLY if it appears in the facts; if not, write 'core: not stated' — never guess.\n"
    "- Lead with identity + the headline specs (core, memory, max clock, supply/temp).\n"
    "- Call out the CAUTIONS / absolute-maximum / must-not items explicitly — those bite people.\n"
    "- Group under short headings; use bullets; cite the page like (p.42) when you state a number.\n"
    "- Be concise. This is what to remember, not a re-print of the datasheet.")


def _part_id(dg: "DatasheetDigest") -> str:
    """The best single part/identity line to lock the briefing onto."""
    ids = dg.facts.get("Identity & core", [])
    for line, _ in ids:
        if re.search(r"cortex|\bmcu\b|\d+\s*(kb|kbyte|mb|dmips|mhz)", line, re.I) and len(line) < 130:
            return line
    return ids[0][0] if ids else dg.title


def synthesise(dg: DatasheetDigest, gw, per_cat: int = 40, budget: int = 9000) -> str:
    """One grounded model call over the extracted facts. Offline/no-model → '' (caller shows the
    deterministic facts regardless)."""
    if gw is None or not gw.available():
        return ""
    block = _facts_block(dg, per_cat, budget)
    if not block.strip():
        return ""
    verified = "; ".join(f"{v['key']}={v['value']} (p.{v['page']})" for v in dg.verified) \
        or "none auto-extracted"
    prompt = (f"PART (this datasheet is ONLY about this — describe nothing else): {_part_id(dg)}\n"
              f"VERIFIED SPECS — AUTHORITATIVE, never contradict these:\n  {verified}\n"
              f"Peripherals seen: {', '.join(dg.peripherals) or 'none detected'}\n\n"
              f"EXTRACTED DATASHEET FACTS:\n{block}\n\nWrite the 'What to remember' briefing:")
    try:
        return gw.provider.generate(_SYNTH_SYSTEM, prompt).strip()
    except Exception:
        return ""


def analyze(pages: list[Page], title: str, gw=None) -> DatasheetDigest:
    dg = scan(pages)
    dg.title = title
    dg.summary = synthesise(dg, gw)
    return dg


def _first(cat_rows, pat: str) -> tuple[str, int] | None:
    rx = re.compile(pat, re.I)
    for line, page in cat_rows:
        if rx.search(line):
            return (line, page)
    return None


def key_facts(dg: DatasheetDigest) -> list[str]:
    """The deterministic 'what to remember' — curated from the grounded facts, no model. This is the
    trustworthy summary: every line is verbatim from the datasheet with a page cite, so it cannot
    hallucinate (the local model demonstrably invents a different chip's specs even when grounded)."""
    L: list[str] = []
    ident = dg.facts.get("Identity & core", [])
    part = None                                   # a real part number: letters THEN digits (STM32F411…)
    for line, _ in ident:
        m = re.search(r"\b([A-Z][A-Za-z]{1,}\d{2,}[A-Za-z0-9/\-]*)\b", line)
        if m:
            part = m.group(1); break
    L.append(f"- **Part:** {part or _part_id(dg)}")
    core = _first(ident, r"cortex-[mar]\d")
    if core:
        c = re.search(r"cortex-[mar]\d\+?", core[0], re.I)
        L.append(f"- **Core:** {c.group(0) if c else core[0][:60]}  _(p.{core[1]})_")
    for v in dg.verified:                       # verified geometry = ground truth
        L.append(f"- **{v['key'].replace('_', ' ')}:** {v['value']}  _(p.{v['page']})_")
    supply = _first(dg.facts.get("Power & electrical", []), r"\d\.\d\s*V\s*(to|-|–|…)\s*\d\.\d\s*V")
    if supply:
        L.append(f"- **Supply:** {supply[0][:80]}  _(p.{supply[1]})_")
    temp = _first(dg.facts.get("Power & electrical", []) + dg.facts.get("Other notable", []),
                  r"-?\s*40\s*(to|°|-|–).{0,20}°?C")
    if temp:
        L.append(f"- **Temp range:** {temp[0][:80]}  _(p.{temp[1]})_")
    if dg.peripherals:
        L.append(f"- **Peripherals:** {', '.join(dg.peripherals)}")
    return L


def render(dg: DatasheetDigest, full: bool = True) -> str:
    L = [f"# Datasheet digest — {dg.title}",
         f"_{dg.pages} pages, {dg.scanned_pages} with text scanned; "
         f"{sum(len(v) for v in dg.facts.values())} salient facts extracted_", ""]

    L.append("## What to remember (grounded — every line is verbatim from the datasheet)")
    L += key_facts(dg)
    L.append("")

    cautions = dg.facts.get("Cautions & limits", [])
    hard = [(ln, pg) for ln, pg in cautions
            if re.search(r"must not|should not|do not|permanent damage|absolute maximum rating", ln, re.I)
            and len(ln) > 30]
    if hard:
        L.append("## ⚠ Cautions that bite (grounded)")
        for ln, pg in hard[:8]:
            L.append(f"- (p.{pg}) {ln}")
        L.append("")

    if dg.summary:
        L.append("## AI briefing — ⚠ EXPERIMENTAL, may be wrong")
        L.append("_The local model can hallucinate a different chip's specs. Trust the grounded "
                 "sections above; treat this as a rough draft only._")
        L.append(dg.summary); L.append("")

    if dg.peripherals:
        L.append("## Peripherals detected"); L.append(", ".join(dg.peripherals)); L.append("")

    if full:
        for cat in _CAT_ORDER:
            rows = dg.facts.get(cat) or []
            if not rows:
                continue
            L.append(f"## {cat} ({len(rows)})")
            for line, page in rows[:60]:
                L.append(f"- (p.{page}) {line}")
            if len(rows) > 60:
                L.append(f"- …and {len(rows) - 60} more")
            L.append("")
        if dg.sections:
            L.append(f"## Section coverage ({len(dg.sections)} headings found)")
            for h, page in dg.sections[:80]:
                L.append(f"- (p.{page}) {h}")
            if len(dg.sections) > 80:
                L.append(f"- …and {len(dg.sections) - 80} more")
    return "\n".join(L)
