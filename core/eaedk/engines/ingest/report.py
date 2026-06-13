"""Datasheet Intelligence Report (v2.3.0) — the 7-section report produced after ingest.

Fully deterministic: extracted facts (cited), mandatory items not found (with where to look),
priority order, arch-family risks (Piece 3), closest board (Piece 4), an honest can/cannot, and
the single most important next step. The LLM is NOT used here — every line is engine-derived.
"""
from __future__ import annotations

import json
import sqlite3

from ... import repo
from .arch_risks import risks_for_arch
from .labels import label_for
from .similarity import similar_with_guidance

# Mandatory bring-up items. `needs` lists the extracted fact keys that satisfy it; an item with
# `always_missing` is never produced by the deterministic extractor, so it's always flagged.
_MANDATORY = [
    {"key": "memory_map", "label": "Memory map (flash & RAM base + size)",
     "needs": ["flash_base", "flash_bytes", "ram_base", "ram_bytes"], "bringup_critical": False,
     "why": "Without it EAEDK can't generate your linker script and your code won't link.",
     "where": 'search "memory map", "0x0800", "Flash memory", "SRAM" — usually the memory chapter.'},
    {"key": "clock", "label": "Clock configuration (default + max)", "needs": ["sysclk_max_hz"],
     "bringup_critical": False,
     "why": "The wrong clock means the wrong baud rate, so UART output is garbage.",
     "where": 'search "clock tree", "HSI", "maximum frequency", "MHz" — usually the clock chapter.'},
    {"key": "boot_pins", "label": "Boot pin configuration", "needs": [], "always_missing": True,
     "bringup_critical": True,
     "why": "Wrong boot pins = the board won't enter programming mode, so you can't flash it.",
     "where": 'search "BOOT0", "boot mode", "programming mode" — usually Chapter 2 or the pinout.'},
    {"key": "watchdog", "label": "Watchdog default state", "needs": [], "always_missing": True,
     "bringup_critical": True,
     "why": "If the watchdog is enabled at reset you have ~1 second before the chip resets itself.",
     "where": 'search "IWDG", "WWDG", "watchdog", "reset" — usually the system chapter.'},
    {"key": "power_on_reset", "label": "Power-on reset sequence", "needs": [], "always_missing": True,
     "bringup_critical": True,
     "why": "Code that runs before power is stable causes unpredictable behaviour.",
     "where": 'search "power-on", "reset sequence", "startup" — usually electrical characteristics.'},
    {"key": "gpio_base", "label": "GPIO register base", "needs": [], "always_missing": True,
     "bringup_critical": False,
     "why": "Needed to drive the LED for your blink project.",
     "where": 'search "GPIO", "register map", "0x4002" — usually the register map / memory chapter.'},
    {"key": "uart_base", "label": "UART register base", "needs": [], "always_missing": True,
     "bringup_critical": False,
     "why": "Needed for debug printing over serial.",
     "where": 'search "USART", "UART", "register map" — usually the register map chapter.'},
]

_CONF_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _fmt_value(key: str, value: str) -> str:
    """Human-friendly display of an extracted value."""
    try:
        if key.endswith("_base"):
            return f"0x{int(value, 0):08X}"
        if key.endswith("_bytes"):
            return f"{int(value) // 1024}KB"
        if key == "sysclk_max_hz":
            return f"{int(value) // 1_000_000}MHz max"
    except (ValueError, TypeError):
        pass
    return str(value)


