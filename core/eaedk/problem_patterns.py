"""Problem-pattern engine — EAEDK as a pattern navigator, not an answer box.

The general mechanism (UART bring-up is only the FIRST curated seed; SPI / I2C / HardFault / DMA /
driver-probe / Yocto-boot are meant to slot in as more ``ProblemPattern`` instances later):

    PatternMatcher  → which problem family is this?          (fine MATCH, after the Purpose gate)
    ProblemPattern  → the curated map: zones, decision tree, proof steps, traps
    ProofPathState  → where the learner is inside that tree  (rebuilt from the transcript each turn)
    EvidenceEvent   → normalise a messy reply to an evidence variable ("TX is silent" → tx=absent)
    DecisionNode    → branch on NORMALISED evidence, never on the exact phrase
    render          → the mentor explains the current node + proof step (deterministic, board-agnostic)

Everything here is curated data + a generic engine. No LLM authors a pattern; no board-specific fact
is ever asserted (the proof path ASKS for pins/instances, so there is nothing to hallucinate).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# --- The first-class objects --------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceVar:
    """A normalised observation the decision tree branches on. ``values`` maps a normalised value to
    the messy phrases that mean it, so different wordings collapse to the same branch key."""
    name: str
    values: dict[str, tuple[str, ...]]      # normalised_value -> trigger phrases
    labels: dict[str, str] = field(default_factory=dict)   # normalised_value -> human narration


@dataclass(frozen=True)
class DecisionNode:
    id: str
    zone: str
    proof_step: str                         # the ONE action to take now
    why: str                                # why this step, here
    expects: str | None = None              # the EvidenceVar this step produces (None = terminal)
    branches: dict[str, str] = field(default_factory=dict)   # evidence value -> next node id
    rules_out: str = ""                     # what reaching this node has ELIMINATED
    candidates: tuple[str, ...] = ()         # the narrowed set of remaining causes


@dataclass(frozen=True)
class ProblemPattern:
    name: str
    title: str
    match_groups: tuple[tuple[str, ...], ...]   # matches when EACH group has a hit (AND of ORs)
    zones: tuple[tuple[str, str], ...]          # (zone, one-line description)
    required_evidence: tuple[str, ...]          # context to gather up front
    beginner_traps: tuple[str, ...]
    entry: str
    nodes: dict[str, DecisionNode]
    evidence_vars: dict[str, EvidenceVar]


@dataclass
class ProofPathState:
    matched: bool = False
    pattern: ProblemPattern | None = None
    node: DecisionNode | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    awaiting: bool = False                   # asked for proof, reply did not resolve it yet


# --- The engine (generic over every pattern) ----------------------------------------------------

def _hit(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


def match_pattern(message: str) -> ProblemPattern | None:
    """Fine MATCH: the first registered pattern whose every signal group fires in the message."""
    low = " " + (message or "").lower() + " "
    for pat in PATTERNS.values():
        if all(_hit(low, group) for group in pat.match_groups):
            return pat
    return None


def extract_evidence(pattern: ProblemPattern, message: str) -> dict[str, str]:
    """Normalise a messy reply into evidence variables — phrase-independent. 'TX is silent',
    'no waveform on TX', 'pin is not toggling', 'logic analyzer shows nothing' all become the same
    ``tx_activity = absent`` because they share the curated trigger phrases."""
    low = " " + (message or "").lower() + " "
    out: dict[str, str] = {}
    for var in pattern.evidence_vars.values():
        for value, phrases in var.values.items():
            if _hit(low, phrases):
                out[var.name] = value
                break
    return out


def resolve(messages: list[dict]) -> ProofPathState:
    """Rebuild the proof-path state from the whole transcript (minimal, robust session state — no new
    storage, survives restarts). MATCH on the first user turn that fits a pattern, then replay every
    later user turn as evidence, advancing the decision tree on NORMALISED evidence only."""
    users = [m.get("content", "") for m in (messages or []) if m.get("role") == "user"]
    if not users:
        return ProofPathState()
    matched_at, pattern = None, None
    for i, msg in enumerate(users):
        pattern = match_pattern(msg)
        if pattern:
            matched_at = i
            break
    if pattern is None:
        return ProofPathState()

    node = pattern.nodes[pattern.entry]
    evidence: dict[str, str] = {}
    for msg in users[matched_at + 1:]:
        evidence.update(extract_evidence(pattern, msg))
        while node.expects and node.expects in evidence:        # advance as far as evidence allows
            nxt = node.branches.get(evidence[node.expects])
            if not nxt or nxt not in pattern.nodes:
                break
            node = pattern.nodes[nxt]
    awaiting = matched_at < len(users) - 1 and node.id == pattern.entry
    return ProofPathState(matched=True, pattern=pattern, node=node, evidence=evidence, awaiting=awaiting)


# --- Rendering (deterministic, board-agnostic; addresses the board by name only) ----------------

def _enumerate(items, fmt="  {i}. {x}"):
    return "\n".join(fmt.format(i=i, x=x) for i, x in enumerate(items, 1))


def render_proof_path(state: ProofPathState, board_name: str | None = None) -> str:
    p, node = state.pattern, state.node
    on = f" on {board_name}" if board_name else ""

    if state.awaiting:                                          # proof asked, reply unclear — re-ask
        return (f"I still need the result of that first proof step to branch correctly{on}. "
                f"{node.proof_step}\nThen tell me what you saw.")

    if node.id == p.entry:                                      # entry: frame the whole problem
        zones = _enumerate([f"{z} — {d}" for z, d in p.zones])
        L = [f"This is a {p.title}. Don't debug all of it at once{on} — first split it into zones so "
             "we change one thing at a time:",
             zones,
             "",
             f"First proof step: {node.proof_step}",
             f"Why this first: {node.why}",
             "",
             "So I can branch with you, tell me:",
             _enumerate(p.required_evidence, "  - {x}")]
        if p.beginner_traps:
            L += ["", f"Common trap to rule out: {p.beginner_traps[0]}"]
        return "\n".join(L)

    # advanced node: narrate from NORMALISED evidence, state what's ruled out, give the next step
    L = []
    obs = _observed(state)
    if obs:
        L.append(obs)
    if node.rules_out:
        L.append(node.rules_out)
    if node.candidates:
        L.append("So it is now one of: " + "; ".join(node.candidates) + ".")
    L.append(f"Next proof step: {node.proof_step}")
    L.append(f"Why: {node.why}")
    L.append("Run that and tell me the result — we'll branch from there.")
    return "\n".join(L)


def _observed(state: ProofPathState) -> str:
    """Narrate the evidence that advanced us, from its normalised label — never the user's raw words."""
    p = state.pattern
    bits = []
    for var_name, value in state.evidence.items():
        var = p.evidence_vars.get(var_name)
        if var and value in var.labels:
            bits.append(var.labels[value])
    return ("You reported " + "; ".join(bits) + ".") if bits else ""


