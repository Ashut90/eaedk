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

# Role A — System Architect. Reason about goal/scope/trade-off before recommending anything.
_ASK_SYSTEM = (
    "You are EAEDK's mentor acting as a battle-tested principal firmware engineer. The user is "
    "asking what to build or which design to choose. Do NOT recommend immediately. First reason: "
    "what are they trying to LEARN or ship, what does THIS board's capability map make possible, "
    "and what is the trade-off (e.g. HAL ships fast but hides the register boundaries; bare-metal "
    "is slower but shows how the bus actually moves data). Open with a consequence or a question, "
    "never a definition. Then point at a first step WITH a reason. Reason ONLY from the CONTEXT; "
    "NEVER state a hardware fact (address, register, clock, memory size) not in the CONTEXT — for a "
    "specific value, name the datasheet section to verify instead. End with ONE question that makes "
    "the user decide what matters most: speed of shipping, or depth of understanding.")

# Role A — explain by starting from the hardware consequence, not a textbook definition.
_EXPLAIN_SYSTEM = (
    "You are EAEDK's mentor. Explain the concept by starting from the hardware CONSEQUENCE — what "
    "the chip does wrong without it — in at most three plain sentences for a beginner, using the "
    "board's architecture as context, then say what to check next. Never open with a definition. "
    "NEVER invent an address, register, clock, or timing value; if a specific value matters, name "
    "the datasheet section to confirm it instead of stating it.")


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


# Role A (System Architect) + Role D (Reverse Mentor). Teach the engineer to THINK, not to copy code.
_CHAT_SYSTEM = (
    "You are EAEDK's mentor. Your job is to teach a person to THINK like a firmware engineer — not "
    "to hand out code or definitions. Reason ONLY from the CONTEXT (board, architecture, the "
    "peripherals this board actually has, flash/RAM, project, learning step, and whether the user "
    "is on the Wokwi simulator or real hardware).\n"
    "Rules for every reply:\n"
    "- Open with a CONSEQUENCE or a QUESTION, never a definition.\n"
    "- Tie the answer to THIS board's real capabilities; if a peripheral is not in the list, say so "
    "rather than assume it. Never give a generic answer when the board is known.\n"
    "- For any specific register, alternate-function (AF) number, address, clock, or timing: do NOT "
    "state the value — you cannot verify it. Name the concern and the datasheet table to check, then "
    "ask which value the user actually used.\n"
    "- Explain WHY a choice and what breaks if they pick the alternative; never 'it depends' without "
    "saying what it depends on.\n"
    "- Before any code, make the user answer four questions (ASK, do not answer them): what is the "
    "goal, why are they doing it, how (which of this board's peripherals), when is it done (which "
    "validations PASS).\n"
    "- If the user is on Wokwi, downgrade physical-only concerns (boot pins, factory bootloader ROM) "
    "to 'later, on real hardware' — never block learning over something the simulator does not model.\n"
    "- End with exactly ONE 'Try this:' tied to this board and project, then ONE follow-up question.\n"
    "- No filler ('great question', 'certainly', 'of course'). NEVER assert a hardware fact "
    "(address, register, clock, memory size) not in the CONTEXT.")


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


_PROGRESS_Q = ("how am i doing", "what's next", "whats next", "what next", "my progress",
               "where am i", "am i done", "how far")


def _progress_summary(conn, project_name: str | None):
    """The State Engine's next-incomplete item for this project (or None). Deterministic."""
    if not project_name:
        return None
    p = repo.get_project(conn, project_name)
    if p is None:
        return None
    from .engines.state import project_status
    s = project_status(conn, p)
    return s


def mentor_chat(conn: sqlite3.Connection, board_name: str, messages: list[dict],
                use_llm: bool = False, gateway: Gateway | None = None,
                project: str | None = None, has_hardware: bool = False) -> str:
    """A 2-way mentor turn. ``messages`` is the conversation so far ([{role, content}, ...]).
    Always returns an answer + a board-tied 'Try this' + a follow-up question (the contract holds
    even offline). The post-filter runs on every LLM response. ``project`` lets the mentor read the
    State Engine for progress questions; ``has_hardware`` ties the example to Wokwi when False."""
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
    if not has_hardware:        # a Wokwi-only user — point the experiment at the simulator
        try_this = try_this.replace("run it in Wokwi", "run it in Wokwi").rstrip()
        if "Wokwi" not in try_this:
            try_this += " (run it in the Wokwi simulator — no hardware needed)."
    question = _followup_question(caps, path)
    progress = _progress_summary(conn, project)

    # "How am I doing / what's next" -> answer straight from the State Engine (never the LLM).
    if progress and any(k in last_user.lower() for k in _PROGRESS_Q):
        if progress["next"]:
            head = (f"You're at {progress['complete']}/{progress['total']} "
                    f"({progress['percent']}%). Your next task is '{progress['next']['title']}'. "
                    f"Why it matters: {progress['next']['why_it_matters']}")
        else:
            head = (f"You're at {progress['complete']}/{progress['total']} — every item is "
                    "complete. Nice work.")
        return f"{head}\n\nTry this: {try_this}\n\n{question}"

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
    have = ", ".join(sorted(c["capability"] for c in caps)) or "(none recorded)"
    path_lines = "\n".join(f"{s['step']}. {s['title']} — {s['why']}" for s in path)
    history = "\n".join(f"{m.get('role','user').upper()}: {m.get('content','')}"
                        for m in messages[-8:])
    hw = ("The user has a PHYSICAL board." if has_hardware
          else "The user is on the Wokwi simulator (NO physical board); tie 'Try this' to Wokwi and "
               "treat physical-only concerns as 'later, on real hardware'.")
    geo = []
    if isinstance(board.get("flash_bytes"), int):
        geo.append(f"flash {board['flash_bytes'] // 1024}KB")
    if isinstance(board.get("ram_bytes"), int):
        geo.append(f"RAM {board['ram_bytes'] // 1024}KB")
    geo_line = ("Geometry: " + ", ".join(geo) + "\n") if geo else ""
    step_line = f"Current learning step: {path[0]['title']}\n" if path else ""
    prog_line = ""
    if progress and progress["next"]:
        prog_line = (f"Project '{project}' progress: {progress['complete']}/{progress['total']} "
                     f"done; next task: {progress['next']['title']}.\n")
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\n{hw}\n"
              f"This board HAS these peripherals: {have}\n{geo_line}{step_line}{prog_line}"
              f"Capabilities (reason from these, not generic):\n{cap_lines}\nLearning path:\n{path_lines}\n"
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
