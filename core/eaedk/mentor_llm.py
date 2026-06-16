"""Mentor LLM layer (Part 3) — the model explains and guides; it never asserts a hardware fact.

Reuses the offline Gateway + the post-filter. Off by default: without `--llm`, both commands
return a useful deterministic answer (the curated learning path / concept anchor). With `--llm`,
the model elaborates and the post-filter strips any uncited hardware value.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from . import repo, mentor, reasoning, semantic_cost, arbiter, problem_patterns, navigator
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
    try:                                                 # a model timeout must degrade, not crash
        raw = gw.provider.generate(_ASK_SYSTEM, prompt)
    except Exception:
        return body + "\n[mentor] LLM unreachable (timed out); showing the deterministic answer above."
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
    "- When the user names a project type (robotics, sensor, motor, audio, IoT), reason about WHICH "
    "of this board's specific peripherals that project needs and why — e.g. motor control = a "
    "timer's PWM through an H-bridge, not generic GPIO — never list all peripherals generically.\n"
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


# Project-type reasoning (v2.4.1): when the user names a domain, reason about WHICH of THIS board's
# specific peripherals that project needs and why — never a generic capability list.
_PROJECT_DOMAINS = {
    "robotics": ("robot", "robotics", "motor", "servo", "h-bridge", "h bridge", "bldc", "stepper",
                 "wheel", "drone", "actuator", "encoder"),
    "sensor":   ("sensor", "imu", "accelerometer", "gyro", "temperature", "humidity", "proximity",
                 "distance", "lidar"),
    "audio":    ("audio", "sound", "speaker", "microphone", "music", "i2s"),
    "iot":      ("iot", "wifi", "wi-fi", "bluetooth", " ble ", "mqtt", "internet of"),
}


def _detect_domain(text: str) -> str | None:
    low = " " + (text or "").lower() + " "
    for domain, kws in _PROJECT_DOMAINS.items():
        if any(k in low for k in kws):
            return domain
    return None


def _domain_reasoning(domain: str, caps: set, family: str | None, board_name: str) -> str:
    """Curated, family-aware reasoning: which of THIS board's specific peripherals the project needs
    and why. Deterministic guidance (same class as the existing think-before-code hints), gated on the
    board's capabilities and chip family — so it is specific, not a generic peripheral dump."""
    has = caps.__contains__
    if domain == "robotics":
        L = [f"Robotics on {board_name} is move + sense + decide, and each maps to a SPECIFIC "
             "peripheral, not a generic GPIO pin:"]
        if has("timer"):
            if family == "stm32":
                L.append("- Move: motor speed is a PWM signal from a TIMER. On STM32 the advanced-"
                         "control timer (TIM1) has COMPLEMENTARY outputs with dead-time — exactly what "
                         "drives both sides of an H-BRIDGE (a DC/BLDC motor). A basic timer "
                         "(TIM9/10/11) cannot. Confirm which timer is the advanced one on your part in "
                         "the datasheet.")
            else:
                L.append("- Move: motor speed is a PWM signal from a TIMER, sent through a motor "
                         "driver / H-bridge — never straight from a GPIO pin.")
            L.append("- Position: a TIMER in encoder mode counts wheel-encoder pulses for odometry.")
        else:
            L.append("- Move: motors need PWM, but no timer capability is recorded for this board — "
                     "check the datasheet for a timer with PWM output.")
        buses = [b.upper() for b in ("i2c", "spi") if has(b)]
        if buses:
            L.append(f"- Sense: an IMU (accel+gyro) over {'/'.join(buses)} gives orientation — your "
                     "balance and heading.")
        if has("uart"):
            L.append("- Talk: UART to a host (Raspberry Pi, GPS, or a Bluetooth module).")
        L.append("Safety: a motor MUST go through a driver/H-bridge on its own supply — back-EMF and "
                 "current will destroy a pin driven directly.")
        return "\n".join(L)
    if domain == "sensor":
        L = [f"A sensor project on {board_name} needs a sensor BUS, not generic GPIO:"]
        if has("i2c"):
            L.append("- I2C: many sensors on two wires (IMU, temp/humidity, ToF) — start here.")
        if has("spi"):
            L.append("- SPI: faster, more wires — for high-rate sensors (some IMUs, external ADCs).")
        L.append("- A TIMER sets a fixed sample rate so readings are evenly spaced." if has("timer")
                 else "- Sample at a fixed rate (a timer interrupt) so readings are evenly spaced.")
        if not (has("i2c") or has("spi")):
            L.append("- No I2C/SPI is recorded for this board — check the datasheet before assuming a bus.")
        return "\n".join(L)
    if domain == "audio":
        L = [f"Audio on {board_name} is about streaming samples without the CPU stalling:",
             "- Output: I2S to a DAC/codec if the board has it, or PWM as a crude fallback.",
             ("- A TIMER sets the sample rate; DMA moves samples so the CPU isn't blocked."
              if has("timer") else "- You need a steady sample clock (timer) and ideally DMA."),
             "- Check the datasheet for I2S/DAC — not every board has true audio output."]
        return "\n".join(L)
    if domain == "iot":
        L = [f"An IoT project on {board_name} starts with one question: does it have connectivity?"]
        conn_caps = [c for c in ("wifi", "ethernet", "bluetooth", "ble") if has(c)]
        if conn_caps:
            L.append(f"- This board has {', '.join(conn_caps)} — that's your link.")
        else:
            L.append("- No Wi-Fi/Ethernet/BLE is recorded for this board, so you'd add a module over "
                     "UART or SPI (e.g. an ESP-AT module or a network controller). Confirm in the datasheet.")
        if has("uart"):
            L.append("- UART connects that module (and your debug console).")
        return "\n".join(L)
    return ""


# --- Role detection (v2.5.0): decide the behavioural mode in Python, before the model runs -------

# Simulation-specific triggers: only THESE (with the Wokwi flag set) make the mentor the
# Reverse Mentor. A Wokwi user's design or debug question still gets Architect / Peer (v2.5.1).
_SIM_TRIGGERS = ("wokwi", "simulator", "virtual", "blocking", "boot pin", "boot0",
                 "can't export", "cannot export", "won't export")

# The user shared code or is reporting a fault — a debugging turn (shared by the Purpose gate).
_PEER_TRIGGERS = ("my code", "compiles but", "doesn't work", "nothing happens", "not working",
                  "wrong output", "review this", "check this", "what's wrong", "crashed",
                  "crashes", "won't run", "wont run", "won't boot", "wont boot", "freezes",
                  "hangs", "reboots", "stuck")