# --- LLM-voiced proof path (docs/31): engine approves a packet → LLM voices → verifier guards -----
#
# The engine stays the authority: pattern, node, branch and proof step are deterministic. The packet
# is the ONLY thing the LLM may speak from. The packet is board-AGNOSTIC (it asks for pins/instances,
# never states them), so a correct voice has no board fact to invent — and the verifier blocks any it
# does invent, falling back to the deterministic render.

def build_packet(state: ProofPathState, board_name: str | None = None) -> dict:
    """The approved proof-path packet — exactly what the LLM is allowed to voice for this node."""
    p, node = state.pattern, state.node
    pk: dict = {"pattern_title": p.title, "board_name": board_name or "",
                "proof_step": node.proof_step, "why": node.why}
    if state.awaiting:
        pk["stage"] = "reask"
    elif node.id == p.entry:
        pk["stage"] = "intro"
        pk["zones"] = [f"{z} — {d}" for z, d in p.zones]
        pk["required_evidence"] = list(p.required_evidence)
        pk["trap"] = p.beginner_traps[0] if p.beginner_traps else ""
    else:
        pk["stage"] = "branch"
        pk["observed"] = _observed(state)
        pk["rules_out"] = node.rules_out
        pk["candidates"] = list(node.candidates)
    return pk


def render_packet_for_prompt(pk: dict) -> str:
    L = ["APPROVED PROOF-PATH PACKET (voice this; never change the engineering):",
         f"  problem: {pk['pattern_title']}"]
    if pk.get("board_name"):
        L.append(f"  board (address by name only — state NO fact about it): {pk['board_name']}")
    if pk["stage"] == "intro":
        L.append("  stage: FIRST step — frame the problem into zones, then give the first proof step")
        L.append("  zones:")
        L += [f"    - {z}" for z in pk["zones"]]
        L.append(f"  first proof step (keep the ACTION intact): {pk['proof_step']}")
        L.append(f"  why this first: {pk['why']}")
        L.append("  ask the learner for this evidence:")
        L += [f"    - {e}" for e in pk["required_evidence"]]
        if pk.get("trap"):
            L.append(f"  one trap worth a mention: {pk['trap']}")
    elif pk["stage"] == "branch":
        L.append("  stage: BRANCH — acknowledge the evidence, say what's ruled out, give the next step")
        if pk.get("observed"):
            L.append(f"  observed (normalised — narrate this, not the user's words): {pk['observed']}")
        if pk.get("rules_out"):
            L.append(f"  ruled out: {pk['rules_out']}")
        if pk.get("candidates"):
            L.append("  remaining causes (do NOT add to or change this list):")
            L += [f"    - {c}" for c in pk["candidates"]]
        L.append(f"  next proof step (keep the ACTION intact): {pk['proof_step']}")
        L.append(f"  why: {pk['why']}")
        L.append("  end by asking them to run it and report the result")
    else:  # reask
        L.append("  stage: RE-ASK — the reply didn't resolve the step; gently re-ask, no new content")
        L.append(f"  proof step to repeat: {pk['proof_step']}")
    return "\n".join(L)


