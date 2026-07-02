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

from .extract import Page, _is_heading, extract_from_pages

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
            if _is_heading(ln) and len(sections) < 200:
                sections.append((ln, p.number))
            for m in _PERIPH.findall(ln):
                periph.add(m.upper().replace("I²C", "I2C"))
            if _salient(ln):
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
    "- Use ONLY the facts given. Do NOT invent numbers, registers, peripherals, or limits.\n"
    "- State the core/architecture ONLY if it appears in the facts. If the core is not in the facts, "
    "write 'core: not stated in extracted facts' — NEVER guess it (do not assume Cortex-M4, etc.).\n"
    "- Lead with identity + the headline specs (core, memory, max clock, supply/temp).\n"
    "- Call out the CAUTIONS / absolute-maximum / must-not items explicitly — those bite people.\n"
    "- Group under short headings; use bullets; cite the page like (p.42) when you state a number.\n"
    "- Be concise. This is what to remember, not a re-print of the datasheet.")


def synthesise(dg: DatasheetDigest, gw, per_cat: int = 40, budget: int = 9000) -> str:
    """One grounded model call over the extracted facts. Offline/no-model → '' (caller shows the
    deterministic facts regardless)."""
    if gw is None or not gw.available():
        return ""
    block = _facts_block(dg, per_cat, budget)
    if not block.strip():
        return ""
    ident = ", ".join(f"{v['value']}" for v in dg.verified[:4]) or "(no geometry auto-extracted)"
    prompt = (f"CHIP FACTS (verified geometry: {ident}; peripherals seen: "
              f"{', '.join(dg.peripherals) or 'none detected'}).\n"
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


def render(dg: DatasheetDigest, full: bool = True) -> str:
    L = [f"# Datasheet digest — {dg.title}",
         f"_{dg.pages} pages, {dg.scanned_pages} with text scanned; "
         f"{sum(len(v) for v in dg.facts.values())} salient facts extracted_", ""]

    if dg.verified:
        L.append("## Verified specs (deterministic — matched in the text)")
        for v in dg.verified:
            L.append(f"- **{v['key']}** = {v['value']}  _(p.{v['page']}, {v['confidence']})_")
        L.append("")

    if dg.summary:
        L.append("## What to remember"); L.append(dg.summary); L.append("")
    else:
        L.append("## What to remember"); L.append("_(no model available — run with --llm for the "
                 "synthesised briefing; the grounded facts below were still extracted from every "
                 "page.)_"); L.append("")

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
