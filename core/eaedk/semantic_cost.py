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

import json
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
    # Edge-AI / ML — all map to the TFLite-Micro cost baseline (v3.1 Gap 3).
    "tflite": "tflite_micro", "tflite_micro": "tflite_micro", "tensorflow": "tflite_micro",
    "tinyml": "tflite_micro", "ai": "tflite_micro", "ml": "tflite_micro", "cnn": "tflite_micro",
    "dnn": "tflite_micro", "rnn": "tflite_micro", "edgeai": "tflite_micro", "edge-ai": "tflite_micro",
    "lwip": "lwip", "tcpip": "lwip", "tcp/ip": "lwip",
    "fatfs": "fatfs", "fat": "fatfs", "filesystem": "fatfs",
}

# Multi-word intents matched as phrases on the normalised text (single-token aliases can't catch
# these). Edge-AI phrasing all maps to the TFLite-Micro cost baseline (v3.1 Gap 3).
_PHRASES = {
    "neural network": "tflite_micro", "machine learning": "tflite_micro",
    "deep learning": "tflite_micro", "gesture recognition": "tflite_micro",
    "recognize gesture": "tflite_micro", "recognize hand gesture": "tflite_micro",
    "hand gesture": "tflite_micro", "image recognition": "tflite_micro",
    "object detection": "tflite_micro", "image classification": "tflite_micro",
    "computer vision": "tflite_micro", "edge ai": "tflite_micro", "edge inference": "tflite_micro",
    "voice recognition": "tflite_micro", "keyword spotting": "tflite_micro",
}


def _norm(text: str) -> str:
    return " " + " ".join(re.split(r"[^a-z0-9]+", (text or "").lower())) + " "


