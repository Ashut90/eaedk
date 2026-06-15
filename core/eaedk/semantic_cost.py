"""Semantic intent translation (v3.0 P2) — high-level intent → concrete hardware cost.

The validation engine is a pure numeric checker; it cannot act on the word "gRPC". This layer
turns a beginner's intent into the flash/RAM numbers the engine needs, using a curated, seeded,
citation-backed lookup (``semantic_cost_estimates``). It is NOT LLM inference — every number is a
seeded estimate with a source, flagged unverified until a human confirms it.

Feasibility uses the conservative end of the range: if even the summed *minimum* exceeds the
board's flash or RAM, it cannot fit (FAIL). If the *maximum* exceeds it but the minimum fits, the
result depends on configuration (UNKNOWN/TIGHT). If the maximum fits, PASS.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import repo

# Colloquial spellings the user types -> the canonical seeded term.
_ALIASES = {
    "grpc": "grpc",
    "tls": "tls_mbedtls", "mbedtls": "tls_mbedtls", "mbed-tls": "tls_mbedtls",
    "ssl": "tls_mbedtls", "https": "tls_mbedtls",
    "mqtt": "mqtt_paho", "paho": "mqtt_paho",
    "freertos": "freertos", "rtos": "freertos",
    "tflite": "tflite_micro", "tflite_micro": "tflite_micro", "tensorflow": "tflite_micro",
    "tinyml": "tflite_micro",
    "lwip": "lwip", "tcpip": "lwip", "tcp/ip": "lwip",
    "fatfs": "fatfs", "fat": "fatfs", "filesystem": "fatfs",
}


def _fmt(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB".replace(".0MB", "MB")
    if n >= 1024:
        return f"{n // 1024}KB"
    return f"{n}B"


# Protocols/stacks we RECOGNISE as cost-bearing but have no seeded estimate for. Naming them lets us
# honestly say "I don't have cost data for X" instead of staying silent (v3.0 P2B).
_UNCOSTED = {
    "coap", "http", "websocket", "websockets", "ble", "zigbee", "lorawan",
    "canbus", "modbus", "cbor", "protobuf", "nanopb", "openssl", "wolfssl", "littlefs",
    "spiffs", "zephyr", "threadx", "dtls", "snmp",
}
_STOPWORDS = {"and", "with", "the", "a", "an", "to", "for", "use", "using", "add", "i", "want",
              "need", "plus", "on", "this", "board", "my", "of", "in", "it", "run", "running"}


def canonical(token: str) -> str | None:
    return _ALIASES.get(token.strip().lower())


def parse_intent(text: str) -> list[str]:
    """Canonical *costed* terms named in free text, in first-seen order, deduped."""
    out: list[str] = []
    for tok in re.split(r"[^A-Za-z0-9_]+", (text or "").lower()):
        term = _ALIASES.get(tok)
        if term and term not in out:
            out.append(term)
    return out


def detect_uncosted(text: str) -> list[str]:
    """Recognised cost-bearing terms with NO seeded estimate, named in free text (v3.0 P2B)."""
    toks = [t for t in re.split(r"[^A-Za-z0-9_]+", (text or "").lower()) if t]
    seen, out = set(), []
    for t in toks:
        if t in _UNCOSTED and t not in seen and _ALIASES.get(t) is None:
            seen.add(t); out.append(t)
    return out


def classify_terms(text: str) -> tuple[list[str], list[str]]:
    """For an explicit `--intent` list: split into (known canonical terms, unknown requested tokens).
    Every deliberate token the user typed is accounted for — known ones costed, the rest flagged."""
    known: list[str] = []
    unknown: list[str] = []
    for tok in re.split(r"[^A-Za-z0-9_]+", (text or "").lower()):
        if not tok or tok in _STOPWORDS:
            continue
        term = _ALIASES.get(tok)
        if term:
            if term not in known:
                known.append(term)
        elif tok not in unknown:
            unknown.append(tok)
    return known, unknown


def assess(conn: sqlite3.Connection, board_name: str, terms: list[str],
           unknown_terms: list[str] | None = None) -> dict[str, Any]:
    """Sum the cost of the named terms and check feasibility against the board's flash/RAM.

    ``terms`` are canonical costed terms; ``unknown_terms`` are recognised-but-uncosted requests to
    surface. Verdict: FAIL (min doesn't fit) / UNKNOWN (tight or missing data) / PASS (max fits)."""
    board, _soc = repo.load_board(conn, board_name)
    found: list[dict[str, Any]] = []
    unknown_terms = list(unknown_terms or [])
    for t in terms:
        row = repo.semantic_cost(conn, t)
        if row is None:
            if t not in unknown_terms:
                unknown_terms.append(t)
        else:
            found.append(dict(row))

    f_min = sum(r["flash_min_bytes"] for r in found)
    f_max = sum(r["flash_max_bytes"] for r in found)
    r_min = sum(r["ram_min_bytes"] for r in found)
    r_max = sum(r["ram_max_bytes"] for r in found)
    board_flash = board.get("flash_bytes") if board else None
    board_ram = board.get("ram_bytes") if board else None

    reasons: list[str] = []
    verdict = "PASS"
    geometry_known = board_flash is not None and board_ram is not None

    if not found:
        verdict = "UNKNOWN"
        reasons.append("No cost data matched the requested intent.")
    elif not geometry_known:
        verdict = "UNKNOWN"
        reasons.append(f"{board_name} has no confirmed flash/RAM geometry to check against.")
    else:
        if f_min > board_flash:
            verdict = "FAIL"
            reasons.append(f"Flash: even the minimum estimate {_fmt(f_min)} exceeds the board's "
                           f"{_fmt(board_flash)} — it cannot fit.")
        if r_min > board_ram:
            verdict = "FAIL"
            reasons.append(f"RAM: even the minimum estimate {_fmt(r_min)} exceeds the board's "
                           f"{_fmt(board_ram)} — it cannot fit.")
        if verdict != "FAIL":
            if f_max > board_flash or r_max > board_ram:
                verdict = "UNKNOWN"
                reasons.append("Fits at the low end but not the high end — depends on build config; "
                               "provide a measured estimated_image_size / estimated_ram_usage.")
            else:
                reasons.append(f"Fits: max estimate {_fmt(f_max)} flash / {_fmt(r_max)} RAM is "
                               f"within {_fmt(board_flash)} / {_fmt(board_ram)}.")

    if unknown_terms and verdict == "PASS":
        verdict = "UNKNOWN"
    if unknown_terms:
        reasons.append("No cost data for: " + ", ".join(unknown_terms)
                       + " — provide estimated_image_size and estimated_ram_usage to validate these.")

    return {"board": board_name, "board_flash": board_flash, "board_ram": board_ram,
            "terms": found, "unknown_terms": unknown_terms,
            "flash_min": f_min, "flash_max": f_max, "ram_min": r_min, "ram_max": r_max,
            "verdict": verdict, "reasons": reasons}


def render(result: dict[str, Any]) -> str:
    """Full breakdown for `validate --intent`: per-term costs, the summed math, and the verdict."""
    L = [f"# Intent feasibility — {result['board']}", ""]
    bf, br = result["board_flash"], result["board_ram"]
    L.append(f"Board budget: {_fmt(bf) if bf is not None else 'UNKNOWN'} flash, "
             f"{_fmt(br) if br is not None else 'UNKNOWN'} RAM")
    L.append("")
    L.append("## Cost of each requested capability (seeded estimates, ranges)")
    for r in result["terms"]:
        verified = "verified" if r["verified_by_human"] else "UNVERIFIED estimate"
        L.append(f"  • {r['term']}: flash {_fmt(r['flash_min_bytes'])}–{_fmt(r['flash_max_bytes'])}, "
                 f"RAM {_fmt(r['ram_min_bytes'])}–{_fmt(r['ram_max_bytes'])}  [{verified}]")
        if r["notes"]:
            L.append(f"        {r['notes']}")
    for t in result["unknown_terms"]:
        L.append(f"  • {t}: no cost data on file.")
    L.append("")
    L.append("## Summed estimate vs board")
    L.append(f"  Flash needed: {_fmt(result['flash_min'])}–{_fmt(result['flash_max'])}   "
             f"vs board {_fmt(bf) if bf is not None else 'UNKNOWN'}")
    L.append(f"  RAM needed:   {_fmt(result['ram_min'])}–{_fmt(result['ram_max'])}   "
             f"vs board {_fmt(br) if br is not None else 'UNKNOWN'}")
    L.append("")
    L.append(f"## Verdict: {result['verdict']}")
    for r in result["reasons"]:
        L.append(f"  - {r}")
    return "\n".join(L) + "\n"


def chat_note(conn: sqlite3.Connection, board_name: str, text: str) -> str:
    """A concise grounded cost note for the chat path, or "" when no known term is mentioned.

    When a term is NOT in the table it explicitly says so and asks for numbers (v3.0 P2B)."""
    terms = parse_intent(text)
    uncosted = detect_uncosted(text)
    if not terms and not uncosted:
        return ""
    if not terms:                                    # only recognised-but-uncosted terms mentioned
        return ("I don't have cost data for " + ", ".join(uncosted)
                + " — provide estimated_image_size and estimated_ram_usage to run a numeric "
                  "validation.")
    res = assess(conn, board_name, terms, uncosted)
    parts = []
    for r in res["terms"]:
        parts.append(f"{r['term']} ~{_fmt(r['flash_min_bytes'])}–{_fmt(r['flash_max_bytes'])} flash / "
                     f"~{_fmt(r['ram_min_bytes'])}–{_fmt(r['ram_max_bytes'])} RAM")
    note = "Based on known cost data: " + "; ".join(parts) + ". "
    bf, br = res["board_flash"], res["board_ram"]
    if bf is not None:
        note += (f"Summed, that is ~{_fmt(res['flash_min'])}–{_fmt(res['flash_max'])} flash and "
                 f"~{_fmt(res['ram_min'])}–{_fmt(res['ram_max'])} RAM; this board has "
                 f"{_fmt(bf)} flash / {_fmt(br) if br is not None else 'UNKNOWN'} RAM. ")
        if res["verdict"] == "FAIL":
            note += "This will NOT fit."
        elif res["verdict"] == "UNKNOWN":
            note += "This is tight — it may not fit depending on configuration."
        else:
            note += "This fits."
    if res["unknown_terms"]:
        note += (" I don't have cost data for " + ", ".join(res["unknown_terms"])
                 + " — provide estimated_image_size and estimated_ram_usage for those.")
    return note
