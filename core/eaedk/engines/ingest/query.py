"""Board Query Engine (v2.3.0) — answer any question about a board, with an explicit confidence.

Trust rules (non-negotiable):
- Mode A (the fact is in SQLite, cited): answer deterministically at HIGH with the citation.
- A mandatory-but-missing item (boot pins, watchdog, power-on): UNKNOWN with search instructions.
- Otherwise (Mode B): the LLM may elaborate, but the post-filter strips any uncited address /
  size / clock / timing — the model never asserts a hardware value. Confidence MEDIUM + verify.
- Every answer ends with an action (an implication, a verify-before, or a search-for). Never a
  bare fact.
"""
from __future__ import annotations

import sqlite3

from ... import repo
from ...llm.postfilter import build_board_allowlist, filter_text, numbers_in_text
from .labels import label_for

# topic -> (keywords, fact keys, implication, search-terms to suggest if not extracted).
_TOPICS = [
    ("clock", ("clock", "frequency", "speed", "mhz", "hsi", "hse", "pll", "oscillator"),
     ["sysclk_max_hz"],
     "If you calculate your baud rate for the wrong clock, UART output will be garbage. "
     "Always confirm your actual clock before setting baud rate.",
     "'clock tree', 'maximum frequency', 'HSI', 'SYSCLK'"),
    ("flash", ("flash", "program memory", "code size"), ["flash_bytes", "flash_base"],
     "Your compiled image must fit in this — EAEDK checks it when you set the image size.",
     "'Flash memory', 'program memory', 'memory map'"),
    ("ram", ("ram", "sram", "memory size", "stack", "heap"), ["ram_bytes", "ram_base"],
     "Your stack + heap + statics must fit in this — EAEDK validates it for your project.",
     "'SRAM', 'RAM size', 'data memory'"),
]
# mandatory items the deterministic extractor never produces -> always UNKNOWN with search help.
_MANDATORY_UNKNOWN = {
    "boot": ("boot pin configuration",
             "Wrong boot pins = the board won't enter programming mode, so you cannot flash it.",
             '"BOOT0", "BOOT1", "boot mode", "programming mode" — usually Chapter 2 or the pinout'),
    "watchdog": ("watchdog default state",
                 "If the watchdog is enabled at reset you have ~1 second before the chip resets.",
                 '"IWDG", "WWDG", "watchdog", "reset" — usually the system chapter'),
    "power": ("power-on reset sequence",
              "Code that runs before power is stable causes unpredictable behaviour.",
              '"power-on", "reset sequence", "startup" — usually electrical characteristics'),
}


def _fmt(key: str, value: str) -> str:
    try:
        if key.endswith("_base"):
            return f"0x{int(value, 0):08X}"
        if key.endswith("_bytes"):
            return f"{int(value) // 1024}KB"
        if key == "sysclk_max_hz":
            return f"{int(value) // 1_000_000}MHz"
    except (ValueError, TypeError):
        pass
    return str(value)


def _board_facts(conn, board_name):
    """key -> {value, page, section, confidence, source}. Datasheet candidates carry citations;
    verified board geometry is HIGH 'EAEDK board data'."""
    out: dict[str, dict] = {}
    board, _soc = repo.load_board(conn, board_name)
    if board:
        for k in ("flash_base", "flash_bytes", "ram_base", "ram_bytes"):
            if board.get(k) is not None:
                out[k] = {"value": str(board[k]), "page": None, "section": None,
                          "confidence": "HIGH", "source": "EAEDK verified board data"}
    for c in repo.list_fact_candidates(conn, board_name, status="pending"):
        out[c["fact_key"]] = {"value": c["fact_value"], "page": c["page"],
                              "section": c["section"], "confidence": c["confidence"],
                              "source": "your datasheet"}
    return out