# Board-specific tokens the engine NEVER puts in a packet, so any of these in a voiced answer that is
# not present verbatim in the packet is invented and must be blocked.
_INVENTED_CHECKS = (
    ("invented pin", r"\bP[A-K]\d{1,2}\b"),
    ("invented GPIO port", r"\bGPIO[A-K]\b"),
    ("invented UART/USART instance", r"\b(?:USART|UART)[\s-]?\d\b"),
    ("invented register/bus", r"\b(?:RCC|MODER|AFRL|AFRH|APB[12]|AHB[12]|BRR|CR1|IDR|ODR)\b"),
    ("invented clock frequency", r"\b\d+(?:\.\d+)?\s?[MG]Hz\b"),
    ("invented address", r"\b0x[0-9a-fA-F]{3,}\b"),
)


def verify_voiced(response: str, packet: dict) -> tuple[bool, list[str]]:
    """Check a voiced answer for invented board-specific facts (pins, registers, UART instance, clock,
    address) not present in the approved packet. Returns (safe, violations). Safe answers are shown;
    unsafe ones are blocked by the caller in favour of the deterministic render."""
    allowed = render_packet_for_prompt(packet).lower()
    violations: list[str] = []
    for label, rx in _INVENTED_CHECKS:
        for m in re.finditer(rx, response, re.I):
            if m.group(0).lower() not in allowed:
                violations.append(f"{label}: {m.group(0)}")
    return (not violations, violations)


# ================================================================================================
# SEED PATTERN #1 — UART bring-up failure (curated, deterministic). The only seeded pattern for now.
# ================================================================================================

_TX_ACTIVITY = EvidenceVar(
    name="tx_activity",
    values={
        "absent": ("silent", "no waveform", "not toggling", "isn't toggling", "no activity",
                   "nothing on tx", "nothing on the tx", "no signal", "shows nothing", "flat",
                   "stays low", "stays high", "no output on tx", "tx is dead", "dead pin",
                   "not moving", "doesn't move"),
        "present": ("toggling", "waveform", "i see activity", "pulses", "0x55 on tx", "data on tx",
                    "signal on tx", "tx is moving", "see the 0x55", "see pulses"),
    },
    labels={"absent": "no activity on the TX pin", "present": "the TX pin toggling"},
)

_GPIO_TOGGLE = EvidenceVar(
    name="gpio_toggle",
    values={
        "moves": ("gpio toggles", "pin toggles", "it toggles", "led blinks", "pin moves",
                  "moves as gpio", "works as gpio", "blinks"),
        "static": ("still nothing", "still silent", "still flat", "doesn't toggle", "no toggle",
                   "still dead", "still no", "nothing as gpio", "stays put"),
    },
    labels={"moves": "the pin toggles fine as a plain GPIO output",
            "static": "the pin stays dead even as a plain GPIO output"},
)