def _phrase_terms(text: str) -> list[str]:
    """Canonical terms named as multi-word phrases (v3.1 Gap 3)."""
    norm = _norm(text)
    out: list[str] = []
    for phrase, term in _PHRASES.items():
        if f" {phrase} " in norm and term not in out:
            out.append(term)
    return out


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
    """Canonical *costed* terms named in free text (single-token aliases + multi-word phrases)."""
    out: list[str] = []
    for tok in re.split(r"[^A-Za-z0-9_]+", (text or "").lower()):
        term = _ALIASES.get(tok)
        if term and term not in out:
            out.append(term)
    for term in _phrase_terms(text):                 # v3.1 Gap 3: "neural network", "hand gesture", …
        if term not in out:
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
    known: list[str] = list(_phrase_terms(text))     # v3.1 Gap 3: phrase intents first
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
    board_caps = repo.board_capability_names(conn, board_name)
    found: list[dict[str, Any]] = []
    unknown_terms = list(unknown_terms or [])
    peripheral_failures: list[dict[str, Any]] = []       # v3.1 Gap 4
    for t in terms:
        row = repo.semantic_cost(conn, t)
        if row is None:
            if t not in unknown_terms:
                unknown_terms.append(t)
        else:
            d = dict(row)
            prereqs = json.loads(d.get("prerequisites_json") or "[]")
            d["prerequisites"] = prereqs
            # Requires ANY ONE of the listed capabilities. None present -> peripheral non-compliance.
            d["prereq_ok"] = (not prereqs) or bool(set(prereqs) & board_caps)
            if not d["prereq_ok"]:
                peripheral_failures.append({"term": d["term"], "requires": prereqs})
            found.append(d)

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

    # v3.1 Gap 4: a missing physical peripheral is a hard FAIL regardless of memory — you cannot run
    # a network protocol on a board with no NIC, even if it had infinite flash.
    for pf in peripheral_failures:
        verdict = "FAIL"
        reasons.append(f"Peripheral: '{pf['term']}' requires a {' or '.join(pf['requires'])} "
                       f"interface, which {board_name} does not have. It cannot run here at any size.")

    if unknown_terms and verdict == "PASS":
        verdict = "UNKNOWN"
    if unknown_terms:
        reasons.append("No cost data for: " + ", ".join(unknown_terms)
                       + " — provide estimated_image_size and estimated_ram_usage to validate these.")

    return {"board": board_name, "board_flash": board_flash, "board_ram": board_ram,
            "terms": found, "unknown_terms": unknown_terms,
            "peripheral_failures": peripheral_failures,
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
        if r.get("prerequisites"):                       # v3.1 Gap 4: peripheral requirement status
            ok = "present" if r.get("prereq_ok") else "MISSING on this board"
            L.append(f"        requires: {' or '.join(r['prerequisites'])} interface — {ok}")
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
    for pf in res.get("peripheral_failures", []):        # v3.1 Gap 4: peripheral compliance first
        note += (f"But {pf['term']} needs a {' or '.join(pf['requires'])} interface this board does "
                 f"not have — it cannot run here regardless of memory. ")
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


def _fmt_opt(n: int | None) -> str:
    return _fmt(n) if isinstance(n, int) else "UNKNOWN"


def recommend_boards(conn: sqlite3.Connection, terms: list[str],
                     unknown_terms: list[str] | None = None) -> dict[str, Any]:
    """Rank EVERY seeded board by whether the named intent fits its VERIFIED flash/RAM + required
    peripherals, using the same deterministic ``assess`` as validation. This is the grounded answer to
    'which board suits best for X' — sourced from the board DB and the cost table, never the selected
    board and never an LLM (which would invent board names)."""
    unknown_terms = list(unknown_terms or [])
    fits: list[dict[str, Any]] = []
    maybe: list[dict[str, Any]] = []
    no: list[dict[str, Any]] = []
    for r in repo.list_boards(conn):
        res = assess(conn, r["name"], terms, unknown_terms)
        entry = {"board": r["name"], "verdict": res["verdict"],
                 "flash": res["board_flash"], "ram": res["board_ram"]}
        bucket = fits if res["verdict"] == "PASS" else (no if res["verdict"] == "FAIL" else maybe)
        bucket.append(entry)
    key = lambda e: (e["flash"] or 0, e["ram"] or 0)              # most headroom first
    fits.sort(key=key, reverse=True)
    maybe.sort(key=key, reverse=True)
    no.sort(key=key, reverse=True)
    return {"terms": list(terms), "unknown": unknown_terms, "fits": fits, "maybe": maybe, "no": no}


def recommend_chat(conn: sqlite3.Connection, terms: list[str],
                   unknown_terms: list[str] | None = None) -> str:
    """Render the grounded board recommendation for the chat path."""
    rec = recommend_boards(conn, terms, unknown_terms)
    intent = ", ".join(rec["terms"] + rec["unknown"]) or "your goal"
    L = [f"Which board suits {intent}? Here is how EAEDK's boards actually measure up — from their "
         "verified flash/RAM and the seeded cost table, not a guess:", ""]
    if rec["fits"]:
        L.append("Fits comfortably:")
        L += [f"  • {e['board']} — {_fmt_opt(e['flash'])} flash / {_fmt_opt(e['ram'])} RAM"
              for e in rec["fits"][:6]]
    if rec["maybe"]:
        L.append("Maybe — depends on your exact model/config (measure, then confirm):")
        L += [f"  • {e['board']} — {_fmt_opt(e['flash'])} flash / {_fmt_opt(e['ram'])} RAM"
              for e in rec["maybe"][:5]]
    if not rec["fits"] and not rec["maybe"]:
        L.append("None of the seeded boards comfortably fit it — this likely needs an application-class "
                 "(Linux-capable) SoC beyond the current set. Size your model first, then we can talk.")
    if rec["no"]:
        L.append("Too small for it: " + ", ".join(e["board"] for e in rec["no"][:8]) + ".")
    L.append("")
    L.append("Choose by the tightest constraint first — memory here — then the peripherals it needs, "
             "power, and ecosystem. Tell me your model size or required peripherals and I'll narrow it.")
    return "\n".join(L)
