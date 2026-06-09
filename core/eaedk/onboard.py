"""Interactive board onboarding wizard (`eaedk board add --interactive`).

Loads pristine hardware data into the SQLite truth DB with live, in-loop validation so a
bad partition layout is caught and re-prompted immediately — never deferred to the end.

Design for testability: all terminal I/O goes through injected ``ask``/``out`` callables
(real ``input``/``print`` in the CLI, scripted in tests). Live validation REUSES the
deterministic rule functions (`PARTITION_LAYOUT_FITS`, `PARTITION_NO_OVERLAP`,
`VECTOR_TABLE_PLACEMENT`) so the wizard and the engine can never disagree.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

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
    unknown_fields: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        # Any blank/UNKNOWN core field drops the record to MEDIUM (defensive posture).
        return "MEDIUM" if self.unknown_fields else "HIGH"


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
    out(f"  Confidence   {draft.confidence}"
        + (f"  (UNKNOWN: {', '.join(draft.unknown_fields)})" if draft.unknown_fields else ""))


def _commit(conn, draft: BoardDraft) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        src = conn.execute(
            "INSERT INTO sources(type,title,uri,hash,created_at) VALUES ('manual',?,NULL,NULL,?)",
            (f"interactive onboarding: {draft.name}", now)).lastrowid
        cit = conn.execute(
            "INSERT INTO citations(source_id,page,section,bbox_json,snippet) "
            "VALUES (?,NULL,'interactive onboarding',NULL,?)",
            (src, f"entered by engineer on {now}")).lastrowid
        # Multiple boards can share a SoC (socs.name is UNIQUE) — reuse if present.
        soc_name = draft.soc_name or draft.name
        existing = conn.execute("SELECT id FROM socs WHERE name=?", (soc_name,)).fetchone()
        if existing is not None:
            soc_id = existing["id"]
        else:
            soc_id = conn.execute(
                "INSERT INTO socs(name,vendor,arch,notes) VALUES (?,?,?,?)",
                (soc_name, draft.vendor, draft.arch,
                 f"VTOR alignment {draft.vtor_align}B")).lastrowid
        board_id = conn.execute(
            "INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
            "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence) "
            "VALUES (?,?,?,?,?,?,NULL,NULL,'internal_flash','[]',?,?)",
            (soc_id, draft.name, draft.flash_base, draft.flash_bytes, draft.ram_base,
             draft.ram_bytes, src, draft.confidence)).lastrowid
        verified = 1 if draft.confidence == "HIGH" else 0
        for p in draft.partitions:
            conn.execute(
                "INSERT INTO facts(board_id,kind,key,value,citation_id,confidence,"
                "verified_by_human,created_at) VALUES (?,'partition',?,?,?,?,?,?)",
                (board_id, p["role"], json.dumps({"base": p["base"], "size": p["size"]}),
                 cit, draft.confidence, verified, now))


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

    _commit(conn, draft)
    _summary(draft, out)
    return draft.name