def detect_mentor_role(user_message: str, page_context: dict) -> str:
    """Pick the mentor's behavioural mode deterministically (docs/25, reordered in v2.5.1):
    SPONSOR -> PEER_MENTOR -> SYSTEM_ARCHITECT -> REVERSE_MENTOR(sim trigger + flag) -> ARCHITECT.
    The Wokwi flag informs the 'Try this' (point at the simulator) but no longer overrides the
    reasoning role for ordinary questions."""
    msg = (user_message or "").lower()

    # Role C — deterministic only, never needs the model.
    if (page_context.get("page_type") or "") in ("validate", "export"):
        return "SPONSOR"

    # Role B — the user shared code or is debugging.
    code = page_context.get("current_code") or ""
    if len(code) > 50 or any(t in msg for t in _PEER_TRIGGERS):
        return "PEER_MENTOR"

    # Role A — architectural / design / feasibility question.
    arch_triggers = ("should i use", "which board", "hal or", "bare metal", "where do i start",
                     "how do i design", "architecture", "which peripheral", "why would i",
                     "can i do", "is it possible", "what board", "robotics", "motor", "sensor",
                     "iot", "wifi", "bootloader", "fail-safe", "rtos", "driver")
    if any(t in msg for t in arch_triggers):
        return "SYSTEM_ARCHITECT"

    # Role D — only for a simulation-specific question on the Wokwi path.
    if page_context.get("wokwi_flag") and any(t in msg for t in _SIM_TRIGGERS):
        return "REVERSE_MENTOR"

    return "SYSTEM_ARCHITECT"


# --- Purpose Decision (the first-step gate): decide WHAT the turn is for, before any answer -------
#
# The mentor is not an answer engine. Before generating anything it chooses one OUTCOME:
#   ANSWER_NOW | ASK_CLARIFICATION | REDIRECT_TO_FOUNDATION | DECLINE_OUT_OF_SCOPE
# ANSWER_NOW is never the default — it requires BOTH a resolvable intent AND grounding in EAEDK's
# CURATED knowledge (its boards, concepts, capabilities, and reasoning topics). The user's question
# is the subject; the selected board is only context. Grounding reuses the EXISTING detectors, so
# this is a decision layer over what the system already knows — not a new keyword/domain list.

# Foundation intents — the learner wants to enter the FIELD / become an engineer (a career framing),
# not start a task. These get a learning path, not a board-bound technical answer.
_FOUNDATION_PHRASES = (
    "become a firmware", "become an embedded", "become a embedded", "firmware engineer",
    "embedded engineer", "embedded developer", "firmware developer", "want to become",
    "how do i become", "trying to become", "new to embedded", "new to firmware",
    "getting into embedded", "get into embedded", "break into embedded", "into firmware",
    "complete beginner", "career in",
)

# Direction intents — "where do I start" on THIS board: grounded by the board's learning path.
_DIRECTION_PHRASES = (
    "where do i start", "where to start", "where should i start", "where do i begin",
    "how do i start", "how should i start", "what should i build", "what do i build",
    "what should i make", "what to build", "get started", "getting started", "first project",
    "start with", "begin with", "what should i learn first",
)

# Field-entry words — a direction question aimed at the DISCIPLINE (not a board or peripheral) is a
# foundation question: "where to start in firmware / embedded / programming" → a learning path.
_FIELD_ENTRY = (" firmware", " embedded", " programming", " programmin", " to program",
                " coding", " to code", " software")

# Generic English words filtered out of the curated grounding vocabulary so common filler in a
# capability summary ("you turn on/off") never grounds an off-topic question.
_VOCAB_STOP = frozenset((
    "the", "and", "for", "with", "that", "this", "you", "your", "they", "them", "its", "are",
    "use", "used", "using", "one", "two", "four", "more", "most", "each", "same", "like", "via",
    "off", "out", "read", "reads", "turn", "turns", "talk", "talks", "connect", "connects", "send",
    "sending", "wired", "several", "small", "share", "attached", "often", "hold", "holds", "general",
    "purpose", "device", "computer", "custom", "other", "high", "speed", "precise", "periodic",
    "events", "event", "messages", "message", "files", "file", "data", "block", "blocks", "drive",
    "drives", "screen", "monitor", "things", "simplest", "wire", "wires", "line", "lines", "run",
    "reading", "writing", "first", "how", "what", "where", "debug", "between",
))

# Capitalised pronoun forms that are never an external subject.
_SUBJECT_STOP = {"i", "im", "ive", "id", "ill"}


@dataclass
class PurposeDecision:
    purpose: str                 # ANSWER_NOW | ASK_CLARIFICATION | REDIRECT_TO_FOUNDATION | DECLINE_OUT_OF_SCOPE
    reason: str = ""             # short rationale, for logs/tests
    subject: str = ""            # the out-of-scope subject (DECLINE_OUT_OF_SCOPE only)


def _seeks_foundation(text: str) -> bool:
    """The learner wants to enter the field / become an engineer — a career/foundation framing, not a
    specific grounded concept. Career detector + field-entry phrases, plus a 'where do I start in
    <firmware/embedded/programming>' direction question, which is field-entry, not a board start."""
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    if _is_career(text) or any(p in low for p in _FOUNDATION_PHRASES):
        return True
    direction = any(p in low for p in _DIRECTION_PHRASES)
    return direction and any(w in low for w in _FIELD_ENTRY)


def _grounding_vocab(conn: sqlite3.Connection) -> set[str]:
    """EAEDK's curated content vocabulary: capability names, the salient words of their plain-language
    summaries, and concept names. Used to tell an embedded question ('drive the LED', 'boot pins')
    from an off-topic one — derived from seeded data, not a hand-written keyword list."""
    vocab: set[str] = set()
    for r in repo.list_capabilities(conn):
        vocab.add(r["name"].lower())
        for w in re.findall(r"[a-z0-9]+", (r["summary"] or "").lower()):
            if len(w) >= 3 and w not in _VOCAB_STOP:
                vocab.add(w)
    for r in repo.list_concepts(conn):
        vocab.add(re.sub(r"[^a-z0-9]+", "", r["name"].lower()))
    return vocab


def _mentions_vocab(conn: sqlite3.Connection, text: str) -> bool:
    """The question contains a word from EAEDK's curated content vocabulary."""
    vocab = _grounding_vocab(conn)
    return any(w in vocab for w in re.findall(r"[a-z0-9]+", (text or "").lower()))


def _has_evidence(low: str, code: str) -> bool:
    """A fault report is answerable only WITH concrete diagnostic evidence — shared code, a fault
    ADDRESS or register value, a log/stack trace, or a line number. Naming the exception TYPE
    ('it crashed with a HardFault') is the symptom, not evidence; that still needs clarification."""
    if len(code or "") > 50:
        return True
    markers = ("traceback", "stack trace", "backtrace", "undefined reference", "panic:",
               "assert", "0x", "log:", "at line", "line ")
    if any(m in low for m in markers):
        return True
    return bool(re.search(r"\b0x[0-9a-f]+\b|\b\d{3,}\b", low))   # a fault address or concrete value


