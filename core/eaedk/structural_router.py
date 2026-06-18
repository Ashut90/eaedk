"""Structural Router (Stage 2B) — deterministic, stateless intent classifier.

ISOLATED shadow module. Does NOT modify navigator.py, classify(), or any existing
execution path.  Extracts an IntentVector purely from text structure and maps it to
a route category, proving that the same text ALWAYS produces the same route.

Design principles
-----------------
1. **No conversation state** — same text → same IntentVector, always.
2. **No DB access** — pure text analysis; entity recognition is registry-based.
3. **Structural objective detection** — syntactic patterns (fault signals, comparison
   markers, direction phrases, definition frames) in strict priority order.
4. **Entity cross-reference** — extracted entities are classified as specific concepts
   (→ TEACH), broad learning areas (→ LEARNING_MAP), or decision-topic keywords
   (→ DECISION_MAP) to resolve ambiguity.

Usage::

    from eaedk.structural_router import shadow_route, extract_intent

    route = shadow_route("my UART is not working")   # → "PROOF_PATH"
    vec   = extract_intent("what is SPI?")            # → IntentVector(entities=('SPI',), objective='define')

The ``workspace_telemetry`` parameter on ``shadow_route`` is reserved for future
enrichment (board context, active project) — the core routing works without it.
"""
from __future__ import annotations

from dataclasses import dataclass


# ── Route constants (mirror navigator.py for direct comparison) ────────────────────────

PROOF_PATH    = "PROOF_PATH"
DECISION_MAP  = "DECISION_MAP"
LEARNING_MAP  = "LEARNING_MAP"
CLARIFY       = "CLARIFY"
DECLINE       = "DECLINE"
TEACH         = "TEACH"


# ── IntentVector ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IntentVector:
    """Purely structural extraction of user intent — no conversation state, no DB.

    ``entities``  – named engineering entities found in the text (protocols, concepts,
                    broad learning areas, boards/chips).
    ``objective`` – the syntactic intent frame: ``fault | compare | direction |
                    define | vague``.
    """
    entities:  tuple[str, ...]
    objective: str                              # fault | compare | direction | define | vague

    def __repr__(self) -> str:
        return f"IntentVector(entities={self.entities!r}, objective={self.objective!r})"


# ── Objective detection (syntactic patterns, strict priority order) ────────────────────
#
# Priority matters: a message can contain signals from several categories.  The first
# match wins because it represents the MOST ACTIONABLE intent:
#   fault > compare > define > direction > vague

_FAULT_SIGNALS: tuple[str, ...] = (
    # status negatives
    "not working", "isn't working", "isnt working",
    "doesn't work", "doesnt work", "not functioning",
    # absence
    "no output", "no signal", "no data", "no print", "not printing",
    "nothing happens", "nothing on", "shows nothing",
    "silent", "silence", "dead",
    # failure / error
    "fail", "failed", "failing", "error", "broken",
    "crash", "crashes", "crashed",
    "hang", "hangs", "hanging", "freeze", "freezes", "frozen",
    "reboot", "reboots", "rebooting",
    # garbled output
    "garbage", "wrong output", "corrupt", "noise", "garbled",
    # inability
    "can't get", "cant get", "trouble with", "problem with",
    # signal-level
    "not toggling", "isn't toggling", "no waveform",
    "stays low", "stays high", "doesn't toggle",
    # value symptoms
    "reads all zeros", "reads zero", "all zeros", "all 0xff",
    "all ff",
)

_COMPARE_SIGNALS: tuple[str, ...] = (
    " or ", " vs ", " versus ",
    " compare ", "difference between",
    "which should i", "which board", "which mcu", "which chip",
    "which protocol", "which one",
    "trade-off", "trade off", "pros and cons",
    "should i use", "should i choose",
)

_DIRECTION_SIGNALS: tuple[str, ...] = (
    "where do i start", "where to start", "where should i start",
    "where do i begin", "where to begin", "where do i even",
    "where do i even begin",
    "how do i start", "how should i start", "how to start", "how to begin",
    "what should i build", "what should i learn",
    "what to build", "what to learn",
    "first project", "getting started", "get started",
    "roadmap", "career",
    "become a", "become an", "get into", "break into",
    "overwhelmed", "feel huge", "feels huge", "feels overwhelming",
    "not sure where", "don't know where", "dont know where",
    "help me focus", "direction",
    "how do i learn", "how should i learn",
    "what path", "which path",
    "confused between", "not sure which",
    "jumping", "finishing nothing",
)