def intelligence_report(conn: sqlite3.Connection, board_name: str) -> dict:
    """Build the 7-section report for a board that has just been ingested (reads staged candidates
    + any confirmed facts). Returns structured data; render with ``render_report_text``."""
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        raise ValueError(f"unknown board: {board_name}")
    arch = soc["arch"]

    # best candidate per key (highest confidence), plus already-confirmed facts.
    best: dict[str, sqlite3.Row] = {}
    for c in repo.list_fact_candidates(conn, board_name, status="pending"):
        cur = best.get(c["fact_key"])
        if cur is None or _CONF_RANK.get(c["confidence"], 0) > _CONF_RANK.get(cur["confidence"], 0):
            best[c["fact_key"]] = c
    confirmed = repo.board_facts_map(conn, board_name)
    found_keys = set(best) | set(confirmed)

    # Section 1 — what I found (cited).
    found = []
    for key, c in best.items():
        cite = f"p.{c['page']}" + (f", §{c['section']}" if c["section"] else "")
        found.append({"key": key, "label": label_for(key),
                      "value": _fmt_value(key, c["fact_value"]),
                      "citation": cite, "confidence": c["confidence"]})
    found.sort(key=lambda f: f["key"])

    def _item_found(it):
        return (not it.get("always_missing")) and bool(it["needs"]) and \
            all(k in found_keys for k in it["needs"])

    # Section 2 — mandatory bring-up items not found.
    missing = [{"label": it["label"], "why": it["why"], "where": it["where"]}
               for it in _MANDATORY if it.get("bringup_critical") and not _item_found(it)]

    # Section 3 — priority order.
    priority = []
    for n, it in enumerate(_MANDATORY, 1):
        ok = _item_found(it)
        priority.append({"n": n, "label": it["label"],
                         "note": it["why"], "found": ok,
                         "status": "FOUND" if ok else "NOT FOUND"})

    # Section 4 — risks (deterministic, arch-aware).
    risks = risks_for_arch(arch, found_keys)

    # Section 5 — closest board.
    similar = similar_with_guidance(conn, board_name, top=3)

    # Section 6 — can / cannot.
    has_geo = all(k in found_keys for k in ("flash_base", "flash_bytes", "ram_base", "ram_bytes"))
    can = ["Run toolchain validation"]
    cannot = []
    (can if has_geo else cannot).append("Generate a correct memory.ld linker script")
    (can if has_geo else cannot).append("Validate flash/RAM fit for your project")
    (can if arch else cannot).append("Generate a blink + UART starter scaffold")
    (can if has_geo else cannot).append("Run memory-overlap and vector-table validation")
    cannot.append("Validate the boot sequence (boot pins not found)")
    cannot.append("Detect pin-mux conflicts (alternate-function table not extracted)")

    # Section 7 — the single most important next step.
    nxt_item = next((it for it in _MANDATORY if it.get("bringup_critical") and not _item_found(it)),
                    None) or next((it for it in _MANDATORY if not _item_found(it)), None)
    next_step = ({"action": f"Find the {it_label(nxt_item)} before anything else.",
                  "why": nxt_item["why"], "where": nxt_item["where"]} if nxt_item else
                 {"action": "Everything mandatory was found — proceed to `eaedk project new`.",
                  "why": "", "where": ""})

    return {"board": board_name, "soc": soc["name"], "arch": arch,
            "found": found, "missing": missing, "priority": priority, "risks": risks,
            "similar": similar, "can": can, "cannot": cannot, "next_step": next_step}


def it_label(item: dict) -> str:
    return item["label"].lower()


def render_report_text(r: dict) -> str:
    L = [f"# Datasheet Intelligence — {r['board']} ({r['soc']}, {r['arch']})", ""]

    L.append("## 1. What I found (extracted, cited)")
    if r["found"]:
        for f in r["found"]:
            L.append(f"  ✓ {f['label']}: {f['value']}   ({f['citation']})   [{f['confidence']}]")
    else:
        L.append("  (nothing extractable was found — see Section 2 for where to look)")
    L.append("")

    L.append("## 2. What I could not find (MANDATORY for bring-up)")
    if r["missing"]:
        for m in r["missing"]:
            L.append(f"  ✗ {m['label']}")
            L.append(f"      Why mandatory: {m['why']}")
            L.append(f"      Where to look: {m['where']}")
    else:
        L.append("  (all mandatory bring-up items were found)")
    L.append("")

    L.append("## 3. Priority order (what to do first)")
    for p in r["priority"]:
        mark = "✓" if p["found"] else "✗"
        L.append(f"  {p['n']}. {mark} {p['label']}  — {p['status']}")
    L.append("")

    L.append("## 4. Risk warnings")
    if r["risks"]:
        for rk in r["risks"]:
            L.append(f"  ⚠ {rk['severity']} — {rk['title']}")
            L.append(f"      {rk['explanation']}")
    else:
        L.append("  (no architecture-specific risks flagged)")
    L.append("")

    L.append("## 5. Closest known board")
    if r["similar"]:
        m = r["similar"][0]
        L.append(f"  {m['name']} ({m['score']}% match, confidence {m['confidence']})")
        L.append("  Will probably work the same:")
        L.extend(f"    → {w}" for w in m["works_same"])
        L.append("  You MUST verify from your datasheet:")
        L.extend(f"    → {v}" for v in m["must_verify"])
        L.append(f"  Suggested starting template: {m['suggested_template']}")
    else:
        L.append("  (no similar board found)")
    L.append("")

    L.append("## 6. What EAEDK can and cannot do with this board")
    L.append("  Can now:")
    L.extend(f"    ✓ {c}" for c in r["can"])
    L.append("  Cannot yet (missing data):")
    L.extend(f"    ✗ {c}" for c in r["cannot"])
    L.append("  To unlock the missing items: find Section 2's items, then "
             "`eaedk ingest --board <name> --review` to add them.")
    L.append("")

    L.append("## 7. Your immediate next step")
    L.append(f"  {r['next_step']['action']}")
    if r["next_step"]["why"]:
        L.append(f"  {r['next_step']['why']}")
    if r["next_step"]["where"]:
        L.append(f"  {r['next_step']['where']}")
    return "\n".join(L) + "\n"