def _external_subject(text: str) -> str:
    """A mid-sentence, capitalised proper-noun phrase the question is really about (e.g. 'Nvidia
    Jetson'). Empty when it names no such entity. A named-entity signal, NOT a blocklist: sentence-
    initial capitals and the pronoun 'I' are ignored. Only consulted once a question has already
    failed to resolve to any grounded EAEDK knowledge."""
    phrases: list[str] = []
    cur: list[str] = []
    at_start = True
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#'-]*|[.?!]", text or ""):
        if tok in ".?!":
            if cur:
                phrases.append(" ".join(cur)); cur = []
            at_start = True
            continue
        proper = tok[:1].isupper() and not at_start and tok.lower().rstrip(".") not in _SUBJECT_STOP
        if proper:
            cur.append(tok)
        elif cur:
            phrases.append(" ".join(cur)); cur = []
        at_start = False
    if cur:
        phrases.append(" ".join(cur))
    return phrases[0] if phrases else ""


def _subject_grounded(conn: sqlite3.Connection, subject: str, board_name: str) -> bool:
    """Is the NAMED subject something EAEDK actually knows — a seeded board (by name or colloquialism),
    a word in the curated peripheral/concept vocabulary, or a recognised semantic-cost intent (gRPC,
    TLS, CoAP, …)? If none of these, it is out of scope."""
    low = _norm_words(subject)
    for r in repo.list_boards(conn):
        if any(f" {a} " in low for a in _board_aliases(r["name"])):
            return True
    if _mentions_vocab(conn, subject):
        return True
    return bool(semantic_cost.parse_intent(subject) or semantic_cost.detect_uncosted(subject))


def decide_purpose(conn: sqlite3.Connection, board_name: str, user_text: str,
                   page_context: dict, messages: list[dict] | None = None) -> PurposeDecision:
    """First-step gate: choose the turn's PURPOSE before any answer is generated (docs/29)."""
    msg = user_text or ""
    low = " " + msg.lower() + " "
    code = page_context.get("current_code") or ""
    page = page_context.get("page_type") or ""

    # Validate/Export context: the Validation Engine has a deterministic verdict to point at (SPONSOR).
    if page in ("validate", "export"):
        return PurposeDecision("ANSWER_NOW", "validation context")

    # Does the question resolve to EAEDK's CURATED knowledge? The selected board is context, never
    # grounding by itself — only another board the user actually NAMES counts.
    anchored = (reasoning.detect_topic(msg) is not None      # a known engineering decision
                or _detect_concept(conn, msg) is not None    # a known concept
                or _detect_domain(msg) is not None)          # a known project domain
    direction = any(p in low for p in _DIRECTION_PHRASES)    # "where do I start" on this board
    progress = any(k in low for k in _PROGRESS_Q)
    grounded = (anchored or direction or progress
                or bool(semantic_cost.parse_intent(msg))     # a named, costed intent (gRPC, TLS, …)
                or bool(semantic_cost.detect_uncosted(msg))  # a recognised intent without cost data (CoAP)
                or len(code) > 50                            # a code-review / studio turn
                or _mentions_vocab(conn, msg)                # curated peripheral/concept vocabulary
                or len(_mentioned_boards(conn, messages or [], board_name)) > 1)

    # (1) A fault report with no evidence yet — get the logs/error/code before answering.
    if any(t in low for t in _PEER_TRIGGERS) and not _has_evidence(low, code):
        return PurposeDecision("ASK_CLARIFICATION", "fault report without evidence")

    # (2) A field-entry / career question (not anchored to a specific concept) — give a learning
    #     path, not a board-bound technical answer.
    if _seeks_foundation(msg) and not anchored:
        return PurposeDecision("REDIRECT_TO_FOUNDATION", "learner seeking a starting point")

    # (3) An out-of-scope NAMED SUBJECT wins over weak grounding. A direction phrase ("where to
    #     start") grounds the ACTION, not the SUBJECT — so "where to start in NVIDIA Jetson" must
    #     still be declined. Skip when the question is anchored to a concept/topic/domain we teach
    #     (e.g. "Zephyr or FreeRTOS" anchors the RTOS topic) or when the subject is a board / vocab
    #     term EAEDK actually knows.
    if not anchored:
        subject = _external_subject(msg)
        if subject and not _subject_grounded(conn, subject, board_name):
            return PurposeDecision("DECLINE_OUT_OF_SCOPE",
                                   "named subject outside grounded knowledge", subject)

    # (4) Nothing resolves to grounded knowledge — and no named subject to decline.
    if not grounded:
        return PurposeDecision("ASK_CLARIFICATION", "intent not resolvable")

    # (5) Grounded AND intent understood — only now may the mentor answer.
    return PurposeDecision("ANSWER_NOW", "grounded; intent clear")


def render_purpose(conn: sqlite3.Connection, decision: PurposeDecision,
                   board_name: str, path: list, active_boards: list[str] | None = None) -> str:
    """Render a non-answer outcome. None of these bind the answer to the selected board as the subject
    — that is the whole point of the gate."""
    if decision.purpose == "REDIRECT_TO_FOUNDATION":
        return _career_roadmap(board_name, path, active_boards)
    if decision.purpose == "DECLINE_OUT_OF_SCOPE":
        subj = decision.subject or "that"
        return (
            f"I'd be guessing about {subj}, and I won't pretend otherwise — it's outside the hardware "
            "EAEDK actually has verified facts for. I'm grounded in a specific set of microcontroller "
            "and single-board-computer targets and the embedded concepts that transfer across them; "
            f"{subj} isn't one of them.\n\n"
            f"If {subj} is genuinely what you need, EAEDK isn't the right tool for it yet — I'd rather "
            "tell you that than invent an answer. If you're here to learn embedded firmware, tell me "
            "your goal and I'll ground it in hardware I can reason about honestly.\n\n"
            "Question: what are you trying to build or learn?")
    # ASK_CLARIFICATION — two flavours, both ask rather than guess.
    if decision.reason.startswith("fault report"):
        return (
            "Naming the fault is the symptom, not the evidence — I'd just be guessing without more. "
            "Give me something concrete and I can reason about it: the faulting ADDRESS (the PC/LR the "
            "CPU stacked), the fault status registers your handler can read (CFSR/HFSR on Cortex-M), "
            "the last line it logged before it died, or the code in the function it crashed in.\n\n"
            "Question: what was it doing when it faulted, and what address did it fault at?")
    return (
        "I don't have enough to answer that well yet, and I'd rather ask than guess. Tell me what "
        "you're trying to do, and on which board, and I'll point you at the first real step.\n\n"
        "Question: what's your goal here?")


# --- Domain-aware "Try this" (chosen in Python, not left to the model) ---------------------------

