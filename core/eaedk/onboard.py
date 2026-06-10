"""Interactive board onboarding wizard (`eaedk board add --interactive`).

Loads pristine hardware data into the SQLite truth DB with live, in-loop validation so a
bad partition layout is caught and re-prompted immediately — never deferred to the end.

Design for testability: all terminal I/O goes through injected ``ask``/``out`` callables
(real ``input``/``print`` in the CLI, scripted in tests). Live validation REUSES the
deterministic rule functions (`PARTITION_LAYOUT_FITS`, `PARTITION_NO_OVERLAP`,
`VECTOR_TABLE_PLACEMENT`) so the wizard and the engine can never disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import repo
from .context import build_context
from .engines.validation.rules import RULES, FAIL, UNKNOWN

Ask = Callable[[str], str]
Out = Callable[[str], None]

# Core architecture menu -> (arch string, VTOR alignment bytes). Selecting a core binds the
# VTOR alignment requirement into the session immediately (used by VECTOR_TABLE_PLACEMENT).
ARCH_OPTIONS: list[tuple[str, str, int]] = [
    ("Cortex-M0+", "arm-cortex-m0plus", 256),
    ("Cortex-M3", "arm-cortex-m3", 512),
    ("Cortex-M4", "arm-cortex-m4", 512),
    ("Cortex-M7", "arm-cortex-m7", 512),
    ("Cortex-M33", "arm-cortex-m33", 512),
    ("Other (enter arch string)", "", 0),
]

# (label, role) for the bootloader + 3-slot layout.
PART_SLOTS: list[tuple[str, str]] = [
    ("Bootloader", "bootloader"),
    ("Slot A", "slot_a"),
    ("Slot B", "slot_b"),
    ("Slot C", "slot_c"),
]

CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")
# Domains for optional initial facts (-> facts.domain) and their default fine `kind`.
FACT_DOMAINS = ("MEMORY", "CLOCK", "TIMING", "PINMUX", "POWER")
# Provenance source types for optional facts (-> facts.source_type).
FACT_SOURCE_TYPES = ("DATASHEET", "TRM", "SDK_DOC", "SCHEMATIC", "USER_INPUT")

_MEM_RE = re.compile(r"(?i)^\s*(0x[0-9a-f]+|\d+)\s*(b|kb|kib|mb|mib|gb|gib)?\s*$")
_MEM_FACTOR = {"b": 1, "kb": 1024, "kib": 1024, "mb": 1024**2,
               "mib": 1024**2, "gb": 1024**3, "gib": 1024**3}


def parse_mem(s: str) -> int | None:
    """Parse a hex address or a size with optional binary unit (KB/MB = 1024-based)."""
    m = _MEM_RE.match(s or "")
    if not m:
        return None
    num = int(m.group(1), 0)
    return num * _MEM_FACTOR[(m.group(2) or "b").lower()]


@dataclass
class BoardDraft:
    name: str = ""
    vendor: str | None = None
    soc_name: str | None = None
    arch: str = ""
    vtor_align: int = 512
    flash_base: int | None = None
    flash_bytes: int | None = None
    ram_base: int | None = None
    ram_bytes: int | None = None
    partitions: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    confidence_choice: str | None = None     # engineer-selected; capped by UNKNOWNs

    @property
    def confidence(self) -> str:
        # Any blank/UNKNOWN core field caps the record at MEDIUM (defensive posture); the
        # engineer may choose lower, but never claim higher than the data supports.
        ceiling = "MEDIUM" if self.unknown_fields else "HIGH"
        if self.confidence_choice is None:
            return ceiling
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        return self.confidence_choice if order[self.confidence_choice] <= order[ceiling] \
            else ceiling


def _prompt(ask: Ask, out: Out, label: str, default: Any = None,
            parser: Callable[[str], Any] | None = None) -> tuple[Any, bool]:
    """Returns (value, is_unknown). Blank with no default -> (None, True)."""
    hint = ""
    if default is not None:
        hint = f" [0x{default:X}]" if parser is parse_mem and isinstance(default, int) else f" [{default}]"
    raw = ask(f"{label}{hint}: ").strip()
    if raw == "":
        if default is not None:
            return default, False
        return None, True
    if parser is not None:
        val = parser(raw)
        if val is None:
            out(f"  ! could not parse {raw!r}; recording as UNKNOWN")
            return None, True
        return val, False
    return raw, False


def _select_arch(ask: Ask, out: Out) -> tuple[str, str | None, int]:
    """Returns (arch_string, soc_core_label, vtor_align). Reprompts on bad selection."""
    out("SoC core architecture:")
    for i, (label, _arch, align) in enumerate(ARCH_OPTIONS, 1):
        extra = f"  (VTOR {align}B)" if align else ""
        out(f"  {i}) {label}{extra}")
    while True:
        raw = ask("Select core [1-%d]: " % len(ARCH_OPTIONS)).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(ARCH_OPTIONS):
            label, arch, align = ARCH_OPTIONS[int(raw) - 1]
            if arch == "":  # Other
                custom = ask("Enter arch string (e.g. arm-cortex-a53): ").strip()
                arch = custom or "unknown"
                align = 256 if "m0" in arch.lower() else 512
                label = arch
            out(f"  -> {label} bound; VTOR alignment = {align} bytes")
            return arch, label, align
        out("  ! enter a number from the list")


def _collect_partitions(ask: Ask, out: Out, defaults: dict[str, dict[str, int]]
                        ) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for label, role in PART_SLOTS:
        d = defaults.get(role, {})
        base, _ = _prompt(ask, out, f"  {label} base offset", d.get("base"), parse_mem)
        size, _ = _prompt(ask, out, f"  {label} size", d.get("size"), parse_mem)
        if base is None or size is None:
            out(f"  ! {label} skipped (incomplete)")
            continue
        parts.append({"name": label, "role": role, "base": base, "size": size})
    return parts


def _validate_partitions(draft: BoardDraft) -> list[str]:
    """Run the live fitment + VTOR rules. Returns FAIL reasons (empty = clean)."""
    if draft.flash_base is None or draft.flash_bytes is None or not draft.partitions:
        return []
    boot = next((p for p in draft.partitions if p["role"] == "bootloader"), None)
    inputs = {
        "partitions": draft.partitions,
        "primary_storage_bytes": draft.flash_bytes,
        "vector_table_align": draft.vtor_align,
    }
    if boot is not None:
        inputs["vector_table_addr"] = draft.flash_base + boot["base"]
    ctx = build_context(inputs, {"flash_base": draft.flash_base,
                                 "flash_bytes": draft.flash_bytes}, {"arch": draft.arch},
                        "bootloader")
    errors: list[str] = []
    for key in ("PARTITION_LAYOUT_FITS", "PARTITION_NO_OVERLAP", "VECTOR_TABLE_PLACEMENT"):
        res = RULES[key].func(ctx)
        if res.status == FAIL:
            errors.append(res.reason)
    return errors


def _summary(draft: BoardDraft, out: Out) -> None:
    def mem(v):
        return "UNKNOWN" if v is None else f"{v} B (0x{v:X})"

    out("")
    out(f"=== Board onboarded: {draft.name} ===")
    out(f"  SoC          {draft.soc_name or '-'}  ({draft.arch}, VTOR {draft.vtor_align}B)")
    out(f"  Vendor       {draft.vendor or '-'}")
    out(f"  Flash        {mem(draft.flash_bytes)}"
        + (f" @ 0x{draft.flash_base:08X}" if draft.flash_base is not None else ""))
    out(f"  RAM          {mem(draft.ram_bytes)}"
        + (f" @ 0x{draft.ram_base:08X}" if draft.ram_base is not None else ""))
    if draft.partitions:
        out("  Partitions   (verified boundaries):")
        for p in draft.partitions:
            absbase = (draft.flash_base + p["base"]) if draft.flash_base is not None else None
            absstr = f"  abs=0x{absbase:08X}" if absbase is not None else ""
            out(f"    {p['name']:11s} off=0x{p['base']:X}  size=0x{p['size']:X}{absstr}")
    else:
        out("  Partitions   (none recorded)")
    if draft.facts:
        out("  Initial facts:")
        for f in draft.facts:
            out(f"    {f['domain']}.{f['key']} = {f['value']}  "
                f"[{f['source_type']}, {f['confidence']}]")
    out(f"  Confidence   {draft.confidence}"
        + (f"  (UNKNOWN: {', '.join(draft.unknown_fields)})" if draft.unknown_fields else ""))


def _choose(ask: Ask, out: Out, label: str, options: tuple[str, ...],
            default: str | None = None) -> str:
    """Select one of ``options`` (case-insensitive); blank returns ``default``."""
    hint = f" [{default}]" if default else ""
    upper = {o.upper(): o for o in options}
    while True:
        raw = ask(f"{label} ({'/'.join(options)}){hint}: ").strip()
        if raw == "" and default:
            return default
        if raw.upper() in upper:
            return upper[raw.upper()]
        out(f"  ! choose one of: {', '.join(options)}")


def _collect_facts(ask: Ask, out: Out, default_conf: str) -> list[dict[str, Any]]:
    """Optional loop to enter initial engineering facts with source citation."""
    yn = ask("Add initial facts (timing/clock/memory) with citations? [y/N]: ").strip().lower()
    if yn not in ("y", "yes"):
        return []
    facts: list[dict[str, Any]] = []
    out("  Enter facts; leave the key blank to finish.")
    while True:
        key, _ = _prompt(ask, out, "  Fact key (blank to stop)")
        if not key:
            break
        domain = _choose(ask, out, "  Domain", FACT_DOMAINS, default="MEMORY")
        value, _ = _prompt(ask, out, "  Value (hex/number/text)")
        if value is None:
            out("  ! value required; skipping this fact")
            continue
        source_type = _choose(ask, out, "  Source", FACT_SOURCE_TYPES, default="DATASHEET")
        section, _ = _prompt(ask, out, "  Citation section (e.g. 'Table 12, RCC')")
        page, _ = _prompt(ask, out, "  Citation page", parser=parse_mem)
        conf = _choose(ask, out, "  Confidence", CONFIDENCE_LEVELS, default=default_conf)
        facts.append({"domain": domain, "key": key, "value": value,
                      "source_type": source_type, "section": section, "page": page,
                      "confidence": conf})
    return facts


def _commit(conn, draft: BoardDraft) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        # Board identity keeps its own (typed) row + provenance source (no raw SQL here —
        # everything goes through repo helpers / record_fact).
        src = repo.create_manual_source(conn, f"interactive onboarding: {draft.name}")
        soc_id = repo.get_or_create_soc(conn, draft.soc_name or draft.name, draft.vendor,
                                        draft.arch, notes=f"VTOR alignment {draft.vtor_align}B")
        board_id = repo.create_board(
            conn, soc_id=soc_id, name=draft.name, flash_base=draft.flash_base,
            flash_bytes=draft.flash_bytes, ram_base=draft.ram_base, ram_bytes=draft.ram_bytes,
            source_id=src, confidence=draft.confidence)
        # Partitions normalize into the unified engineering-fact layer via record_fact().
        for p in draft.partitions:
            repo.record_fact(
                conn, board_id=board_id, domain="MEMORY", kind="partition",
                fact_key=p["role"], fact_value={"base": p["base"], "size": p["size"]},
                source_type="USER_INPUT", confidence=draft.confidence,
                citation_section="interactive onboarding",
                snippet=f"{p['name']} entered by engineer on {now}")
        # Optional initial facts, each with its own provenance + confidence.
        for f in draft.facts:
            repo.record_fact(
                conn, board_id=board_id, domain=f["domain"], fact_key=f["key"],
                fact_value=f["value"], source_type=f["source_type"], confidence=f["confidence"],
                citation_section=f.get("section"), citation_page=f.get("page"),
                snippet=f"{f['source_type']} fact entered on {now}")


def run_wizard(conn, ask: Ask, out: Out, max_attempts: int = 5) -> str | None:
    """Drive the onboarding wizard. Returns the committed board name, or None if aborted."""
    out("EAEDK interactive board onboarding — Enter to leave a field UNKNOWN.\n")
    draft = BoardDraft()

    name, _ = _prompt(ask, out, "Board name")
    if not name:
        out("error: a board name is required; aborting.")
        return None
    if conn.execute("SELECT 1 FROM boards WHERE name=?", (name,)).fetchone():
        out(f"error: a board named {name!r} already exists; aborting.")
        return None
    draft.name = name

    draft.vendor, _ = _prompt(ask, out, "Vendor")
    draft.soc_name, _ = _prompt(ask, out, "SoC name")

    # Selecting a core binds the VTOR alignment into the session immediately.
    draft.arch, _core_label, draft.vtor_align = _select_arch(ask, out)

    for field_label, attr in (("Total Flash size", "flash_bytes"),
                              ("Flash base address", "flash_base"),
                              ("Total RAM size", "ram_bytes"),
                              ("RAM base address", "ram_base")):
        val, unknown = _prompt(ask, out, field_label, parser=parse_mem)
        setattr(draft, attr, val)
        if unknown:
            draft.unknown_fields.append(attr)

    # Confidence: default to what the data supports; the choice is capped by UNKNOWNs.
    ceiling = "MEDIUM" if draft.unknown_fields else "HIGH"
    draft.confidence_choice = _choose(ask, out, "Overall confidence", CONFIDENCE_LEVELS,
                                      default=ceiling)
    if draft.unknown_fields and draft.confidence_choice == "HIGH":
        out("  ! cannot be HIGH with UNKNOWN core fields — capping at MEDIUM")

    out("\n3-slot partition allocation (bootloader + slots A/B/C):")
    defaults: dict[str, dict[str, int]] = {}
    for attempt in range(1, max_attempts + 1):
        draft.partitions = _collect_partitions(ask, out, defaults)
        errors = _validate_partitions(draft)
        if not errors:
            break
        out("")
        for e in errors:
            out(f"  Error: {e}")
        if attempt < max_attempts:
            out("  Re-enter the affected offsets (Enter keeps the shown value):")
            defaults = {p["role"]: {"base": p["base"], "size": p["size"]}
                        for p in draft.partitions}
        else:
            out("  Too many invalid attempts — recording partitions as UNKNOWN.")
            draft.partitions = []
            draft.unknown_fields.append("partitions")

    draft.facts = _collect_facts(ask, out, draft.confidence)

    _commit(conn, draft)
    _summary(draft, out)
    return draft.name
