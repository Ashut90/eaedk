"""Turn a deterministic board match (repo.find_similar_boards) into plain-English guidance:
what will probably work the same, what MUST be verified, and a suggested starting template.

Deterministic — the LLM never scores or decides. It may only narrate the result elsewhere.
"""
from __future__ import annotations

import sqlite3

from ... import repo

# arch family -> the init patterns that tend to carry across boards of that family.
_SAME_PATTERNS = {
    "stm32": ["Clock init pattern (RCC configuration)",
              "GPIO register layout (MODER / ODR / BSRR)",
              "UART init sequence"],
    "esp32": ["ESP-IDF GPIO/UART driver calls", "FreeRTOS task + app_main structure"],
    "avr": ["Direct port I/O (DDRx / PORTx)", "USART register setup", "F_CPU-based timing"],
    "rp2040": ["pico-sdk GPIO/UART calls", "second-stage bootloader (boot2) requirement"],
}
_VERIFY_ALWAYS = ["Exact register addresses (do NOT assume they match the similar board)",
                  "Pin alternate-function table", "Flash programming sequence"]


def _family(soc: str | None, vendor: str | None) -> str:
    s = (soc or "").upper()
    if s.startswith("STM32"):
        return "stm32"
    if s.startswith("ESP32"):
        return "esp32"
    if s == "RP2040":
        return "rp2040"
    if s.startswith("ATMEGA") or s.startswith("ATTINY") or (vendor or "").lower() == "microchip":
        return "avr"
    return "generic"


def describe_match(conn: sqlite3.Connection, match: dict) -> dict:
    """Add 'works the same' / 'must verify' / 'suggested template' to a similarity match."""
    fam = _family(match["soc"], match.get("vendor"))
    same = _SAME_PATTERNS.get(fam, ["Core init order: clocks → peripherals → application"])
    # suggested template = the matched board's most common goal, falling back to bare_metal_app.
    goal = conn.execute(
        "SELECT p.goal_type, COUNT(*) c FROM projects p JOIN boards b ON b.id=p.board_id "
        "WHERE b.name=? GROUP BY p.goal_type ORDER BY c DESC LIMIT 1", (match["name"],)).fetchone()
    template = goal["goal_type"] if goal else "bare_metal_app"
    return {**match, "works_same": same, "must_verify": _VERIFY_ALWAYS,
            "suggested_template": template}


def _effective_geometry(conn: sqlite3.Connection, board_name: str, board: dict) -> tuple:
    """The board's flash/RAM size for scoring, resolved from stored columns -> confirmed facts ->
    pending extracted candidates. Returns (flash_bytes, ram_bytes, unconfirmed) where
    ``unconfirmed`` is True if any value came from an as-yet-unconfirmed extracted candidate."""
    flash, ram = board.get("flash_bytes"), board.get("ram_bytes")
    unconfirmed = False
    if flash is None or ram is None:
        confirmed = repo.board_facts_map(conn, board_name)
        pending = {c["fact_key"]: c["fact_value"]
                   for c in repo.list_fact_candidates(conn, board_name, status="pending")}
        for key, setter in (("flash_bytes", "flash"), ("ram_bytes", "ram")):
            if locals()[setter] is None:
                if key in confirmed:
                    val = confirmed[key]
                elif key in pending:
                    val, unconfirmed = pending[key], True
                else:
                    continue
                try:
                    v = int(val)
                except (TypeError, ValueError):
                    continue
                if setter == "flash":
                    flash = v
                else:
                    ram = v
    return flash, ram, unconfirmed


def similar_with_guidance(conn: sqlite3.Connection, board_name: str, top: int = 3) -> list[dict]:
    """Top matches for a board, each with plain-English guidance. Geometry for scoring comes from
    stored columns or, for a freshly-ingested board, its pending extracted facts (Fix 4)."""
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return []
    flash, ram, unconfirmed = _effective_geometry(conn, board_name, board)
    matches = repo.find_similar_boards(
        conn, soc["arch"], flash, ram,
        peripherals=repo.board_capability_names(conn, board_name), exclude=board_name, top=top)
    out = []
    for m in matches:
        d = describe_match(conn, m)
        d["geometry_unconfirmed"] = unconfirmed   # the flash/RAM that fed scoring isn't confirmed yet
        out.append(d)
    return out