DOMAIN_TRY_THIS = {
    "robotics":      "Generate one PWM signal on {timer} and sweep the duty cycle 0-100% — that is motor "
                     "speed control. Watch it in Wokwi before touching a real motor.",
    "motor":         "Sweep a servo 0-180 degrees at 50Hz PWM — it proves your timer period and "
                     "duty-cycle math are correct.",
    "sensor":        "Write a blocking I2C read of one register and print the raw value over UART — "
                     "before interpreting it, prove the bus is talking.",
    "audio":         "Generate a square wave at 440Hz on a timer output — that is concert A. If you hear "
                     "it, your timer and GPIO are working.",
    "iot":           "Connect to Wi-Fi and print your IP over UART — before sending data, prove the stack "
                     "initialises cleanly.",
    "secure_boot":   "Sketch your boot chain on paper — ROM, then bootloader, then app — and mark where "
                     "each stage VERIFIES the next before it jumps. The first arrow with no verification "
                     "is your vulnerability; that's what secure boot closes.",
    "bootloader":    "Write 4 bytes to flash, read them back, and compare — if they match, your flash "
                     "driver works.",
    "linker_script": "Open your project's linker script (the .ld file), halve the FLASH region's LENGTH, "
                     "and rebuild. Read the linker's 'region overflowed' error — it shows you exactly how "
                     "the script, not the compiler, decides what fits where.",
    "ml_inference":  "Before writing any inference code, get your model's size (the .tflite / weights "
                     "array in bytes) and compare it against this board's RAM and flash. Most ML-on-MCU "
                     "projects live or die on that one comparison — do it first.",
    "memory_layout": "Add one large static array to your program, build it, and open the .map file — "
                     "watch .bss grow and free RAM shrink. The map file, not a guess, is the truth about "
                     "where every byte lives.",
    "driver":        "Write to one register, read it back, and verify the value — before any protocol, "
                     "prove register access works.",
    "default":       "Enable the peripheral clock, configure one pin, and toggle it in a loop — before "
                     "any protocol, prove the pin moves.",
}

# Specific topics first so they win over the broader ones (secure_boot before bootloader; the
# ml/linker/memory topics before any generic match).
_TRY_THIS_KEYWORDS = (
    ("secure_boot",   ("secure boot", "chain of trust", "signature verif", "verify the signature",
                       "anti-rollback", "rollback counter", "boot chain", "root of trust")),
    ("linker_script", ("linker", "memory.ld", ".ld file", "ld script", "scatter file",
                       "linker script", "memory section", "memory map")),
    ("ml_inference",  ("neural network", "tensorflow", "tflite", "tinyml", "inference", "ml model",
                       "machine learning model", "run a model", "ai model", "cnn ", "gesture recogn")),
    ("memory_layout", ("memory layout", ".bss", ".data section", "stack overflow", "heap vs stack",
                       "where do variables", "where variables live", "ram usage", "stack vs heap")),
    ("bootloader",    ("bootloader", "fail-safe", "failsafe", "ota", "rollback")),
    ("driver",        ("driver", "device tree", "of_match", "register map")),
    ("motor",         ("servo",)),
    ("robotics",      ("robot", "robotics", "motor", "wheel", "drone", "bldc", "stepper", "actuator")),
    ("sensor",        ("sensor", "imu", "accelerometer", "gyro", "temperature", "humidity", "distance",
                       "lidar", "proximity")),
    ("audio",         ("audio", "sound", "speaker", "microphone", "music", "i2s")),
    ("iot",           ("iot", "wifi", "wi-fi", "bluetooth", "ble", "mqtt", "internet of")),
)

# A career / learning-path question has NO single hands-on experiment — it gets a roadmap, and the
# 'Try this' is SUPPRESSED rather than faked with an irrelevant blink (v2.7 P3). Kept specific so a
# genuine "where do I start with this board" still gets an experiment.
_CAREER_KW = ("career", "get a job", "land a job", "get into embedded", "break into embedded",
              "become an embedded", "roadmap to", "learning roadmap", "study plan", "curriculum",
              "what should i learn", "how do i learn embedded", "learn embedded systems",
              "job in embedded", "embedded career")


def _is_career(text: str) -> bool:
    low = " " + (text or "").lower() + " "
    return any(k in low for k in _CAREER_KW)


def _tt_norm(s: str) -> str:
    """Normalise a 'Try this' for repeat-detection: first 40 chars, lowercased, whitespace-collapsed
    (well before any appended Wokwi suffix)."""
    return " ".join((s or "").lower().split())[:40]


def _used_try_this(messages: list[dict]) -> set[str]:
    """The experiments already offered this session, so we never repeat one (v2.7 P3)."""
    used: set[str] = set()
    for m in messages or []:
        if m.get("role") != "assistant":
            continue
        for line in (m.get("content") or "").splitlines():
            low = line.strip().lower()
            if low.startswith("try this:"):
                used.add(_tt_norm(low[len("try this:"):]))
    return used


def _select_try_this(text: str, family: str | None,
                     used: set[str] | frozenset[str] = frozenset()) -> str | None:
    """The first experiment to run, matched to the topic the user named. Returns None to SUPPRESS the
    'Try this' — for a career/learning question (which gets a roadmap instead) or when the only
    experiment we'd offer was already given this session. Family-gated: the robotics/motor experiment
    names TIM1 only on STM32. When no topic is named, fall back to the board family's own experiment
    (F_CPU on AVR, RCC on STM32, …)."""
    if _is_career(text):
        return None                                    # roadmap, not an experiment
    low = " " + (text or "").lower() + " "
    key = next((k for k, kws in _TRY_THIS_KEYWORDS if any(w in low for w in kws)), None)
    if key is None:
        candidate = _board_try_this(family)
    else:
        candidate = DOMAIN_TRY_THIS[key]
        if "{timer}" in candidate:
            candidate = candidate.replace("{timer}", "TIM1" if family == "stm32" else "a timer output")
    if _tt_norm(candidate) in used:
        return None                                    # never repeat the same experiment in a session
    return candidate


def _tt_block(try_this: str | None) -> str:
    """The 'Try this' paragraph, or nothing when it is suppressed."""
    return f"\n\nTry this: {try_this}" if try_this else ""


# --- Multi-board context retention (v2.7 P4A) ----------------------------------------------------
# Informal names the user is likely to type, mapped to the canonical board. The board's own name
# (and its hyphen/space variants) is always matched too — this only adds the colloquialisms.
_BOARD_ALIASES = {
    "STM32F103-BluePill":     ("blue pill", "bluepill", "stm32f103", "f103c8"),
    "Nucleo-F103RB":          ("nucleo f103", "f103rb"),
    "Nucleo-F411RE":          ("nucleo f411", "f411", "f411re"),
    "STM32H743":              ("h743", "stm32h7"),
    "STM32MP157":             ("mp157", "stm32mp1", "stm32mp157"),
    "ESP32-DevKitC":          ("esp32", "esp 32", "devkitc"),
    "Raspberry-Pi-Pico":      ("pico", "rp2040", "raspberry pi pico"),
    "WIZnet-W5500-EVB-Pico":  ("w5500", "wiznet"),
    "Arduino-Uno":            ("uno", "atmega328", "arduino uno"),
    "Arduino-Mega":           ("mega", "atmega2560", "arduino mega"),
    "Raspberry-Pi-4":         ("pi 4", "raspberry pi 4", "rpi4", "bcm2711"),
    "BeagleBone-Black":       ("beaglebone", "bbb", "am335"),
    "i.MX8M-Mini-EVK":        ("imx8", "imx8m", "i mx8"),
    "RTL8722DM":              ("rtl8722", "ameba"),
}