_UART_NODES = {
    "tx_probe": DecisionNode(
        id="tx_probe", zone="TX software path",
        proof_step="Send 0x55 ('U') in a tight loop and watch the TX pin on a scope or logic analyzer.",
        why="0x55 is a clean alternating pattern, and watching TX splits the problem in half: if TX "
            "never moves, the fault is BEFORE the wire (code, clock, or pinmux) and RX is irrelevant; "
            "if it moves, the fault is downstream (baud or RX).",
        expects="tx_activity",
        branches={"absent": "tx_silent", "present": "tx_present"}),
    "tx_silent": DecisionNode(
        id="tx_silent", zone="pinmux / clock / code",
        rules_out="A silent TX means nothing is leaving the chip — so RX wiring and baud are NOT the "
                  "problem yet; they are downstream of a TX that doesn't move.",
        candidates=("the pin's GPIO/pinmux (wrong alternate function)",
                    "the UART (or its GPIO port) clock not enabled",
                    "the wrong UART instance configured",
                    "code never reaching the transmit call"),
        proof_step="Drive that exact pin as a plain GPIO output (toggle it in a loop). If it moves, "
                   "switch it back to the UART's alternate function; if it stays dead, the fault is the "
                   "clock or your code never runs.",
        why="Toggling the pin as GPIO isolates the layer: a moving pin proves the GPIO, clock and code "
            "path are alive and the fault is the UART alternate-function/instance; a dead pin proves "
            "it is the peripheral clock or execution never reaching there.",
        expects="gpio_toggle",
        branches={"moves": "af_or_instance", "static": "clock_or_code"}),
    "tx_present": DecisionNode(
        id="tx_present", zone="baud / RX path",
        rules_out="TX is leaving the chip, so the transmit path, its clock and pinmux are fine — the "
                  "fault is downstream.",
        candidates=("baud mismatch between the two ends",
                    "TX→RX miswire (or TX→TX)",
                    "the receiver's pin/alternate-function"),
        proof_step="Confirm both ends use the EXACT same baud, computed from the real core clock (not "
                   "the datasheet max), and that your board's TX goes to the other side's RX.",
        why="A toggling TX with no received bytes is almost always a baud divisor computed from the "
            "wrong clock, or TX/RX crossed."),
    "af_or_instance": DecisionNode(
        id="af_or_instance", zone="alternate function / instance",
        rules_out="The pin, its clock and your code all work (it toggled as GPIO).",
        candidates=("the alternate-function number for that pin doesn't map to THIS UART's TX",
                    "you initialised a different UART instance than the pin belongs to"),
        proof_step="In the datasheet's alternate-function table, confirm the AF number you set on that "
                   "exact pin maps to the TX of the UART instance you are initialising.",
        why="A pin that toggles as GPIO but stays silent as UART is the classic wrong-AF / wrong-"
            "instance mismatch — the code compiles and the board is silent."),
    "clock_or_code": DecisionNode(
        id="clock_or_code", zone="peripheral clock / execution",
        rules_out="The pin is dead even as GPIO, so this is not a UART alternate-function problem.",
        candidates=("the GPIO port's peripheral clock is not enabled before you configure the pin",
                    "execution never reaches your toggle (it faulted or looped earlier)"),
        proof_step="Enable that GPIO port's clock FIRST, then blink an LED (or set a breakpoint) at the "
                   "very top of the toggle to prove the code runs at all.",
        why="A pin that won't move even as GPIO is either un-clocked silicon or code that never gets "
            "there — both look identical from outside and must be separated before touching the UART."),
}

UART_BRINGUP = ProblemPattern(
    name="uart_bringup",
    title="UART bring-up problem",
    match_groups=(
        ("uart", "usart", " serial ", "serial port"),
        ("not working", "isn't working", "isnt working", "doesn't work", "doesnt work", "no output",
         "not printing", "no print", "nothing", "silent", "garbage", "no data", "fail", "broken",
         "dead", "won't transmit", "wont transmit", "not transmit", "not receiv", "no signal",
         "not getting", "can't get", "cant get"),
    ),
    zones=(
        ("Wiring / electrical", "grounds tied, TX→RX (not TX→TX), levels correct"),
        ("Pinmux / GPIO mode", "the pin is on the UART's alternate function, not plain GPIO"),
        ("UART clock & baud", "the peripheral is clocked and the baud divisor uses the real clock"),
        ("TX/RX software path", "init order, the transmit/receive calls actually run"),
        ("Interrupt / DMA path", "if used: the right IRQ/stream, flags cleared, buffers set"),
    ),
    required_evidence=(
        "Which board / MCU?",
        "Which UART/USART instance?",
        "Which TX and RX pins?",
        "What baud rate?",
        "Polling, interrupt, or DMA?",
    ),
    beginner_traps=(
        "printing before the UART is initialised sends nothing",
        "wiring TX→TX instead of TX→RX, or forgetting a common ground",
        "computing the baud divisor from the datasheet's max clock instead of the real reset clock",
        "forgetting to enable the UART's (or its GPIO port's) peripheral clock",
    ),
    entry="tx_probe",
    nodes=_UART_NODES,
    evidence_vars={"tx_activity": _TX_ACTIVITY, "gpio_toggle": _GPIO_TOGGLE},
)


# The pattern registry. Add the next curated pattern here (SPI / I2C / HardFault / …); the engine
# above is already general over all of them.
PATTERNS: dict[str, ProblemPattern] = {
    UART_BRINGUP.name: UART_BRINGUP,
}