def answer_query(conn: sqlite3.Connection, board_name: str, question: str,
                 use_llm: bool = False, gateway=None) -> dict:
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return {"confidence": "UNKNOWN",
                "answer": f"I don't know that board ('{board_name}'). Pick one from the Boards "
                          "list, or onboard it with `eaedk board add --interactive`."}
    q = (question or "").lower()
    facts = _board_facts(conn, board_name)

    # 1) Architecture is always known for a DB board (HIGH, from the SoC record).
    if any(w in q for w in ("architecture", "which core", "what core", "cpu", "arch")):
        return {"confidence": "HIGH",
                "answer": f"{board_name} uses the {soc['arch']} core (EAEDK verified board data).\n\n"
                          "Confidence: HIGH.\n\nWhat this means: this sets your compiler target "
                          "(the -mcpu / -march flags) and your startup code — get it wrong and the "
                          "firmware won't run on the chip."}

    # 2) Mandatory-but-missing items -> UNKNOWN with search instructions.
    for kw, (label, why, where) in _MANDATORY_UNKNOWN.items():
        if kw in q:
            return {"confidence": "UNKNOWN",
                    "answer": f"UNKNOWN — {label} was not found in your datasheet or EAEDK's "
                              f"database.\n\nThis is MANDATORY. {why}\n\nSearch your datasheet for: "
                              f"{where}.\n\nDo not proceed until this is confirmed."}

    # 3) A topic that maps to a fact we actually hold -> HIGH, cited (Mode A).
    missed_search = None
    for _name, kws, keys, implication, search_terms in _TOPICS:
        if any(k in q for k in kws):
            hit = next((facts[k] for k in keys if k in facts), None)
            if hit is not None:
                key = next(k for k in keys if k in facts)
                if hit["page"]:
                    cite = "p." + str(hit["page"]) + (f", §{hit['section']}" if hit["section"] else "")
                    lead = f"Based on {hit['source']} ({cite})"
                else:
                    cite = hit["source"]
                    lead = f"Based on {hit['source']}"
                return {"confidence": hit["confidence"], "citation": cite,
                        "answer": f"{lead}: {board_name}'s {label_for(key).lower()} is "
                                  f"{_fmt(key, hit['value'])}.\n\n"
                                  f"Confidence: {hit['confidence']} — {hit['source']}.\n\n"
                                  f"What this means for your code: {implication}"}
            # topic known but no verified value -> remember its search terms for Fix 3 below.
            missed_search = search_terms
            break

    # 4) Mode B — no verified fact. Fix 3: distinguish "no datasheet ingested" from "ingested but
    # this value wasn't extracted". A datasheet was ingested if the board has any fact candidates.
    ingested = bool(repo.list_fact_candidates(conn, board_name, status="pending")) or \
        bool(repo.list_fact_candidates(conn, board_name, status="confirmed"))
    if ingested:
        terms = missed_search or "the value you're after"
        return {"confidence": "LOW",
                "answer": f"Your datasheet was ingested, but I couldn't extract this value "
                          f"automatically — it may still be there in a format I missed.\n\n"
                          f"Search your datasheet for: {terms}.\n\nThen confirm it with: "
                          f"`eaedk ingest --board \"{board_name}\" --review`.\n\nConfidence: LOW."}
    base = (f"I don't have a verified value for that on {board_name} ({soc['arch']}).")
    gw = gateway or (None if not use_llm else __import__(
        "eaedk.llm.gateway", fromlist=["Gateway"]).Gateway())
    if use_llm and gw is not None and gw.available():
        caps = ", ".join(sorted(repo.board_capability_names(conn, board_name)))
        sys = ("You are EAEDK's board assistant. Answer briefly for a beginner. NEVER assert a "
               "specific address, memory size, clock, or timing value — speak about behaviour and "
               "what to check. Use only numbers given in the context.")
        prompt = (f"Board: {board_name} ({soc['arch']}). Capabilities: {caps}.\n"
                  f"Question: {question}\nAnswer in 2-3 sentences (no invented numbers):")
        allow = build_board_allowlist(conn, board_name)
        allow.numbers |= numbers_in_text(base)
        raw = gw.provider.generate(sys, prompt)
        elaborated, _removed = filter_text(raw, allow)
        body = elaborated.strip() or base
        return {"confidence": "MEDIUM",
                "answer": f"{base}\n\n{body}\n\nConfidence: MEDIUM — general knowledge, not "
                          "verified against your specific board.\n\nVerify this in your datasheet "
                          "before writing code that depends on it (baud rate, delays, timers)."}
    return {"confidence": "LOW",
            "answer": f"{base}\n\nNo datasheet has been ingested for this board yet.\n\n"
                      "Confidence: LOW.\n\nIngest one so I can answer from it:\n"
                      f"`eaedk ingest --file <datasheet>.pdf --board \"{board_name}\" --analyze`."}