def _norm_words(s: str) -> str:
    """Lowercase, drop punctuation to spaces, collapse — so ' uno ' matches but 'announce' doesn't."""
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip() + " "


def _board_aliases(name: str) -> set[str]:
    al = {name.lower(), name.lower().replace("-", " "), name.lower().replace("-", "")}
    al |= set(_BOARD_ALIASES.get(name, ()))
    return {_norm_words(a).strip() for a in al if a.strip()}


def _mentioned_boards(conn: sqlite3.Connection, messages: list[dict], selected: str) -> list[str]:
    """Every board referenced anywhere in the conversation — the selected board first, then any the
    user named by name or colloquialism, deduped in mention order (v2.7 P4A)."""
    text = _norm_words(" ".join((m.get("content") or "") for m in messages or []))
    active = [selected]
    for r in repo.list_boards(conn):
        n = r["name"]
        if n == selected:
            continue
        if any(f" {a} " in text for a in _board_aliases(n)):
            active.append(n)
    return active


def _and_join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _career_roadmap(board_name: str, path: list, active_boards: list[str] | None = None) -> str:
    """A learning *sequence* for a career/learning question — the deterministic answer that replaces a
    single canned experiment (v2.7 P3). When several boards are in play it sequences across all of
    them and stresses transfer (v2.7 P4A)."""
    boards = active_boards or [board_name]
    multi = len(boards) > 1
    if multi:
        lead = (f"You've mentioned {_and_join(boards)}. Career and skill aren't a single experiment "
                "or a single board — they're a sequence you can run on any of them, because the "
                "reasoning transfers. A sound order:")
    else:
        lead = ("Career and skill aren't a single experiment — they're a sequence. On "
                f"{board_name} a sound order is:")
    if path:
        steps = "\n".join(f"{i}. {s['title']} — {s['why']}" for i, s in enumerate(path, 1))
        tail = ("\nGo deep on each before the next; what you learn on one of these boards transfers "
                "directly to the others." if multi else
                "\nGo deep on each before the next; the reasoning transfers to every other board.")
        return f"{lead}\n{steps}{tail}"
    return ("Embedded skill is built in order, not in one experiment: blink, then timers/PWM, then "
            "interrupts, then a bus (UART/I2C/SPI), then a real peripheral, then an RTOS or "
            "bootloader. Master one board deeply; the reasoning transfers everywhere.")


# --- One self-contained few-shot prompt per role (the model never decides which role to play) ----

_ARCH_GUARD = ("\nNote: the register names and AF numbers in the examples are STM32 teaching values. "
               "Reason for the board above; never assert a register, clock, or timing value you were "
               "not given — name the datasheet table to confirm it instead.")


def _role_ctx(board_name, soc, board, caps_set, project, step, current_code,
              family=None, reasoning="") -> dict:
    flash, ram = board.get("flash_bytes"), board.get("ram_bytes")
    return {"board_name": board_name, "arch": soc["arch"], "family": family,
            "peripherals": ", ".join(sorted(caps_set)) or "(none recorded)",
            "flash": f"{flash // 1024}KB" if isinstance(flash, int) else "unknown",
            "ram": f"{ram // 1024}KB" if isinstance(ram, int) else "unknown",
            "project": project or "(none)", "step": step or "(not started)",
            "current_code": (current_code or "(none provided)")[:1500],
            "reasoning": reasoning}


_ARCHITECT_TEMPLATE = """You are an engineering MENTOR, not an answer engine. A learner asked a design,
concept, or feasibility question. Your job is to teach them to THINK like a firmware engineer so they
can solve future problems on their own. NEVER open with implementation — no code, registers, APIs,
SDKs, or board-specific detail until you have established the problem and the options.

Follow this contract every time:
1. What is the real problem being solved?
2. Why does it exist on real hardware?
3. What approaches exist?
4. What are the trade-offs of each?
5. How would an engineer decide — the questions to ask.
6. Only then, briefly, the recommended next step.
The board's facts ENRICH a trade-off; they do not drive the reasoning. The same thinking must hold on
STM32, RP2040, ESP32, AVR, or a Linux SBC.

Board (facts to enrich your reasoning, never to lead with): {board_name} | {arch}
Peripherals: {peripherals} | Flash: {flash} | RAM: {ram}
Project: {project} | Stage: {step}
{reasoning}
STUDY THESE EXAMPLES (notice: the problem and the trade-offs come FIRST; the answer is the small last step):

EXAMPLE — "should I use HAL or bare metal?"
WRONG: "Both have advantages. HAL is easier; bare metal gives more control."
RIGHT: "First — are you optimising to LEARN, or to ship on a deadline? That decides this. The real
choice is understanding vs speed. HAL wraps the registers so you ship fast and port across the family,
but it hides how the hardware works — fine until it breaks and you can't see why. Bare-metal means you
write the registers yourself: slow, but you understand and can debug every line. A middle path exists
(low-layer register helpers). Trade-off: HAL buys development speed at the cost of understanding and
code size; bare-metal buys control and debuggability at the cost of time. To decide, ask: learning or
shipping? how many peripherals? will this port to other chips? Since you want to learn firmware — write
one UART both ways and feel the difference. What matters more to you right now: speed, or understanding?"

EXAMPLE — "do I need an RTOS?"
WRONG: "An RTOS lets you run multiple tasks. Use FreeRTOS."
RIGHT: "Before any RTOS — what problem are you actually solving? An RTOS solves INDEPENDENT timing:
several activities each with their own deadline, where one slow step would starve the others. With one
loop you don't have that problem yet. Your options: a super-loop (trivial, but everything is coupled),
a super-loop plus interrupts (handles urgent events, still one thread), or an RTOS (independent
prioritised tasks — but it costs RAM per task stack and adds concurrency bugs: priority inversion,
deadlock, stack overflow). The trade-off is independent timing and modularity versus RAM and
complexity. Decide by asking: do I truly have two-plus independent deadlines? is a blocking call
stalling time-critical work? do I have RAM for the stacks? Most projects should start as a super-loop
and add an RTOS only when faking concurrency with flags becomes the bug. What independent deadlines do
you actually have?"

Now answer the user with this contract — problem and trade-offs first, the recommendation last, ending
with one question that makes them reason. Board facts enrich; never lead with implementation; never
list peripherals generically.""" + _ARCH_GUARD


_PEER_HEAD = """You are a colleague sitting next to the engineer who fell into the same trap
yesterday. You look at their code and find the ONE hardware consequence they have not considered. You
never comment on style or unused variables.

Board: {board_name} | Arch: {arch}
Peripherals: {peripherals}
Flash: {flash} | RAM: {ram}
Current code:
{current_code}

STUDY THESE EXAMPLES (they are for THIS board's chip family — reason from them):
"""