_DEFINE_SIGNALS: tuple[str, ...] = (
    "what is ", "what are ", "what's ", "what does ",
    "explain ", "tell me about", "describe ",
    "how does ", "how does it work", "how does this work",
    "how does that work", "how do you",
    "concept of", "meaning of", "tell me what",
    "want to understand", "want to learn about", "want to learn ",
)


# ── Entity registries ─────────────────────────────────────────────────────────────────
# Each registry maps a canonical entity name to trigger phrases.  Entities are classified
# as PROTOCOL (specific), CONCEPT (specific), BROAD_AREA (learning-direction), or BOARD.

_PROTOCOLS: dict[str, tuple[str, ...]] = {
    "UART":     ("uart", "usart", "serial port"),
    "SPI":      ("spi",),
    "I2C":      ("i2c", "iic", "i²c"),
    "CAN":      ("can bus",),
    "BLE":      ("ble", "bluetooth low energy", "bluetooth"),
    "Wi-Fi":    ("wifi", "wi-fi", "wi fi"),
    "LoRa":     ("lora",),
    "Zigbee":   ("zigbee",),
    "MQTT":     ("mqtt",),
    "Ethernet": ("ethernet",),
}

_CONCEPTS: dict[str, tuple[str, ...]] = {
    "GPIO":          ("gpio",),
    "DMA":           ("dma", "direct memory access"),
    "timer":         (" timer ", "pwm"),
    "watchdog":      ("watchdog",),
    "RTOS":          (" rtos ", "freertos", "zephyr", "real-time os"),
    "linker_script": ("linker script", "memory.ld", ".ld file", "scatter file"),
    "memory_layout": ("memory layout", "stack overflow", ".bss"),
    "clock_tree":    ("clock tree", "peripheral clock", "clock enable"),
}

_BROAD_AREAS: dict[str, tuple[str, ...]] = {
    "communication_systems": (
        "communication", "communications", " protocol", " protocols",
        "wired", "wireless", "networking",
        "sensor data", "gateway", " telemetry",
    ),
    "embedded_linux": (
        "kernel", "driver development", "device driver",
        "embedded linux", "kernel module", "device tree",
        " linux ",
    ),
    "build_systems": (
        "yocto", "buildroot", "bitbake", "build system",
        "custom linux image", "rootfs", "meta-layer",
    ),
    "wireless_rf_sdr": (
        " sdr ", "software defined radio", "gnu radio",
        "rtl-sdr", "hackrf", " rf ", "radio frequency",
    ),
    "debug_skill": (
        "debugging", "debug skill", "debug patterns",
        "debug skill", "getting oriented",
    ),
}

_BOARDS: dict[str, tuple[str, ...]] = {
    "RP2040":       ("rp2040", "raspberry pi pico", " pico "),
    "STM32":        ("stm32",),
    "ESP32":        ("esp32",),
    "nRF52840":     ("nrf52840", "nrf52", "nordic"),
    "Raspberry Pi":  ("raspberry pi", " rpi"),
    "BeagleBone":   ("beaglebone", " bbb"),
    "Arduino":      ("arduino",),
    "Jetson":       ("jetson",),
}

# All entity registries grouped for extraction
_ALL_REGISTRIES: list[tuple[dict[str, tuple[str, ...]], str]] = [
    (_PROTOCOLS,    "protocol"),
    (_CONCEPTS,     "concept"),
    (_BROAD_AREAS,  "area"),
    (_BOARDS,       "board"),
]

# Known engineering-decision topic keywords (mirror reasoning.TOPICS).
# Used to disambiguate COMPARE: if a topic keyword fires → DECISION_MAP,
# otherwise the comparison is within a learning area → LEARNING_MAP.
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hal_vs_baremetal":      ("hal", "bare metal", "bare-metal", "cmsis"),
    "rtos_vs_superloop":     ("rtos", "freertos", "zephyr", "superloop"),
    "interrupt_vs_polling":  ("interrupt", "polling", "volatile", "atomic"),
    "polling_vs_dma":        ("dma",),
    "board_selection":       ("which board", "which mcu", "which chip"),
    "failsafe_bootloader":   ("bootloader", "fail-safe", "ota", "firmware update"),
    "watchdog_strategy":     ("watchdog",),
    "debugging_method":      ("nothing happens", "doesn't work", "board is dead"),
    "clock_tree":            ("clock tree", "peripheral clock", "rcc"),
    "linker_script":         ("linker script", "memory.ld", "ld script"),
    "memory_layout":         ("memory layout", "stack overflow", "heap"),
}


# ── Extraction functions ──────────────────────────────────────────────────────────────

def _extract_entities(text: str) -> tuple[str, ...]:
    """Extract named entities from text.  Returns unique canonical names in detection order."""
    low = " " + (text or "").lower().strip() + " "
    found: list[str] = []
    seen:  set[str]  = set()
    for registry, _kind in _ALL_REGISTRIES:
        for name, triggers in registry.items():
            if name not in seen and any(t in low for t in triggers):
                found.append(name)
                seen.add(name)
    return tuple(found)


