"""The Navigator — EAEDK's central Embedded-Engineering pattern router (docs/32).

A confused learner's messy question is first CLASSIFIED into a confusion TYPE, then routed to the
right kind of guidance. This is the one brain; ``mentor_chat`` is just a dispatcher. Adding future
topics means adding Pattern/Map DATA, never rewriting the router.

    user message
      → Purpose gate (coarse)                 already computed by the caller
      → classify() → Route(mode, payload)     <-- here
          PROOF_PATH    broken-system / bring-up / debug   (ProblemPattern engine)
          DECISION_MAP  engineering choice / trade-off      (reasoning.Topic = decision data)
          LEARNING_MAP  broad learning-direction confusion  (LearningMap, below)
          CLARIFY       intent too vague                    (one real question)
          DECLINE       cannot be grounded / verified       (honest limitation)
          TEACH         a grounded concept/skill to explain (the existing teaching pipeline)

The kernel owns classification, pattern/map selection, evidence state, proof steps and verification.
The LLM only voices; it never picks the route or invents board facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import problem_patterns, reasoning, repo, mentor

PROOF_PATH = "PROOF_PATH"
DECISION_MAP = "DECISION_MAP"
LEARNING_MAP = "LEARNING_MAP"
CLARIFY = "CLARIFY"
DECLINE = "DECLINE"
TEACH = "TEACH"


# --- LearningMap: the missing first-class object for learning-direction confusion ----------------

@dataclass(frozen=True)
class SubRoute:
    """A disambiguation/comparison branch inside a problem class (e.g. 'wired bus' vs 'wireless')."""
    name: str
    signals: tuple[str, ...]                     # phrasings that route a query toward this sub-route
    summary: str                                 # one line: what it is / when to use it


@dataclass(frozen=True)
class LearningMap:
    """A registered embedded-engineering PROBLEM CLASS — a broad area a learner is confused about how
    to APPROACH (not a bug, not a single decision). The classification MECHANISM is this registry:
    adding a class is a data entry conforming to this schema, never a change to the router.

    Two shapes are supported. A simple direction map fills ``possible_meanings``/``recommended_route``.
    A richer class fills ``sub_routes`` (for SORT/COMPARE across candidate interpretations) plus an
    ``overview`` and a ``scope_excludes`` boundary. Either way matching is the same AND-of-ORs."""
    name: str
    title: str
    match_groups: tuple[tuple[str, ...], ...]   # entry signals: matches when EACH group has a hit
    possible_meanings: tuple[str, ...] = ()     # simple shape: what people mean by this
    prerequisites: tuple[str, ...] = ()
    recommended_route: tuple[str, ...] = ()     # simple shape: an ordered route
    wrong_starts: tuple[str, ...] = ()          # where beginners waste months
    first_step: str = ""                        # the first move that proves direction
    clarifying_question: str = ""               # the one question that sharpens the route
    needs_linux: bool = False                   # area requires a Linux-class (Cortex-A) board
    # Richer classification shape (SORT/COMPARE within the class):
    overview: str = ""                          # the framing sentence
    sub_routes: tuple[SubRoute, ...] = ()       # candidate interpretations to sort/compare
    scope_excludes: str = ""                    # what this class does NOT cover + where to route
    board_dependent: bool = True                # does resolving need board-specific facts?
    followups: tuple[str, ...] = ()             # conversational next-moves offered after a teach


@dataclass
class Route:
    mode: str
    purpose: object = None                      # the PurposeDecision (CLARIFY / DECLINE / foundation)
    proof_state: object = None                  # ProblemPattern ProofPathState (PROOF_PATH)
    decision_topic: object = None               # reasoning.Topic (DECISION_MAP), may be None
    learning_map: LearningMap | None = None     # (LEARNING_MAP), None => foundation/career
    reason: str = ""


def _last_user(messages: list[dict]) -> str:
    return next((m.get("content", "") for m in reversed(messages or [])
                 if m.get("role") == "user"), "")


def match_learning(message: str) -> LearningMap | None:
    """Which broad learning area is this (if any)? The same AND-of-ORs match the patterns use."""
    low = " " + (message or "").lower() + " "
    for lm in LEARNING_MAPS.values():
        if all(any(p in low for p in group) for group in lm.match_groups):
            return lm
    return None


def classify(purpose, messages: list[dict]) -> Route:
    """The central router. ``purpose`` is the coarse Purpose-gate decision the caller computed.
    Precedence: a live proof path wins (it's conversation-aware); then a seeded decision; then a
    learning area; then the Purpose gate's foundation/decline/clarify; else teach."""
    last = _last_user(messages)

    ps = problem_patterns.resolve(messages)                  # conversation-aware, replays transcript
    if ps.matched:
        return Route(PROOF_PATH, proof_state=ps, reason="problem pattern matched")

    topic = reasoning.detect_topic(last)                     # the curated decision/trade-off library
    if topic is not None:
        return Route(DECISION_MAP, purpose=purpose, decision_topic=topic, reason=f"topic:{topic.key}")

    lm = match_learning(last)                                # a broad learning-direction area
    if lm is not None:
        return Route(LEARNING_MAP, purpose=purpose, learning_map=lm, reason=f"learning:{lm.name}")

    p = getattr(purpose, "purpose", "ANSWER_NOW")
    if p == "REDIRECT_TO_FOUNDATION":                        # field-entry / career → a learning route
        return Route(LEARNING_MAP, purpose=purpose, reason="foundation")
    if p == "DECLINE_OUT_OF_SCOPE":
        return Route(DECLINE, purpose=purpose, reason="out of scope")
    if p == "ASK_CLARIFICATION":
        return Route(CLARIFY, purpose=purpose, reason="too vague")
    return Route(TEACH, purpose=purpose, reason="grounded teaching")


# --- Deterministic LEARNING_MAP rendering (direction, not an info dump) --------------------------

def _bullets(items, mark="  - "):
    return "\n".join(f"{mark}{x}" for x in items)


def _linux_target_note(target: dict) -> str:
    label = target.get("label") or "that board"
    if target.get("board_class") == "mcu_no_linux":
        return (f"SORT — {label} is microcontroller-class (no MMU), so it is not a Linux-capable "
                "Yocto target. Yocto is for Linux images/rootfs on Linux-capable boards; it is not "
                f"the correct route for {label} kernel/Linux-image work.")
    if target.get("board_class") == "linux_capable":
        return (f"SORT — {label} is Linux-capable, so Yocto/kernel-image work can be the right class "
                "of path if your goal is a Linux image, rootfs, device tree, kernel config or BSP.")
    return ""


# Target-aware MCU firmware wording — never "Pico SDK" for a non-RP2040 chip.
_MCU_SDK = {
    "RP2040":    "Pico SDK, bare metal, or FreeRTOS/RTOS",
    "STM32F103": "STM32Cube/HAL, bare-metal register work, or FreeRTOS",
    "nRF52840":  "nRF Connect SDK/Zephyr, Nordic SDK, bare-metal/vendor SDK, or RTOS",
    "ESP32":     "ESP-IDF, FreeRTOS, or bare-metal/vendor SDK",
}
_MCU_FIRST_PROJECT = {
    "RP2040":    "Pico SDK GPIO/UART project",
    "STM32F103": "STM32Cube/HAL or bare-metal register GPIO/UART project",
    "nRF52840":  "nRF Connect SDK / Zephyr GPIO/UART project",
    "ESP32":     "ESP-IDF GPIO/UART project",
}


def _mcu_sdk(label: str) -> str:
    return _MCU_SDK.get(label, "your vendor SDK/HAL, bare metal, or an RTOS")


def _mcu_vs_linux_routes(label: str) -> str:
    first = _MCU_FIRST_PROJECT.get(label, "GPIO/UART firmware project")
    firmware_steps = (
        f"  - {first}",
        "  - USB CDC/HID project" if label == "RP2040" else "  - timer/interrupt project",
        "  - FreeRTOS task project",
        "  - sensor + logging project",
        "  - bootloader / firmware update project",
    )
    return "\n".join([
        "ORGANIZE — two routes:",
        f"A. If staying with {label} (MCU firmware):",
        *firmware_steps,
        "B. If learning Yocto/kernel development (needs a Linux-capable target):",
        "  - start with QEMU or a Linux-capable SBC",
        "  - build a reference image for that Linux-capable target",
        "  - boot it",
        "  - add one package",
        "  - write one recipe",
        "  - later: kernel config, kernel patch, and device tree work",
        "",
        f"COMPARE — {label} firmware work is MCU firmware: {_mcu_sdk(label)}. "
        "Yocto/Linux kernel work is a Linux-board/BSP path: image, rootfs, bootloader handoff, "
        "kernel config, device tree, and drivers.",
        "",
        f"HELP — Choose {label} firmware path or Yocto/Linux-capable board path.",
    ])


# --- Broad-direction detection + honest scoped-uncertainty (Priority 2) --------------------------

_BROAD_DIRECTION = (
    "where do i start", "where to start", "where do i begin", "where do i even", "how do i start",
    "how should i start", "what should i build", "what should i learn", "which direction",
    "what direction", "roadmap", "career", "get into", "work on", "become strong", "become good",
    "serious project path", "what path", "which path", "feel huge", "feels huge", "overwhelmed",
    "not sure where", "don't know where", "dont know where", "deeply", "compare", "confused between",
    "where do i even begin", "what project should i", "help me focus", "how do i think",
)

# Recognisable large AREAS, for the honest-uncertainty slot only (not a routing table).
_AREAS = (
    ("audio/video / streaming", ("audio", "video", "stream", "casting", "codec", "h264", "rtsp")),
    ("security / secure boot",  ("secure boot", "cryptograph", "tls", "encryption", "attestation")),
    ("motor / drive control",   ("motor control", "bldc", "foc", "servo control", "stepper")),
    ("power / energy",          ("power sequencing", "power budget", "battery life", "low power")),
    ("machine learning on MCU", ("tinyml", "tflite", "neural network", "on-device inference")),
)


def is_broad_direction(text: str) -> bool:
    """A direction / career / comparison / overwhelm question (vs a concrete board task). Used to
    decide when a board-LESS question must NOT be answered as if it were about the selected board."""
    low = " " + (text or "").lower() + " "
    return any(p in low for p in _BROAD_DIRECTION)


def _detected_area(text: str) -> str | None:
    low = " " + (text or "").lower() + " "
    for label, signals in _AREAS:
        if any(s in low for s in signals):
            return label
    return None


def firmware_direction_map() -> str:
    """Board-INDEPENDENT firmware learning direction for a board-less career/foundation question —
    the same spine on any chip, so it never anchors to the selected board."""
    return "\n".join([
        "Becoming a firmware engineer is a direction, not one board — and it's the same spine on "
        "every chip, so let's map it without assuming any hardware.", "",
        "A board-independent firmware roadmap — a learning sequence (go in order, depth on a spine):",
        "  1. C fundamentals — pointers, memory, bit operations (the language of firmware).",
        "  2. MCU basics — what a register is, the memory map, the datasheet/reference-manual habit.",
        "  3. GPIO + UART — drive an output pin, print over serial (your eyes for everything after).",
        "  4. Timers + interrupts — react to events without busy-waiting.",
        "  5. A bus — UART/I2C/SPI to a real peripheral such as a sensor.",
        "  6. Debugging — bisecting a dead board, reading a fault, using a logic analyzer.",
        "  7. Then DEPTH by goal — an RTOS, a bootloader, or embedded Linux / driver work.", "",
        "Pick ONE board later only to practise the spine — the reasoning transfers to all of them.", "",
        "So I can point you precisely — Are you aiming for MCU firmware, embedded Linux, driver/BSP "
        "work, or IoT/product firmware?",
    ])


def direction_clarify() -> str:
    """A board-less bare 'where do I start?' must not assume a board — ask for the direction first."""
    return (
        "Before I point you anywhere I won't assume a board — that would send you down the wrong path "
        "for what you actually want.\n\n"
        "Tell me the direction and I'll give you a real route, not a generic tutorial.\n\n"
        "Question: Are you trying to learn MCU firmware, Linux/Yocto, communication systems, "
        "debugging, or a specific board project?")


def scoped_uncertainty(text: str) -> str:
    """An honest, board-less response when no registered class matches — never a board roadmap and
    never a board-specific Try-this/clock callback (Priority 2)."""
    area = _detected_area(text) or "this area"
    return (
        f"I don't have a structured direction map for {area} yet — and I'd rather tell you that than "
        "hand you a generic board tutorial that ignores your actual question.\n\n"
        "What I can honestly do: if you name the closest board or project you have in hand, I'll "
        "ground the next step in something I can verify; or name the specific sub-area you care about "
        "and I'll sort it into concrete routes and trade-offs.\n\n"
        "Question: what's the closest board or project you're working with, or the one sub-area you "
        "want to pin down first?")


# --- Classified render (SORT → COMPARE → HELP across a class's sub-routes) ------------------------

def teach_packet(lm: LearningMap) -> str:
    """The VERIFIED grounding a conversational mentor may teach from for this class — curated facts
    only, no board specifics. This is the 'chapter' the LLM teaches; the verifier blocks anything
    the LLM states that is not traceable here."""
    L = [f"VERIFIED FACT PACKET — topic: {lm.title}.", f"Framing: {lm.overview}"]
    if lm.sub_routes:
        L.append("Routes / options (teach and compare from these; do not add others):")
        L += [f"  - {sr.name}: {sr.summary}" for sr in lm.sub_routes]
    if lm.scope_excludes:
        L.append(f"Out of scope here: {lm.scope_excludes}")
    if lm.first_step:
        L.append(f"A sound first step: {lm.first_step}")
    return "\n".join(L)


def _render_classified(lm: LearningMap, query: str) -> str:
    low = " " + (query or "").lower() + " "
    hit = [sr for sr in lm.sub_routes if any(s in low for s in sr.signals)]
    L = [f"This is a {lm.title} direction — let's turn the confusion into routes. You're not lost; "
         "here's the map.", ""]
    if lm.overview:
        L.append(lm.overview); L.append("")

    if len(hit) >= 2:
        L.append("SORT — you named a few things; here is how they group and when each wins:")
        L += [f"  • {sr.name}: {sr.summary}" for sr in hit]
        L.append("")
        L.append("COMPARE — pick by the constraint that dominates YOUR project (range, power, data "
                 "rate, wiring, determinism), not by which is most popular.")
    elif len(hit) == 1:
        sr = hit[0]
        L.append(f"SORT — this is the {sr.name} route: {sr.summary}")
        others = [s for s in lm.sub_routes if s is not sr]
        if others:
            L.append("  (the neighbouring routes, so you know the boundary:)")
            L += [f"  • {s.name}: {s.summary}" for s in others]
    else:
        L.append("SORT — this splits into a few routes; which one is yours?")
        L += [f"  • {sr.name}: {sr.summary}" for sr in lm.sub_routes]

    if lm.scope_excludes:
        L.append(""); L.append(f"Scope — {lm.scope_excludes}")
    if lm.first_step:
        L.append(""); L.append(f"HELP — first step that proves direction: {lm.first_step}")
    if lm.clarifying_question:
        L.append(""); L.append(f"Question: {lm.clarifying_question}")
    return "\n".join(L)


# For a board-LESS Linux/Yocto/kernel direction question: separate the routes WITHOUT naming the
# selected board, and state the Linux-capable requirement generically (a Cortex-A SBC or QEMU).
_LINUX_BOARDLESS_NOTE = (
    'SORT — "Linux / kernel / drivers / Yocto" is several different routes, and mixing them is the '
    "usual confusion. They all need a Linux-capable target (a Cortex-A SBC, or QEMU to start) — a "
    "microcontroller has no MMU and cannot run Linux. The routes:\n"
    "  • app / socket networking — TCP/UDP sockets on TOP of an existing Linux; no kernel code.\n"
    "  • kernel driver / BSP / device tree — kernel-space drivers, device tree, board bring-up.\n"
    "  • Yocto / image integration — building or customising the Linux IMAGE / rootfs / BSP."
)
_LINUX_BOARDLESS_CLARIFY = ("Are you targeting app/socket networking, kernel driver/BSP work, or "
                            "Yocto image integration?")


def render_learning_map(conn, lm: LearningMap, board_name: str | None = None,
                        explicit_target: dict | None = None, query: str = "",
                        board_anchored: bool = False) -> str:
    if lm.sub_routes:                                    # richer class → SORT/COMPARE across sub-routes
        return _render_classified(lm, query)
    L = [f"This is a learning-direction question, not a single bug — so let's get you oriented on "
         f"{lm.title}. You're not lost; this is a known area, and here is the map.", ""]

    et = explicit_target
    if et and et.get("selected_differs") and board_name:
        L.append(f"You selected {board_name}, but your question says {et['label']}, "
                 f"so I'm answering for {et['label']}.")
        L.append("")

    # A target was named but we cannot classify it — never silently hijack to the selected board.
    if et and et.get("board_class") == "unknown":
        L.append(f"You mentioned {et['label']}; I will not assume the selected board is the target. "
                 "Is this a Linux-capable SoC/SBC or an MCU-class chip? Tell me and I'll route you "
                 "to the right firmware or Linux/Yocto path.")
        return "\n".join(L)

    if lm.needs_linux and et:
        note = _linux_target_note(et)
        if note:
            L.append(note)
            L.append("")
        if et.get("board_class") == "mcu_no_linux":
            # The two-route block IS the full answer. Do NOT append the generic Yocto/LearningMap tail
            # — for an MCU target it would say "build your board's reference image", which is false.
            label = et.get("label") or "that board"
            L.append(_mcu_vs_linux_routes(label))
            L.append("")
            L.append(f"So I can point you precisely — do you want {label} firmware projects, "
                     "Yocto learning, a Linux-capable board recommendation, or a custom Linux "
                     "BSP/kernel path?")
            return "\n".join(L)
        # linux_capable → fall through to the generic body (the Yocto/kernel route applies there)
    elif lm.needs_linux and board_anchored and board_name:
        # The selected board is genuinely in scope (active project, or the user said "this board").
        _board, soc = repo.load_board(conn, board_name)
        if soc is not None and not mentor.supports_linux(soc):
            L.append(f"If you mean Linux kernel/driver work: {board_name} is a microcontroller (no MMU), "
                     "so it cannot run Linux and you would need a Linux-class board (a Cortex-A SBC) "
                     "for that path.")
            L.append(f"If you mean MCU peripheral drivers or RTOS kernel internals: {board_name} "
                     "works fine for those paths — no Linux required.")
            L.append("")
    elif lm.needs_linux:
        # Board-LESS broad question — never anchor to the selected board. Separate the Linux routes
        # generically and state the Linux-capable requirement without naming any board.
        L.append(_LINUX_BOARDLESS_NOTE)
        L.append("")

    L.append("What people actually mean by this:")
    L.append(_bullets(lm.possible_meanings))
    L.append("")
    L.append("What you'll want under your belt first:")
    L.append(_bullets(lm.prerequisites))
    L.append("")
    L.append("A route that works, in order:")
    L.append(_bullets([f"{i}. {s}" for i, s in enumerate(lm.recommended_route, 1)], mark="  "))
    L.append("")
    L.append("Where beginners waste months — don't start here:")
    L.append(_bullets(lm.wrong_starts))
    L.append("")
    L.append(f"First step to prove you're moving the right way: {lm.first_step}")
    L.append("")
    if et and et.get("board_class") == "linux_capable":      # MCU targets already returned above
        label = et.get("label") or "that board"
        L.append(f"So I can point you precisely — do you want Yocto learning, a {label} "
                 "BSP/kernel path, a Linux-capable board comparison, or a smaller firmware/RTOS path?")
    elif lm.needs_linux and not board_anchored:              # board-less Linux/Yocto → route fork
        L.append(f"So I can point you precisely — {_LINUX_BOARDLESS_CLARIFY}")
    else:
        L.append(f"So I can point you precisely — {lm.clarifying_question}")
    return "\n".join(L)


# ================================================================================================
# SEED LEARNING MAPS — curated, the first two areas. Add more by registering another LearningMap.
# ================================================================================================

_EMBEDDED_LINUX = LearningMap(
    name="embedded_linux",
    title="kernel / driver / embedded development",
    match_groups=(
        ("kernel", "driver development", "device driver", "char device", "device tree", "devicetree",
         "kernel module", "linux driver", "embedded linux", "write a driver", "writing drivers",
         "probe", "platform driver"),
    ),
    possible_meanings=(
        "writing MCU peripheral drivers (GPIO, UART, SPI, I2C) in bare metal or a vendor HAL",
        "writing a Linux kernel DRIVER for a device (the most common Linux goal)",
        "understanding RTOS kernel internals (FreeRTOS, Zephyr — tasks, scheduling, IPC)",
        "learning OS/kernel internals generally, or writing a toy OS / teaching kernel",
        "embedded Linux board support / kernel configuration / device tree",
    ),
    prerequisites=(
        "solid C and pointers",
        "a clear sense of which context you are in — MCU bare-metal, RTOS, or Linux",
        "comfort with a cross-compiler and reading a datasheet or reference manual",
    ),
    recommended_route=(
        "First, confirm your context: MCU peripheral driver, RTOS kernel internals, or Linux kernel/driver.",
        "For MCU peripheral drivers: read the peripheral chapter in your MCU's reference manual, enable "
        "the peripheral clock, configure the pin's alternate function, and write a register-level init.",
        "For RTOS kernel internals: pick FreeRTOS or Zephyr, build the hello-world task example, then "
        "study the scheduler source for the task-create and context-switch path.",
        "For Linux kernel/drivers: boot a stock kernel on a Linux-class board, write a trivial char-device "
        "module (insmod/rmmod, printk), then bind a platform driver to a device-tree node.",
    ),
    wrong_starts=(
        "reading the whole kernel source front-to-back in any context — kernel, RTOS, or MCU",
        "starting with a complex subsystem (networking, USB) before a char device in Linux",
        "writing an MCU peripheral driver while thinking you are writing a Linux kernel driver — "
        "the register names and init sequence are completely different",
        "trying Linux kernel/driver work on a microcontroller — there is no Linux there",
    ),
    first_step="Before any code, settle which context you are in: MCU peripheral driver, RTOS kernel "
               "internals, or Linux kernel/driver. Each has a different datasheet, toolchain, and first "
               "step. Tell me which fits, and I will give you the exact first move.",
    clarifying_question="Are you aiming for embedded Linux drivers, MCU peripheral drivers, RTOS kernel "
                        "internals, or toy OS/kernel learning? These are different routes.",
    needs_linux=True,
)

_BUILD_SYSTEMS = LearningMap(
    name="build_systems",
    title="embedded-Linux build systems (Yocto / Buildroot)",
    match_groups=(
        ("yocto", "buildroot", "bitbake", "openembedded", "build system", "custom linux image",
         "root filesystem", "rootfs", "meta-layer", "meta layer"),
    ),
    possible_meanings=(
        "building a CUSTOM Linux image/rootfs for a board (the usual goal)",
        "adding your app/recipe to an existing image",
        "understanding layers/recipes vs just using a vendor image",
    ),
    prerequisites=(
        "comfort with Linux, make, and the shell",
        "what a rootfs, kernel, bootloader and device tree each are",
        "patience — first builds are large and slow",
    ),
    recommended_route=(
        "Build a vendor's reference image for your board unchanged — prove the flow end-to-end.",
        "Flash and boot it — confirm the image you built actually runs.",
        "Add ONE package to the image (a recipe or an image-install) — learn how content gets in.",
        "Write a tiny recipe for your own app — the first thing that's truly yours.",
    ),
    wrong_starts=(
        "writing custom meta-layers before booting a stock image",
        "Yocto when a simple Buildroot image would teach the concepts faster",
        "debugging recipe internals before you've booted anything",
    ),
    first_step="Pick your board's reference image, build it unchanged, and boot it. Don't customise "
               "anything yet — a clean build+boot proves the toolchain and your setup before you add "
               "complexity.",
    clarifying_question="which board are you targeting, and do you need a fully custom image or just to "
                        "add your app to an existing one?",
    needs_linux=True,
)

# --- Proof-of-concept classes for the general mechanism (NOT the product ceiling) ----------------

_DEBUG_SKILL_GENERAL = LearningMap(
    name="debug_skill_general",
    title="getting oriented in an overwhelming area (and building debug skill)",
    match_groups=(
        ("feel huge", "feels huge", "feels overwhelming", "overwhelmed", "where do i even begin",
         "where do i even start", "too many things", "everything at once", "so much to learn",
         "debugging", "debug skill", "debug patterns", "patterns should i learn",
         "how do i get better at debug", "i keep jumping", "finishing nothing"),
    ),
    overview="When a field feels huge, the fix is not 'learn everything' — it is to cut it into a few "
             "layers and prove one at a time. Breadth comes from depth on a spine, not from tutorials.",
    sub_routes=(
        SubRoute("break the domain into layers",
                 ("feel huge", "overwhelmed", "where do i", "too many", "begin", "lost", "focus"),
                 "split the area into 3-5 layers (physical → protocol → app → reliability) and pick ONE "
                 "to make real first; ignore the rest until that one works."),
        SubRoute("build debugging skill",
                 ("debug", "patterns", "flaky", "intermittent", "hang", "no output"),
                 "debugging is pattern recognition: learn to bisect the signal path, isolate one "
                 "variable, and force a reproducible failure before changing anything."),
        SubRoute("finish one project end-to-end",
                 ("jumping", "finishing nothing", "tutorial", "shiny", "scattered"),
                 "the cure for tutorial-hopping is one small project carried to 'it actually works on "
                 "hardware' — completion teaches more than ten half-starts."),
    ),
    scope_excludes="This is about HOW to approach an area, not a specific protocol or board. Once you "
                   "pick a concrete topic I can route you to that class.",
    first_step="Name the single smallest thing in this area you could make work end-to-end this week, "
               "and we'll turn that into the spine everything else hangs off.",
    clarifying_question="what's the ONE outcome you'd be proud to demo — concretely, not 'learn X'?",
    board_dependent=False,
)

_WIRELESS_RF_SDR = LearningMap(
    name="wireless_rf_sdr",
    title="RF / SDR for an embedded-software person",
    match_groups=(
        (" sdr ", "software defined radio", " rtl-sdr ", " hackrf ", "gnuradio", " rf ",
         "radio frequency", "rf communication", "rf signal", "baseband", "iq sampling", "demodulat"),
    ),
    overview="RF/SDR is a different stack from MCU firmware: it's signals and math (sampling, IQ, "
             "modulation, filtering) on top of radio hardware. As a software person you start in "
             "software-defined radio, not by bringing up a board.",
    sub_routes=(
        SubRoute("SDR in software first",
                 ("sdr", "software defined radio", "gnuradio", "rtl-sdr", "start", "without getting lost"),
                 "grab an RTL-SDR dongle + GNU Radio / Python; receive and decode a real signal (FM, "
                 "ADS-B) before touching transmit or custom hardware."),
        SubRoute("the DSP concepts to learn",
                 ("learn", "concept", "math", "modulation", "iq", "baseband", "filter", "deeply"),
                 "sampling & Nyquist, IQ representation, a couple of modulation schemes (FSK/QAM), and "
                 "basic filtering — enough to reason about a link, not a full DSP degree."),
        SubRoute("when an MCU is/ isn't enough",
                 ("mcu", "microcontroller", "stm32", "esp32", "realistic", "on a chip"),
                 "a bare MCU can drive a radio MODULE (LoRa/BLE transceiver) but is not an SDR; real "
                 "SDR wants a host CPU or an FPGA/SoC. Know that boundary before you buy hardware."),
    ),
    scope_excludes="Picking a wireless PROTOCOL for a product (BLE vs LoRa vs Wi-Fi) is the "
                   "communication-systems class, not this one — this is RF/SDR as a discipline.",
    first_step="Receive ONE real signal with an RTL-SDR in GNU Radio (FM broadcast or ADS-B) — that one "
               "demo proves the whole receive chain and orients everything else. No 'blink an LED'.",
    clarifying_question="do you want to RECEIVE/decode signals (start here), understand the DSP, or know "
                        "what's realistic on an MCU vs a host/FPGA?",
    board_dependent=False,
)

_COMMUNICATION_SYSTEMS = LearningMap(
    name="communication_systems",
    title="communication systems",
    match_groups=(
        # group 1 — the domain
        ("communication", "protocol", "wireless", "uart", " spi", " i2c", "i²c", "can bus", "canbus",
         "can-bus", "ethernet", " ble ", "wifi", "wi-fi", " lora", "zigbee", "mqtt", "fieldbus", "modbus",
         "cloud", " iot", "sensor data", "networking", "gateway", "telemetry"),
        # group 2 — a direction/comparison/breadth framing (so 'what is SPI?' stays a concept)
        ("which", " vs ", "versus", "confused", "compare", "direction", "should i", "where to start",
         "where do i start", "build first", "what should i build", "communication system", "between",
         "whether", "follow", "choose", "focus", "jumping", "not sure", "deeply", "deep", "reliabl",
         "best", "path", "project", "gateway", "systems", "or "),
    ),
    overview="Communication splits into three layers, and most confusion is mixing them: (1) wired "
             "local buses between chips, (2) wireless links between devices, (3) networking/transport "
             "to a host or cloud. Pick the layer your problem lives in first.",
    sub_routes=(
        SubRoute("wired local bus (UART / SPI / I2C / CAN)",
                 ("uart", "spi", "i2c", "can", "bus", "wired", "between chips", "onboard"),
                 "chip-to-chip on one board/harness. UART = simple point-to-point; SPI = fast, more "
                 "pins, one master; I2C = 2 wires, many devices, slower; CAN = robust multi-node for "
                 "vehicles/industrial. Choose by speed, wire count, distance, and noise."),
        SubRoute("wireless link (BLE / Wi-Fi / LoRa / Zigbee)",
                 ("wireless", "ble", "bluetooth", "wifi", "wi-fi", "lora", "zigbee", "rf"),
                 "device-to-device over the air. BLE = short range, low power, phone-friendly; Wi-Fi = "
                 "high data rate, high power, IP; LoRa = km range, tiny data, very low power; Zigbee = "
                 "mesh, low power. Choose by range × data-rate × power budget."),
        SubRoute("networking / transport (MQTT, sockets, to cloud)",
                 ("mqtt", "cloud", "tcp", "socket", "http", "ethernet", "internet", "broker", "server",
                  "reliabl", "telemetry"),
                 "moving data to a host/cloud reliably: needs an IP-capable link first (Wi-Fi/Ethernet), "
                 "then a transport (MQTT for pub/sub telemetry, TCP sockets for streams). Reliability = "
                 "acks, retries, QoS, buffering — design these, don't hope."),
    ),
    scope_excludes="RF/SDR as a discipline (signals, modulation, GNU Radio) is the wireless_rf_sdr "
                   "class. A specific broken bus ('my SPI reads 0xFF') is a debug proof-path, not this.",
    first_step="Decide which layer your project lives in (wired bus / wireless / networking), then "
               "prove the SMALLEST link in that layer end-to-end before adding reliability or features.",
    clarifying_question="is your problem chip-to-chip on one board, device-to-device over the air, or "
                        "getting data to a host/cloud? That picks the layer.",
    board_dependent=False,
    followups=(
        "Compare BLE vs Wi-Fi vs LoRa for my use case",
        "Go deeper on the wired buses (UART/SPI/I2C/CAN)",
        "How do I make the link reliable end-to-end?",
        "Quiz me on choosing the right protocol",
    ),
)

# Registry order = match precedence: overwhelm/debug-skill first, then the specific RF/SDR and Linux
# classes, then the broad communication class (so a kernel/Yocto query is not swallowed by 'comms').
LEARNING_MAPS: dict[str, LearningMap] = {
    _DEBUG_SKILL_GENERAL.name: _DEBUG_SKILL_GENERAL,
    _WIRELESS_RF_SDR.name: _WIRELESS_RF_SDR,
    _EMBEDDED_LINUX.name: _EMBEDDED_LINUX,
    _BUILD_SYSTEMS.name: _BUILD_SYSTEMS,
    _COMMUNICATION_SYSTEMS.name: _COMMUNICATION_SYSTEMS,
}