_PEER_TAIL = """
Now review the user's code or question in exactly this style. Find the one hardware consequence they
have not considered. Never comment on style. End with one specific question about their code.""" \
    + _ARCH_GUARD

# One BAD/GOOD pair per chip family — the model sees the family that matches the actual board.
_PEER_EXAMPLES = {
    "stm32": """
EXAMPLE 1 — "my code compiles but nothing happens" (STM32)
WRONG: "There could be several reasons. Check your connections and toolchain."
RIGHT: "Three things cause this on STM32, in order: 1) Clock not enabled — every peripheral is off by
default; configuring UART registers before enabling its APB clock writes to dead hardware, no error,
just silence. Did you enable the USART clock in RCC first? 2) Wrong alternate function — PA9 can be
USART1_TX (AF7) or TIM1_CH2 (AF1); on AF1 the pin is a timer output, not UART. Which AF did you write
to GPIOA->AFR? 3) Wrong baud divisor — if BRR is computed for the wrong clock, the terminal sees
garbage. Which of the three did you check first?"

EXAMPLE 2 — engineer submits UART init code
WRONG: "Looks good. Add error handling and use const where appropriate."
RIGHT: "You enabled the USART clock and set the pin modes — clean. One thing before you flash: which
alternate-function number did you pass to the TX pin? If it's the timer AF instead of the UART AF, the
code compiles, the board is silent, the console is dead. Check the alternate-function table — what AF
value did you write?"
""",
    "avr": """
EXAMPLE 1 — "my code compiles but nothing happens" (AVR / ATmega)
WRONG: "Make sure the peripheral clock is enabled before you use the peripheral."
RIGHT: "On AVR there is no peripheral clock to gate — the #1 cause is F_CPU and the fuse bits. If the
F_CPU you define in code does not match the clock the fuses actually select (the internal 1MHz/8MHz
oscillator vs an external 16MHz crystal), then _delay_ms() runs at the wrong speed AND your UART baud
divisor (UBRR, computed from F_CPU) is wrong — the serial monitor shows garbage or nothing. Does your
F_CPU match the clock the low fuse (CKSEL) selects? And is UBRR computed from that same F_CPU? Which
did you check first — the fuse/clock, or the baud math?"
""",
    "esp32": """
EXAMPLE 1 — "my code compiles but nothing happens" (ESP32)
WRONG: "Check which alternate-function number you assigned to the pin."
RIGHT: "On ESP32 the cause is rarely a pin function — it is usually that you blocked the WiFi core. The
WiFi/BLE stack runs on Core 0; a long blocking loop on that core (a busy-wait, a long delay with no
yield, a blocking read) starves the idle task, the task watchdog fires, and the chip reboots — it
looks like 'nothing happens' but is a reboot loop. Are you blocking on Core 0? Pin the heavy task to
Core 1 with xTaskCreatePinnedToCore, or break it up with vTaskDelay so the watchdog is fed. Second:
did nvs_flash_init() run before any WiFi call — a bad NVS partition reboot-loops too. Which are you
hitting — a blocked core, or NVS?"
""",
    "rp2040": """
EXAMPLE 1 — "my code compiles but nothing happens" (RP2040)
WRONG: "Enable the APB2 clock for the peripheral first."
RIGHT: "RP2040 has no APB2 — that is an STM32 register. Two RP2040-specific traps cause silent failure:
1) Multicore — if you launched code on core1 (multicore_launch_core1) and it touches a resource core0
also uses without a lock or the inter-core FIFO, you get a race that hangs one core with no error.
2) PIO — a PIO state machine needs its clock divider set, its program loaded, AND the state machine
enabled (pio_sm_set_enabled); miss the enable and the PIO sits idle silently. Which are you using —
multicore, or PIO? That tells us where the silence is."
""",
}
_PEER_EXAMPLES["default"] = _PEER_EXAMPLES["stm32"]


_REVERSE_TEMPLATE = """You are a mentor who knows the difference between what matters in a real circuit
and what the simulator handles for you. You never block a beginner from learning because the simulator
skips a physical constraint they don't need yet.

Board: {board_name} | Arch: {arch}
Target: Wokwi simulation (not physical hardware)
Peripherals: {peripherals}

STUDY THESE EXAMPLES:

EXAMPLE 1 — "the validator is blocking my bootloader export" (boot-pin UNKNOWN)
WRONG: "You need to configure your boot pins before export."
RIGHT: "In Wokwi you don't need to worry about boot pins — the simulator handles boot mode
automatically. Boot-pin configuration only matters with a physical programmer on real hardware. For
your simulation, proceed; the physical boot-pin warning is advisory here, not a blocker. When you move
to a real board, that's when you confirm BOOT0 is LOW for normal boot and HIGH to enter the bootloader
for flashing — a real-hardware concern, not Wokwi."

EXAMPLE 2 — "I want to test my power sequencing in Wokwi"
WRONG: "Wokwi supports power sequencing simulation."
RIGHT: "Wokwi doesn't emulate power-rail sequencing — that's physical behaviour the simulator skips.
What it emulates well: GPIO, UART, SPI, I2C, timers, interrupts — enough for most beginner and
intermediate firmware. Power sequencing matters when multiple rails must come up in order to protect
hardware — a real-PCB concern. For now, what are you actually trying to test? If it's code logic, Wokwi
helps; if it's hardware power behaviour, you need a real board."

Now respond knowing the user is on Wokwi. Never block learning with physical-hardware constraints the
simulator doesn't need. Explain what Wokwi does and does not emulate when relevant, and end with one
question.""" + _ARCH_GUARD


def build_architect_prompt(ctx: dict) -> str:
    return _ARCHITECT_TEMPLATE.format(**ctx)


def build_peer_mentor_prompt(ctx: dict) -> str:
    examples = _PEER_EXAMPLES.get(ctx.get("family") or "default", _PEER_EXAMPLES["default"])
    return _PEER_HEAD.format(**ctx) + examples + _PEER_TAIL


def build_reverse_mentor_prompt(ctx: dict) -> str:
    return _REVERSE_TEMPLATE.format(**ctx)


_ROLE_BUILDERS = {"SYSTEM_ARCHITECT": build_architect_prompt,
                  "PEER_MENTOR": build_peer_mentor_prompt,
                  "REVERSE_MENTOR": build_reverse_mentor_prompt}


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


# P1 — the LLM/Engine trust boundary. A chat reply must never offer optimisation advice over a
# project the Validation Engine has already proved NOT FEASIBLE. This banner is prepended in Python
# at every return site, so no rephrasing of the question routes around it.
_NOT_FEASIBLE_BANNER = "⛔ STOP — this project is NOT FEASIBLE."