def _is_broad_area(entity: str) -> bool:
    """Is this entity a broad learning area (vs a specific concept / protocol)?"""
    return entity in _BROAD_AREAS


def _matches_topic(text: str) -> str | None:
    """Check if the text matches a known engineering-decision topic keyword set."""
    low = " " + (text or "").lower().strip() + " "
    for topic_key, triggers in _TOPIC_KEYWORDS.items():
        if any(t in low for t in triggers):
            return topic_key
    return None


def _detect_objective(text: str) -> str:
    """Extract the structural objective from text.

    Priority: fault > compare > define > direction > vague.
    Same text always produces the same result — no conversation state involved.
    """
    low = " " + (text or "").lower().strip() + " "

    # FAULT — highest priority (a broken system is the most actionable signal)
    if any(s in low for s in _FAULT_SIGNALS):
        return "fault"

    # COMPARE / DECIDE — the user is weighing options
    if any(s in low for s in _COMPARE_SIGNALS):
        return "compare"

    # DEFINE / CONCEPT — the user wants an explanation
    if any(s in low for s in _DEFINE_SIGNALS):
        return "define"

    # DIRECTION / LEARN — the user wants orientation or a learning path
    if any(s in low for s in _DIRECTION_SIGNALS):
        return "direction"

    return "vague"


# ── Public API ────────────────────────────────────────────────────────────────────────

def extract_intent(user_text: str) -> IntentVector:
    """Pure, deterministic intent extraction from text structure alone.

    Same text → same IntentVector, always.  No conversation state, no DB, no LLM.
    """
    entities  = _extract_entities(user_text or "")
    objective = _detect_objective(user_text or "")
    return IntentVector(entities=entities, objective=objective)


def shadow_route(user_text: str, workspace_telemetry: dict | None = None) -> str:
    """Deterministic routing based purely on text structure.

    Returns the same route constants as ``navigator.classify()``:
    ``PROOF_PATH``, ``DECISION_MAP``, ``LEARNING_MAP``, ``TEACH``, ``CLARIFY``,
    ``DECLINE``.

    ``workspace_telemetry`` is reserved for future enrichment (board context,
    active project) but the core routing works without it.
    """
    intent = extract_intent(user_text)

    # ── FAULT → PROOF_PATH ────────────────────────────────────────────────────────
    if intent.objective == "fault":
        return PROOF_PATH

    # ── COMPARE / DECIDE ──────────────────────────────────────────────────────────
    if intent.objective == "compare":
        topic = _matches_topic(user_text)
        if topic:
            return DECISION_MAP
        # Protocol or broad-area comparison → within a learning area
        if any(_is_broad_area(e) or e in _PROTOCOLS for e in intent.entities):
            return LEARNING_MAP
        # Default for compare with no known topic or protocol
        return DECISION_MAP

    # ── DEFINE / CONCEPT ──────────────────────────────────────────────────────────
    if intent.objective == "define":
        # Broad area → learning-direction map (richer than a flat TEACH)
        if any(_is_broad_area(e) for e in intent.entities):
            return LEARNING_MAP
        # Specific entity → concept explanation
        if intent.entities:
            return TEACH
        # Define with no recognised entity → ask what
        return CLARIFY

    # ── DIRECTION / LEARN ─────────────────────────────────────────────────────────
    if intent.objective == "direction":
        if intent.entities:
            return LEARNING_MAP
        # Bare "where do I start?" with no area context → ask
        return CLARIFY

    # ── VAGUE ─────────────────────────────────────────────────────────────────────
    return CLARIFY


# ──────────────────────────────────────────────────────────────────────────────────────
#  Target cases and phrasing-split proof
# ──────────────────────────────────────────────────────────────────────────────────────

# (id, text, expected_route)
_TARGET_CASES: list[tuple[str, str, str]] = [
    # Category 1 — Concept definition (→ TEACH)
    ("1.1", "what is SPI?",                                  TEACH),
    ("1.2", "explain I2C to me",                             TEACH),

    # Category 2 — Fault / debug (→ PROOF_PATH)
    ("2.1", "my UART is not working",                        PROOF_PATH),
    ("2.2", "SPI reads all zeros",                           PROOF_PATH),

    # Category 3 — Direction / learning (→ LEARNING_MAP)
    ("3.1", "communication protocols — where do I start?",  LEARNING_MAP),
    ("3.2", "I want to compare BLE vs LoRa",                 LEARNING_MAP),
    ("3.3", "how do I get into driver development?",         LEARNING_MAP),

    # Category 4 — Decision / trade-off (→ DECISION_MAP)
    ("4.1", "RTOS or Linux?",                                DECISION_MAP),
    ("4.2", "should I use HAL or bare metal?",               DECISION_MAP),
]

