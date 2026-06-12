"""Mentor LLM layer (Part 3) — the model explains and guides; it never asserts a hardware fact.

Reuses the offline Gateway + the post-filter. Off by default: without `--llm`, both commands
return a useful deterministic answer (the curated learning path / concept anchor). With `--llm`,
the model elaborates and the post-filter strips any uncited hardware value.
"""
from __future__ import annotations

import sqlite3

from . import repo, mentor
from .llm.gateway import Gateway
from .llm.postfilter import build_board_allowlist, filter_text

_ASK_SYSTEM = (
    "You are EAEDK's mentor for a engineer with zero firmware experience. Reason ONLY from the "
    "board capabilities and the learning path in the CONTEXT. Recommend what to build and why, "
    "in plain language, no jargon, a few sentences. NEVER state a hardware fact (address, "
    "register, clock, memory size) unless it appears in the CONTEXT. Be encouraging and concrete.")

_EXPLAIN_SYSTEM = (
    "You are EAEDK's mentor. Explain the concept in AT MOST TWO sentences: (1) what it is, "
    "(2) what to check next. Plain language for a beginner, using the board's architecture as "
    "context. NEVER invent an address, register, clock, or timing value.")


def _ctx(conn: sqlite3.Connection, board_name: str) -> tuple[dict, dict, list]:
    board, soc = repo.load_board(conn, board_name)
    caps = mentor.capability_map(conn, board_name)
    path = mentor.learning_path_for(conn, {c["capability"] for c in caps})
    return soc, caps, path


def _llm_or_note(use_llm: bool, gw: Gateway) -> str | None:
    if not use_llm:
        return "\n[mentor] add --llm for a conversational answer (offline model)."
    if not gw.available():
        return (f"\n[mentor] LLM unavailable (model '{gw.model}' not pulled); showing the "
                "deterministic answer above.")
    return None


