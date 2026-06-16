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
class LearningMap:
    """A broad area a learner is confused about HOW TO APPROACH (not a bug, not a single decision).
    Curated data, like ProblemPattern — adding one is a registry entry, not code."""
    name: str
    title: str
    match_groups: tuple[tuple[str, ...], ...]   # matches when EACH group has a hit (AND of ORs)
    possible_meanings: tuple[str, ...]          # what people actually mean by this
    prerequisites: tuple[str, ...]
    recommended_route: tuple[str, ...]          # an ordered route
    wrong_starts: tuple[str, ...]               # where beginners waste months
    first_step: str                             # the first move that proves direction
    clarifying_question: str                    # the one question that sharpens the route
    needs_linux: bool = False                   # area requires a Linux-class (Cortex-A) board


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


def render_learning_map(conn, lm: LearningMap, board_name: str | None = None) -> str:
    L = [f"This is a learning-direction question, not a single bug — so let's get you oriented on "
         f"{lm.title}. You're not lost; this is a known area, and here is the map.", ""]

    if lm.needs_linux and board_name:
        _board, soc = repo.load_board(conn, board_name)
        if soc is not None and not mentor.supports_linux(soc):
            L.append(f"First, a grounding fact: {board_name} is a microcontroller (no MMU), so it "
                     f"cannot run Linux — {lm.title} needs a Linux-class board (a Cortex-A SBC). If "
                     "that's a surprise, settling that is itself step zero.")
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
    L.append(f"So I can point you precisely — {lm.clarifying_question}")
    return "\n".join(L)


# ================================================================================================
# SEED LEARNING MAPS — curated, the first two areas. Add more by registering another LearningMap.
# ================================================================================================

_EMBEDDED_LINUX = LearningMap(
    name="embedded_linux",
    title="embedded Linux / kernel & driver development",
    match_groups=(
        ("kernel", "driver development", "device driver", "char device", "device tree", "devicetree",
         "kernel module", "linux driver", "embedded linux", "write a driver", "writing drivers",
         "probe", "platform driver"),
    ),
    possible_meanings=(
        "writing a Linux kernel DRIVER for a device (the most common goal)",
        "understanding how the kernel boots and schedules (theory)",
        "configuring/booting a board's existing kernel (board bring-up, not driver writing)",
    ),
    prerequisites=(
        "solid C and pointers", "how a process/syscall reaches the kernel",
        "comfort on the Linux command line and with a cross-compiler",
    ),
    recommended_route=(
        "Boot a stock kernel on a Linux-class board and get a shell — prove the platform works.",
        "Write a trivial char-device module (insmod/rmmod, printk) — learn the module lifecycle.",
        "Bind a platform driver to a device-tree node — learn probe() and the of_match_table.",
        "Drive one real peripheral (GPIO/I2C) from your driver — the first useful driver.",
    ),
    wrong_starts=(
        "reading the whole kernel source front-to-back",
        "starting with a complex subsystem (networking, USB) before a char device",
        "trying it on a microcontroller — there is no Linux there",
    ),
    first_step="On a Linux-class board, build and `insmod` a hello-world module that printk's, and read "
               "it back with `dmesg`. That one loop proves your toolchain, kernel headers, and module "
               "lifecycle all work.",
    clarifying_question="are you trying to WRITE a driver, or to BOOT/configure Linux on a board? Those "
                        "are different routes.",
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

LEARNING_MAPS: dict[str, LearningMap] = {
    _EMBEDDED_LINUX.name: _EMBEDDED_LINUX,
    _BUILD_SYSTEMS.name: _BUILD_SYSTEMS,
}