# Different phrasings of the SAME intent must produce the SAME route.
# Each group must unanimously agree on the expected route.
_PHRASING_SPLITS: list[tuple[str, str, str]] = [
    # ── Fault phrasings → all PROOF_PATH ────────────────────────────────────────
    ("UART not working",            PROOF_PATH),
    ("UART is broken",              PROOF_PATH),
    ("UART is dead",                PROOF_PATH),
    ("no output on UART",           PROOF_PATH),
    ("UART gives garbage output",   PROOF_PATH),

    # ── Concept phrasings → all TEACH ───────────────────────────────────────────
    ("what is SPI?",                TEACH),
    ("explain SPI",                 TEACH),
    ("tell me about SPI",           TEACH),
    ("what does SPI do?",           TEACH),
    ("how does SPI work?",          TEACH),

    # ── Direction phrasings → all LEARNING_MAP ──────────────────────────────────
    ("communication protocols — where do I start?",  LEARNING_MAP),
    ("where do I start with communication?",          LEARNING_MAP),
    ("communication direction",                       LEARNING_MAP),
    ("how do I start learning about protocols?",      LEARNING_MAP),

    # ── Decision phrasings → all DECISION_MAP ───────────────────────────────────
    ("RTOS or Linux?",                          DECISION_MAP),
    ("RTOS vs Linux",                           DECISION_MAP),
    ("should I use RTOS or Linux?",             DECISION_MAP),
    ("compare RTOS and Linux",                  DECISION_MAP),
]


# ──────────────────────────────────────────────────────────────────────────────────────
#  Self-test execution block
# ──────────────────────────────────────────────────────────────────────────────────────

def _run_tests() -> bool:
    """Run target cases + phrasing-split proof; print results; return True if all pass."""
    ok = True
    width_id   = 6
    width_text = 52
    width_obj  = 10
    width_ent  = 30
    width_exp  = 14
    width_got  = 14
    hdr = (f"{'ID':<{width_id}} {'Text':<{width_text}} {'Obj':<{width_obj}} "
           f"{'Entities':<{width_ent}} {'Expected':<{width_exp}} {'Got':<{width_got}} {'OK'}")
    sep = "─" * len(hdr)

    def _row(cid: str, text: str, expected: str) -> str:
        nonlocal ok
        vec = extract_intent(text)
        got = shadow_route(text)
        passed = got == expected
        if not passed:
            ok = False
        ent_short = ", ".join(vec.entities) if vec.entities else "—"
        if len(ent_short) > width_ent - 2:
            ent_short = ent_short[:width_ent - 4] + "…"
        text_short = text if len(text) <= width_text else text[:width_text - 2] + "…"
        mark = "✓" if passed else "✗"
        return (f"{cid:<{width_id}} {text_short:<{width_text}} {vec.objective:<{width_obj}} "
                f"{ent_short:<{width_ent}} {expected:<{width_exp}} {got:<{width_got}} {mark}")

    print(f"\n{'Structural Router — Target Cases':=^{len(hdr)}}")
    print(hdr)
    print(sep)
    for cid, text, expected in _TARGET_CASES:
        print(_row(cid, text, expected))

    print(f"\n{'Phrasing Split Proof':=^{len(hdr)}}")
    print(hdr)
    print(sep)
    split_groups: dict[str, list[tuple[str, str]]] = {}
    for text, expected in _PHRASING_SPLITS:
        split_groups.setdefault(expected, []).append((text, expected))
    for route, cases in split_groups.items():
        for i, (text, exp) in enumerate(cases):
            cid = f"S{i+1}.{route[:4]}"
            print(_row(cid, text, exp))
        print()  # blank line between groups

    total = len(_TARGET_CASES) + len(_PHRASING_SPLITS)
    passed = total - (1 if not ok else 0)
    # recount properly
    fail_count = 0
    for cid, text, expected in _TARGET_CASES:
        if shadow_route(text) != expected:
            fail_count += 1
    for text, expected in _PHRASING_SPLITS:
        if shadow_route(text) != expected:
            fail_count += 1
    total = len(_TARGET_CASES) + len(_PHRASING_SPLITS)
    print(f"Results: {total - fail_count}/{total} passed", end="")
    if fail_count:
        print(f"  ({fail_count} FAILED)")
    else:
        print("  — all deterministic, no phrasing splits.")
    return fail_count == 0


if __name__ == "__main__":
    _run_tests()