def mentor_ask(conn: sqlite3.Connection, board_name: str, question: str,
               use_llm: bool = False, gateway: Gateway | None = None) -> str:
    soc, caps, path = _ctx(conn, board_name)
    first = path[0] if path else None
    # Deterministic backbone — always shown.
    head = [f"Mentor — {board_name} ({soc['arch']})"]
    if first:
        head.append(f"Start with: {first['title']}.  Why: {first['why']}")
    body = "\n".join(head)

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return body + note

    cap_lines = "\n".join(f"- {c['summary'] or c['capability']}" for c in caps)
    path_lines = "\n".join(f"{s['step']}. {s['title']} — {s['why']}" for s in path)
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\nCapabilities:\n{cap_lines}\n"
              f"Learning path:\n{path_lines}\n\nQUESTION: {question}\n\nAnswer:")
    raw = gw.provider.generate(_ASK_SYSTEM, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    return f"{body}\n\n{filtered}\n[mentor] {removed} uncited hardware claim(s) removed."


_CHAT_SYSTEM = (
    "You are EAEDK's mentor for someone with ZERO firmware experience, talking about ONE specific "
    "board. Reason ONLY from the CONTEXT (board facts, capabilities, learning path, concept "
    "anchor). Every reply MUST have three parts, in plain English with no unexplained jargon: "
    "(1) a short answer (a few sentences); (2) one concrete example or 'Try this:' action tied to "
    "THIS board and the user's first project; (3) end with ONE follow-up question the user can "
    "answer. NEVER say 'I don't know' without offering what to try next. NEVER give a definition "
    "without an example. NEVER invent an address, register, clock frequency, or timing value — "
    "use only numbers that appear in the CONTEXT.")


def _detect_concept(conn: sqlite3.Connection, text: str):
    """Return the concept row whose name appears in the user's text, else None. Punctuation is
    normalised to spaces so 'what is a hardfault?' still matches 'hardfault'."""
    import re as _re
    low = " " + _re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    for row in repo.list_concepts(conn):
        name = _re.sub(r"[^a-z0-9]+", " ", row["name"].lower()).strip()
        if f" {name} " in low or f" {name}s " in low:
            return row
    return None


def _board_try_this(family: str | None) -> str:
    """A concrete, board-family-appropriate experiment for the user's first project. No numbers
    that the post-filter would strip — kept conceptual so it always survives."""
    if family == "stm32":
        return ("In your blink project, find the line that enables your LED pin's GPIO clock "
                "(the RCC line) and comment it out. Build it, run it in Wokwi, and watch the LED "
                "do nothing — that's exactly why enabling the clock first matters.")
    if family == "avr":
        return ("In your blink project, set F_CPU to the wrong value and watch your delay timing "
                "break — it shows how the clock setting drives everything.")
    if family == "esp32":
        return ("In your blink project, try a Wi-Fi call before nvs_flash_init() and watch it "
                "fail — it shows why init order matters on the ESP32.")
    if family == "rp2040":
        return ("In your blink project, look at how the second-stage bootloader (boot2) is placed "
                "before your code — remove it in your head and you'll see why nothing would run.")
    return ("In your blink project, change the order of your init calls and see what breaks — "
            "order is everything in bare-metal firmware.")


def _followup_question(caps: list, path: list) -> str:
    if any(c["capability"] == "uart" for c in caps):
        return "Question: do you know which clock your UART runs on?"
    if path:
        return f"Question: are you ready to start with '{path[0]['title']}'?"
    return "Question: what would you like to build first?"


def mentor_chat(conn: sqlite3.Connection, board_name: str, messages: list[dict],
                use_llm: bool = False, gateway: Gateway | None = None) -> str:
    """A 2-way mentor turn. ``messages`` is the conversation so far ([{role, content}, ...]).
    Always returns an answer + a board-tied 'Try this' + a follow-up question (the contract holds
    even offline). The post-filter runs on every LLM response."""
    from .mentor import family_of
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return ("I can't find that board. Pick one from the Boards list, then ask me again. "
                "Try this: open the Boards tab and click a board to see what it can do. "
                "Question: which board do you have?")
    caps = mentor.capability_map(conn, board_name)
    path = mentor.learning_path_for(conn, {c["capability"] for c in caps})
    fam = family_of(soc["name"])
    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    concept = _detect_concept(conn, last_user)
    try_this = _board_try_this(fam)
    question = _followup_question(caps, path)

    # Deterministic backbone — a real answer even with no model, always ending in an action.
    if concept is not None:
        head = f"{concept['anchor']}"
    elif path:
        head = (f"Good question. For {board_name}, the place to start is '{path[0]['title']}' — "
                f"{path[0]['why']}")
    else:
        head = f"For {board_name}, tell me what you want to do and I'll point you at the first step."
    backbone = f"{head}\n\nTry this: {try_this}\n\n{question}"

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return backbone                              # offline: the structured deterministic answer

    cap_lines = "\n".join(f"- {c['summary'] or c['capability']}" for c in caps)
    path_lines = "\n".join(f"{s['step']}. {s['title']} — {s['why']}" for s in path)
    history = "\n".join(f"{m.get('role','user').upper()}: {m.get('content','')}"
                        for m in messages[-8:])
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\nCapabilities:\n{cap_lines}\n"
              f"Learning path:\n{path_lines}\n"
              + (f"Concept anchor (true; build on this): {concept['anchor']}\n" if concept else "")
              + f"\nCONVERSATION SO FAR:\n{history}\n\nReply now (answer + Try this + a question):")
    raw = gw.provider.generate(_CHAT_SYSTEM, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    answer = filtered.strip()
    # Enforce the contract even if the model (or the post-filter) dropped a part.
    if len(answer) < 20:
        answer = backbone
    else:
        if "try this" not in answer.lower():
            answer += f"\n\nTry this: {try_this}"
        if not answer.rstrip().endswith("?") and "question:" not in answer.lower():
            answer += f"\n\n{question}"
    return answer


def mentor_explain(conn: sqlite3.Connection, board_name: str, concept: str,
                   use_llm: bool = False, gateway: Gateway | None = None) -> str:
    soc, _caps, _path = _ctx(conn, board_name)
    anchor = repo.get_concept(conn, concept)
    if anchor is None:
        known = ", ".join(r["name"] for r in repo.list_concepts(conn))
        base = (f"'{concept}' isn't in the concept library yet. Known: {known}.")
    else:
        base = f"{anchor['name']}: {anchor['anchor']}"

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return base + note

    prompt = (f"Concept: {concept}\nBoard architecture: {soc['arch']}\n"
              f"Factual anchor (true; build on this): {anchor['anchor'] if anchor else '(none)'}\n"
              f"Explain in at most two sentences (what it is; what to check next):")
    raw = gw.provider.generate(_EXPLAIN_SYSTEM, prompt)
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    return f"{base}\n\n{filtered}\n[mentor] {removed} uncited hardware claim(s) removed."