def _feasibility_guard(conn, project_name: str | None) -> str:
    """Return the hard NOT-FEASIBLE banner (with the blocking failures) when the project's current
    feasibility is ``not_feasible``; else ''. Deterministic; reads the Validation Engine."""
    if not project_name:
        return ""
    p = repo.get_project(conn, project_name)
    if p is None:
        return ""
    from .orchestrator import assess_project
    resp = assess_project(conn, p)
    if resp.feasibility != "not_feasible":
        return ""
    fails = [f"{v['check']}: {v['reason']}" for v in resp.validations
             if v.get("gating", True) and v["status"] == "FAIL"]
    blockers = "\n".join(f"  - {f}" for f in fails) or "  - a hard validation failure"
    return (f"{_NOT_FEASIBLE_BANNER} The Validation Engine already proved a HARD hardware limit, not "
            f"a tuning problem:\n{blockers}\n"
            "No optimisation (quantization, -Os, pruning) changes a physical limit — fix the numbers "
            "above or move to a larger board before anything else.\n\n")


# Milestone 2 (docs/31): voice the deterministic proof-path packet as a human mentor. Single pass,
# no Actor-Critic. The engine already chose the pattern, node, branch and proof step — the model only
# adds tone, reassurance, and wording, and may NOT invent any board-specific fact.
_PROOF_VOICE_SYSTEM = (
    "You are a senior firmware mentor guiding a beginner through a debugging PROOF PATH. You are given "
    "an APPROVED proof-path packet from a deterministic engine. Voice it as a warm, concise, human "
    "mentor — you choose the tone, a short reassurance, the wording, and the follow-up phrasing.\n"
    "HARD RULES — you may NOT change the engineering:\n"
    "- Keep the proof step's ACTION exactly as given (reword for flow, never change what to do).\n"
    "- Do not change which problem this is, which causes remain, or what comes next.\n"
    "- State NO board-specific fact — no pin names, register names, UART/USART instance numbers, clock "
    "frequencies, addresses, or MCU-specific setup — unless it appears verbatim in the packet. If the "
    "learner needs those, ASK for them (the packet lists what to ask).\n"
    "- Talk like a person: a few short sentences, no tables. End with the packet's follow-up (ask for "
    "the listed evidence on the first step, or ask them to report the result on a branch).")


def _voice_proof_path(state, board_name: str, use_llm: bool, gateway: Gateway | None) -> str:
    """Voice the proof-path node as a mentor when a model is available; otherwise (and whenever the
    voiced answer invents a board-specific fact) fall back to the deterministic, already-safe render.
    The engine's node/branch/proof-step are authoritative — the model only changes the voice."""
    deterministic = problem_patterns.render_proof_path(state, board_name)
    if not use_llm:
        return deterministic
    gw = gateway or Gateway()
    if not gw.available():
        return deterministic
    packet = problem_patterns.build_packet(state, board_name)
    prompt = problem_patterns.render_packet_for_prompt(packet) + "\n\nVoice this now, as the mentor:"
    try:
        voiced = gw.provider.generate(_PROOF_VOICE_SYSTEM, prompt).strip()
    except Exception:
        return deterministic
    safe, _violations = problem_patterns.verify_voiced(voiced, packet)
    if not safe or len(voiced) < 20:
        return deterministic                 # blocked: invented board fact → safe deterministic render
    return voiced


def mentor_chat(conn: sqlite3.Connection, board_name: str, messages: list[dict],
                use_llm: bool = False, gateway: Gateway | None = None,
                project: str | None = None, has_hardware: bool = False,
                extra_context: str = "", page_type: str = "", current_code: str = "") -> str:
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
    domain = _detect_domain(last_user)
    topic = reasoning.detect_topic(last_user)        # v2.6.0: an engineering decision -> the framework
    ram_kb = board["ram_bytes"] // 1024 if isinstance(board.get("ram_bytes"), int) else None
    flash_kb = board["flash_bytes"] // 1024 if isinstance(board.get("flash_bytes"), int) else None
    flash_base = board["flash_base"] if isinstance(board.get("flash_base"), int) else None
    ram_base = board["ram_base"] if isinstance(board.get("ram_base"), int) else None
    cap_set = {c["capability"] for c in caps}
    role = detect_mentor_role(last_user, {"page_type": page_type,
                                          "wokwi_flag": (not has_hardware),
                                          "current_code": current_code})
    career = _is_career(last_user)                     # P3: career -> roadmap, suppress 'Try this'
    active_boards = _mentioned_boards(conn, messages, board_name)  # P4A: every board in the convo

    # First-step Purpose gate (docs/29): the COARSE confusion classifier.
    purpose = decide_purpose(conn, board_name, last_user,
                             {"page_type": page_type, "current_code": current_code or extra_context},
                             messages)

    # Central Navigator (docs/32): classify the user's CONFUSION TYPE and route to the right KIND of
    # guidance. The five modes are first-class; adding a topic/pattern/map is DATA, not router code.
    route = navigator.classify(purpose, messages)
    if route.mode == navigator.PROOF_PATH:                    # broken-system → guided proof path
        return _voice_proof_path(route.proof_state, board_name, use_llm, gateway)
    if route.mode == navigator.LEARNING_MAP:                  # learning-direction → a route, not a dump
        if route.learning_map is not None:
            return navigator.render_learning_map(conn, route.learning_map, board_name)
        return render_purpose(conn, purpose, board_name, path, active_boards)   # foundation / career
    if route.mode in (navigator.DECLINE, navigator.CLARIFY):  # out-of-scope / too-vague
        return render_purpose(conn, purpose, board_name, path, active_boards)
    # DECISION_MAP (a seeded reasoning.Topic) and TEACH continue into the decision/teaching pipeline.

    # Board SELECTION is fleet-wide: the user is asking WHICH board to choose, so the selected board is
    # not the subject. Answer deterministically from every seeded board's verified geometry + the cost
    # table — never the selected-board cost override, never the LLM (which would hallucinate boards).
    if topic and topic.key == "board_selection":
        sel_terms = semantic_cost.parse_intent(last_user)
        sel_unknown = semantic_cost.detect_uncosted(last_user)
        if sel_terms or sel_unknown:
            return _feasibility_guard(conn, project) + semantic_cost.recommend_chat(
                conn, sel_terms, sel_unknown)

    used = _used_try_this(messages)                    # P3: never repeat an experiment this session
    try_this = _select_try_this(last_user, fam, used)  # domain-aware, family-gated, may be None
    if try_this and not has_hardware and "wokwi" not in try_this.lower():
        try_this += " (run it in the Wokwi simulator — no hardware needed)."
    question = _followup_question(caps, path)
    progress = _progress_summary(conn, project)
    guard = _feasibility_guard(conn, project)        # P1: hard NOT-FEASIBLE banner, prepended below
    sem_note = semantic_cost.chat_note(conn, board_name, last_user)  # P2B: grounded intent cost
    lead = guard + (sem_note + "\n\n" if sem_note else "")           # deterministic prefix on every reply

    # "How am I doing / what's next" -> answer straight from the State Engine (never the LLM).
    if progress and any(k in last_user.lower() for k in _PROGRESS_Q):
        if progress["next"]:
            head = (f"You're at {progress['complete']}/{progress['total']} "
                    f"({progress['percent']}%). Your next task is '{progress['next']['title']}'. "
                    f"Why it matters: {progress['next']['why_it_matters']}")
        else:
            head = (f"You're at {progress['complete']}/{progress['total']} — every item is "
                    "complete. Nice work.")
        return lead + f"{head}{_tt_block(try_this)}\n\n{question}"

    # Deterministic backbone — a real answer even with no model, always ending in an action.
    # An engineering decision teaches the reasoning FRAMEWORK (v2.6.0); a named project type reasons
    # about this board's peripherals (v2.4.1); both ahead of the generic "start with blink" default.
    if career:                                           # P3/P4A: roadmap across all active boards
        head = _career_roadmap(board_name, path, active_boards)
    elif topic:
        head = reasoning.render(topic, board_name, soc["arch"], fam, ram_kb, flash_kb, flash_base, ram_base)
    elif domain:
        head = _domain_reasoning(domain, cap_set, fam, board_name)
    elif concept is not None:
        head = f"{concept['anchor']}"
    elif path:
        head = (f"Good question. For {board_name}, the place to start is '{path[0]['title']}' — "
                f"{path[0]['why']}")
    else:
        head = f"For {board_name}, tell me what you want to do and I'll point you at the first step."
    backbone = f"{head}{_tt_block(try_this)}\n\n{question}"

    gw = gateway or Gateway()
    note = _llm_or_note(use_llm, gw)
    if note is not None:
        return lead + backbone                       # offline: the structured deterministic answer

    # Role C — the Validation Engine's verdict is deterministic; the chat points there, never the model.
    if role == "SPONSOR":
        return lead + ("That's the Validation Engine's call, not mine — open Validate or Export to "
                "see exactly what passed, failed, or is unknown, and why. I explain those results; I "
                "never override them.\n\nTry this: run Validate and read the first FAIL or UNKNOWN.\n\n"
                "Question: which check is blocking you?")

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
    extra_line = (extra_context.strip() + "\n") if extra_context and extra_context.strip() else ""
    dom_block = ""
    if domain:
        dom_block = (f"PROJECT DOMAIN — the user named a '{domain}' project. Reason about WHICH of "
                     "this board's peripherals it needs and why; build on this, stay specific:\n"
                     + _domain_reasoning(domain, cap_set, fam, board_name) + "\n")
    feas_line = ""
    if guard:                                        # P1: the model must reason WITHIN the hard limit
        feas_line = ("HARD CONSTRAINT — this project is NOT FEASIBLE (the Validation Engine proved a "
                     "physical hardware limit). Open your reply by stating this failure; do NOT "
                     "suggest optimisation as if it could work as-is:\n" + guard + "\n")
    boards_line = ""
    if len(active_boards) > 1:                            # P4A: retain every board the user named
        boards_line = (f"The user has referenced multiple boards: {_and_join(active_boards)}. For a "
                       "career/learning question, sequence learning across ALL of them and stress how "
                       "the reasoning transfers between them.\n")
    sem_line = ""
    if sem_note:                                         # P2B: the cost data is already shown to the user
        sem_line = ("COST DATA (seeded estimates, already stated to the user above — reason WITHIN it, "
                    "do not contradict it or invent different numbers):\n" + sem_note + "\n")
    prompt = (f"CONTEXT\nBoard: {board_name} ({soc['arch']})\n{hw}\n{boards_line}{sem_line}{feas_line}"
              f"This board HAS these peripherals: {have}\n{geo_line}{step_line}{prog_line}{dom_block}{extra_line}"
              f"Capabilities (reason from these, not generic):\n{cap_lines}\nLearning path:\n{path_lines}\n"
              + (f"Concept anchor (true; build on this): {concept['anchor']}\n" if concept else "")
              + (f"When you suggest an experiment, use this 'Try this': {try_this}\n" if try_this
                 else "Do NOT end with a 'Try this:' experiment — this is a career/learning question "
                      "(or its experiment was already given). End with a short learning roadmap and a "
                      "question instead.\n")
              + f"\nCONVERSATION SO FAR:\n{history}\n\nReply now (answer + a question):")
    # The model receives a prompt that already IS the detected role (board context before examples).
    step = path[0]["title"] if path else None
    reasoning_block = ""
    if topic:                                        # ground the Architect in the framework reasoning
        reasoning_block = ("Engineering reasoning for this question — elaborate on it, never "
                           "contradict it:\n"
                           + reasoning.render(topic, board_name, soc["arch"], fam, ram_kb, flash_kb, flash_base, ram_base)
                           + "\n")
    system = _ROLE_BUILDERS.get(role, build_architect_prompt)(
        _role_ctx(board_name, soc, board, cap_set, project, step, current_code, fam, reasoning_block))
    # Actor pass — the model proposes. A live-model hiccup (timeout, dropped connection, the model
    # answered the availability ping but stalls on generation) must NEVER crash the request — degrade
    # to the deterministic backbone, which is already a complete grounded answer.
    try:
        raw = gw.provider.generate(system, prompt)
    except Exception:
        return lead + backbone
    # Critic pass — the model reviews its own answer (P4; runs on every online response).
    grounding = f"{sem_line}{feas_line}Board: {board_name} ({soc['arch']}); peripherals: {have}."
    critiqued = arbiter.critic_review(gw, system, raw, grounding)
    filtered, removed = filter_text(critiqued, build_board_allowlist(conn, board_name))
    answer = filtered.strip()
    # Enforce the contract even if the model (or the post-filter) dropped a part.
    if len(answer) < 20:
        answer = backbone
    else:
        if try_this and "try this" not in answer.lower():
            answer += f"\n\nTry this: {try_this}"
        if not answer.rstrip().rstrip("*_# ").endswith("?") and "question:" not in answer.lower():
            answer += f"\n\n{question}"           # already ends in a question? don't append a second
    # Arbiter pass — deterministic, final say. Discards the prose on any hard fail (P4).
    arb = arbiter.arbitrate(conn, board_name, project, last_user, answer)
    if arb.overridden:
        return lead + arb.text                       # the Validation Engine wins; Actor text dropped
    return lead + answer                             # P1/P2B: hard limit + cost data prefixed, always


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
    try:                                                 # a model timeout must degrade, not crash
        raw = gw.provider.generate(_EXPLAIN_SYSTEM, prompt)
    except Exception:
        return base + "\n[mentor] LLM unreachable (timed out); showing the deterministic anchor above."
    filtered, removed = filter_text(raw, build_board_allowlist(conn, board_name))
    return f"{base}\n\n{filtered}\n[mentor] {removed} uncited hardware claim(s) removed."
