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
    # Optional extra forbidden-claim regexes this pattern wants blocked on top of the generic,
    # peripheral-agnostic categories (e.g. a vendor API or board-specific setup string). Most
    # patterns need none — the generic categories already cover pins/registers/instances/clocks.
    sensitive_terms: tuple[str, ...] = ()


@dataclass
class ProofPathState:
    matched: bool = False
    pattern: ProblemPattern | None = None
    node: DecisionNode | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    awaiting: bool = False                   # asked for proof, reply did not resolve it yet
    # User-reported hardware tokens from the transcript (pins, instances, registers, clocks, addresses).
    # These are NOT verified facts — only quotable as "you said X".
    user_reported_evidence: list[str] = field(default_factory=list)


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


def extract_user_reported_evidence(message: str) -> dict[str, list[str]]:
    """Extract raw hardware tokens the user reported — pins, peripheral instances, registers, clock
    frequencies, addresses. These are NOT verified facts; they can only be quoted as "you said X".

    Uses the same generic categories as the verifier, so a user saying "PA9" can be quoted but not
    asserted as a verified truth. Returns {token_type: [raw_token, ...]} mapping with ALL tokens."""
    low = message or ""
    out: dict[str, list[str]] = {}
    for label, rx in _GENERIC_CHECKS:
        for m in re.finditer(rx, low):
            out.setdefault(label, []).append(m.group(0))
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
    user_evidence: list[str] = []
    for msg in users[matched_at + 1:]:
        evidence.update(extract_evidence(pattern, msg))
        # Collect user-reported hardware tokens (pins, instances, registers, clocks, addresses)
        # from each turn — these are quotable but not verified.
        extracted = extract_user_reported_evidence(msg)
        for tokens in extracted.values():
            user_evidence.extend(tokens)
        while node.expects and node.expects in evidence:        # advance as far as evidence allows
            nxt = node.branches.get(evidence[node.expects])
            if not nxt or nxt not in pattern.nodes:
                break
            node = pattern.nodes[nxt]
    awaiting = matched_at < len(users) - 1 and node.id == pattern.entry
    return ProofPathState(matched=True, pattern=pattern, node=node, evidence=evidence,
                          awaiting=awaiting, user_reported_evidence=user_evidence)


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

def build_packet(state: ProofPathState, board_name: str | None = None,
                 community_report=None) -> dict:
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
    pk["sensitive_terms"] = list(p.sensitive_terms)      # pattern-declared extra forbidden claims
    # User-reported evidence provenance — raw tokens the user mentioned (pins, instances, etc.).
    # These are NOT verified facts; the LLM may only quote them as "you said X".
    pk["user_reported_evidence"] = list(state.user_reported_evidence)
    if community_report is not None:
        pk.update(_community_report_packet_fields(community_report))
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
    if pk.get("community_confirm_cases") or pk.get("community_compare_cases"):
        L += [
            "",
            "External field experience — unverified, source-backed, for CONFIRM/COMPARE only:",
            "  these cases are not verified board facts",
            "  local proof step remains primary; do NOT replace or rewrite it",
            "  source links are provenance, not the answer",
            "  speculated cases cannot drive HELP",
        ]
        if pk.get("community_summary_reason"):
            L.append(f"  summary: {pk['community_summary_reason']}")
        if pk.get("best_external_verification_step"):
            L.append("  best external verification step (secondary CONFIRM support only): "
                     f"{pk['best_external_verification_step']}")
        if pk.get("community_confirm_cases"):
            L.append("  CONFIRM support cases:")
            for c in pk["community_confirm_cases"]:
                L.append(_render_community_case_line(c))
        if pk.get("community_compare_cases"):
            L.append("  COMPARE support cases:")
            for c in pk["community_compare_cases"]:
                L.append(_render_community_case_line(c))
        if pk.get("source_links"):
            L.append("  source links (provenance only, not the answer):")
            L += [f"    - {link}" for link in pk["source_links"]]
    return "\n".join(L)


# Generic, peripheral- and vendor-AGNOSTIC categories of board-specific claims. The engine never puts
# any of these in a packet, so any that a voiced answer states without it being in the packet (or a
# verified board fact carried there) is invented. These are CATEGORIES — a peripheral family + a
# number, several pin conventions, register forms — not a UART/STM32 token list. A pattern may add
# more via ProblemPattern.sensitive_terms.
_PERIPHERAL = ("UART", "USART", "LPUART", "SPI", "QSPI", "I2C", "IIC", "I2S", "SAI", "ADC", "DAC",
               "TIM", "TIMER", "PWM", "CAN", "FDCAN", "RTC", "DMA", "USB", "SDIO", "SDMMC", "COMP",
               "OPAMP", "DCMI", "LTDC", "ETH")
_GENERIC_CHECKS = (
    # PeripheralX — any peripheral family immediately followed by an instance number (SPI2, I2C1, …)
    ("peripheral instance", r"\b(?:" + "|".join(_PERIPHERAL) + r")\s?\d+\b"),
    # pins across conventions: STM32/AVR PA9·PB6, ESP32 GPIO21, Arduino D13
    ("pin", r"\bP[A-L]\d{1,2}\b|\bGPIO\s?\d{1,2}\b|\bD\d{1,2}\b"),
    # registers: vendor underscore form (SPI_CR1, I2C_CR2), struct-access (GPIOA->ODR), AVR port
    # registers (PORTB, DDRB), and common bare register/bus/clock-domain names across families
    ("register", r"\b[A-Z][A-Z0-9]*_[A-Z][A-Z0-9]+\b|\b[A-Z]\w*->\w+\b|\bPORT[A-L]\d?\b|\bDDR[A-L]\b|"
                 r"\b(?:RCC|MODER|ODR|IDR|AFRL?|AFRH?|BRR|APB\d|AHB\d|CR[12]|CCR|PSC|ARR)\b"),
    ("clock frequency", r"\b\d+(?:\.\d+)?\s?[kMG]?Hz\b"),
    ("address", r"\b0x[0-9a-fA-F]{3,}\b"),
)

_COMMUNITY_PACKET_KEYS = (
    "community_confirm_cases",
    "community_compare_cases",
    "best_external_verification_step",
    "source_links",
    "community_summary_reason",
    "has_actionable_external_experience",
)

_COMMUNITY_EXTRA_REDACTIONS = (
    ("alternate-function value", r"\bAF\s?\d+\b"),
)


def _redact_unverified_community_text(text: str) -> str:
    """Remove board-specific tokens from unverified community snippets before packet rendering.

    Community material is useful as field experience, but it must not teach the verifier that a pin,
    register, clock, address or peripheral instance is allowed grounding.
    """
    out = str(text or "")
    redactions = list(_GENERIC_CHECKS) + list(_COMMUNITY_EXTRA_REDACTIONS)
    for label, rx in redactions:
        out = re.sub(rx, f"[field-reported {label}]", out, flags=re.I)
    return out


def _evidence_quality_from_reason(reason: str) -> str:
    m = re.search(r"evidence_quality=([a-z_]+)", reason or "")
    return m.group(1) if m else ""


def _community_match_packet(match, include_step: bool) -> dict:
    return {
        "matched_facts": [_redact_unverified_community_text(x)
                          for x in getattr(match, "matched_facts", ())],
        "differing_facts": [_redact_unverified_community_text(x)
                            for x in getattr(match, "differing_facts", ())],
        "case_confidence": getattr(match, "case_confidence", 0.0),
        "evidence_quality": _evidence_quality_from_reason(getattr(match, "reason", "")),
        "suggested_verification_step": (
            _redact_unverified_community_text(
                getattr(match, "suggested_verification_step", ""))
            if include_step else ""
        ),
    }


def _community_report_packet_fields(report) -> dict:
    confirm = tuple(getattr(report, "confirm_cases", ()) or ())
    compare = tuple(getattr(report, "compare_cases", ()) or ())
    usable = bool(confirm or compare)
    return {
        "community_confirm_cases": [_community_match_packet(m, include_step=True)
                                    for m in confirm[:3]],
        "community_compare_cases": [_community_match_packet(m, include_step=False)
                                    for m in compare[:3]],
        "best_external_verification_step": (
            _redact_unverified_community_text(
                getattr(report, "best_verification_step", "") or "")
            if confirm else ""
        ),
        "source_links": list(dict.fromkeys(getattr(report, "top_reference_links", ()) or ()))
                        if usable else [],
        "community_summary_reason": (
            _redact_unverified_community_text(getattr(report, "summary_reason", "") or "")
            if usable else ""
        ),
        "has_actionable_external_experience": bool(
            getattr(report, "has_actionable_external_experience", False) and confirm
        ),
    }


def _render_community_case_line(case: dict) -> str:
    bits = [f"confidence {case.get('case_confidence', 0.0)}"]
    if case.get("evidence_quality"):
        bits.append(f"evidence {case['evidence_quality']}")
    if case.get("matched_facts"):
        bits.append("matched " + "; ".join(case["matched_facts"]))
    if case.get("differing_facts"):
        bits.append("differs " + "; ".join(case["differing_facts"]))
    if case.get("suggested_verification_step"):
        bits.append("external step " + case["suggested_verification_step"])
    return "    - " + "; ".join(bits)


def _packet_for_verifier_allowlist(packet: dict) -> dict:
    """Strip unverified community fields before deriving verifier grounding."""
    pk = dict(packet)
    for key in _COMMUNITY_PACKET_KEYS:
        pk.pop(key, None)
    return pk


def verify_voiced(response: str, packet: dict) -> tuple[bool, list[str]]:
    """Check a voiced answer for invented board/peripheral-specific facts not present in the approved
    packet. Generic across peripherals and vendors (pins, registers, instances, clocks, addresses),
    plus any extra forbidden patterns the pattern declared. Returns (safe, violations); unsafe answers
    are blocked by the caller in favour of the deterministic render.

    User-reported evidence (tokens the user mentioned in their messages) is ALLOWED only when
    phrased as a quote — e.g. "you said PA9", "you reported SPI2", "you reported a 10MHz clock".
    If the same token is phrased as an instruction or assertion ("Configure PA9", "Use SPI2"),
    it is BLOCKED unless verified in the packet. The provenance categories are:
      - verified_fact        — from DB / board facts / pattern object (can be stated as fact)
      - user_reported_evidence — from transcript (quotable as "you said", never asserted as truth)
      - pattern_guidance      — from ProblemPattern/DecisionNode (general proof-path guidance)
      - unsupported_claim     — invented by LLM (blocked/fallback)"""
    allowed = render_packet_for_prompt(_packet_for_verifier_allowlist(packet)).lower()
    checks = list(_GENERIC_CHECKS) + [("pattern-sensitive claim", rx)
                                      for rx in packet.get("sensitive_terms", ())]

    # Collect user-reported tokens from the packet — these are quotable but not assertable.
    user_tokens: set[str] = set()
    for token in packet.get("user_reported_evidence", []):
        user_tokens.add(token.strip().lower())

    violations: list[str] = []
    for label, rx in checks:
        for m in re.finditer(rx, response, re.I):
            token = m.group(0)
            token_lower = token.lower()

            # Token already in the allowed text (packet rendering) — always fine.
            if token_lower in allowed:
                continue

            # User-reported token: only allowed if phrased as a quote ("you said", "you reported")
            if token_lower in user_tokens:
                # Check the surrounding context for quote phrasing (within 60 chars before)
                start = max(0, m.start() - 60)
                prefix = response[start:m.start()].lower()
                if any(q in prefix for q in ("you said", "you reported", "you note",
                                              "you mention", "based on your", "you told me",
                                              "as you noted", "you reported that")):
                    continue

            # If it wasn't in user_tokens OR it was but not phrased as a quote — violate
            violations.append(f"{label}: {token}")

    return (not violations, violations)


def flag_invented_claims(response: str, allowed_text: str,
                         user_text: str = "") -> tuple[bool, list[str]]:
    """General verifier for a conversationally-taught answer: flag any board/peripheral-specific claim
    (pin, register, instance, clock, address) that is NOT present in ``allowed_text`` (the verified
    fact packet the LLM was told to teach from). Reuses the same generic, vendor-agnostic checks as
    verify_voiced. Returns (safe, violations); the caller falls back to the deterministic render when
    unsafe so an invented hardware fact never reaches the learner.

    USER PROVENANCE: a hardware token the user themselves supplied in ``user_text`` (the current and
    immediately preceding turn) is grounded by the user — it is not an invention, so the LLM may
    restate and reason about it. Only tokens present in NEITHER the packet NOR the user's own words
    are flagged. This avoids suppressing a legitimate answer just because it echoes the user's value
    (e.g. the user says "400kHz" and the teach repeats it)."""
    allowed = (allowed_text or "").lower()
    user_supplied = {t.lower()
                     for tokens in extract_user_reported_evidence(user_text or "").values()
                     for t in tokens}
    violations: list[str] = []
    for label, rx in _GENERIC_CHECKS:
        for m in re.finditer(rx, response, re.I):
            tok = m.group(0).lower()
            if tok in allowed or tok in user_supplied:
                continue
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


# ================================================================================================
# SEED PATTERN #2 — I2C bring-up failure (curated, deterministic). Encodes the real debugging
# order: open-drain/pull-ups first (the #1 cause), then addressing, then the 7/8-bit shift vs the
# device-or-master-clock split.
# ================================================================================================

_BUS_IDLE = EvidenceVar(
    name="bus_idle",
    values={
        "low": ("stuck low", "stays low", "held low", "pulled low", "line is low", "lines are low",
                "sda is low", "scl is low", "sda low", "scl low", "won't go high", "wont go high",
                "not going high", "never goes high", "reads 0v", "at 0v", "stuck at 0", "bus is low",
                "can't pull high", "cant pull high", "low at idle"),
        "high": ("idle high", "idles high", "high at idle", "both high", "lines are high",
                 "sitting high", "sit high", "at 3.3", "at 3v3", "at vdd", "both at 3.3",
                 "idle at 3.3", "sitting at 3.3", "lines idle high", "resting high"),
    },
    labels={"low": "one or both I2C lines stuck low at idle (the bus can't be released)",
            "high": "both SDA and SCL idling high — the electrical base is good"},
)

_SCAN_RESULT = EvidenceVar(
    name="scan_result",
    values={
        "found": ("acks at", "ack at", "shows up at", "shows up", "found at", "detected at",
                  "appears at", "responds at", "it's at 0x", "its at 0x", "scan shows",
                  "scanner finds", "scan finds a", "device found", "found a device", "sees it at",
                  "i see it at", "shows at"),
        "none": ("no ack", "nacks", "no device", "nothing shows", "nothing responds", "no response",
                 "finds nothing", "scan finds nothing", "empty scan", "scans empty", "not detected",
                 "no devices", "doesn't show up", "doesnt show up", "nothing acks", "no one acks"),
    },
    labels={"found": "the bus scan finds a device (possibly at an unexpected address)",
            "none": "the bus scan finds no device at any address"},
)

_I2C_NODES = {
    "bus_idle_check": DecisionNode(
        id="bus_idle_check", zone="Bus electrical (pull-ups)",
        proof_step="With the bus idle (no transfers running), measure SDA and SCL against ground. "
                   "Both should sit at Vdd (e.g. 3.3 V).",
        why="I2C is open-drain: devices can only pull the lines LOW, and pull-up resistors are what "
            "pull them HIGH. If either line isn't resting high at idle, the bus can never be "
            "released — so addressing, ACK and data are all downstream of this and irrelevant until "
            "it is fixed.",
        expects="bus_idle",
        branches={"low": "lines_low", "high": "addr_probe"}),
    "lines_low": DecisionNode(
        id="lines_low", zone="pull-ups / stuck device / wiring",
        rules_out="A line stuck low at idle is electrical — so the device address, ACK and your "
                  "transfer code are NOT the problem yet; nothing can work until the line is released.",
        candidates=("missing pull-up resistors (open-drain cannot pull high on its own)",
                    "pull-ups too weak/strong, or tied to the wrong rail",
                    "one device holding the line (unpowered, stuck, or clock-stretching forever)",
                    "SDA/SCL shorted together, swapped, or shorted to ground"),
        proof_step="Confirm a pull-up (typically 2.2k-4.7k) sits on BOTH SDA and SCL to the bus "
                   "voltage. Then remove devices one at a time — if a line springs back high when one "
                   "comes off, that device was holding it.",
        why="A permanently-low line is a wiring/electrical fault, not software. Checking the pull-ups "
            "and pulling devices one at a time separates 'no pull-up' from 'a device is holding the "
            "bus' before any code matters."),
    "addr_probe": DecisionNode(
        id="addr_probe", zone="addressing / ACK",
        rules_out="Both lines idle high, so pull-ups and the bus electrical layer are good — the fault "
                  "is now addressing or the device itself.",
        candidates=("the address in code doesn't match the device (7-bit vs 8-bit shift)",
                    "the device isn't powered or its address pins are wrong",
                    "the master isn't actually clocking SCL"),
        proof_step="Run an I2C bus scan (probe every 7-bit address from 0x08 to 0x77) and note whether "
                   "ANY address ACKs.",
        why="With the electrical base proven good, a scan is the one test that splits 'wrong address' "
            "(something ACKs, just not where you expected) from 'nothing answers at all' (device or "
            "master clock).",
        expects="scan_result",
        branches={"found": "addr_mismatch", "none": "no_ack"}),
    "addr_mismatch": DecisionNode(
        id="addr_mismatch", zone="address format (7-bit vs 8-bit)",
        rules_out="A device ACKs on the scan, so wiring, pull-ups, clocking and the peripheral are all "
                  "working — the fault is the address constant in your code.",
        candidates=("using the datasheet's 8-bit read/write address where the driver wants the 7-bit "
                    "address",
                    "the classic <<1 shift: your value is double (or half) the scanned address",
                    "wrong A0/A1/A2 address-strap pins on the device"),
        proof_step="Compare the address the scan reported with the one in your code. If they differ by "
                   "a factor of two (one is the other shifted left by 1), that is the 7-bit/8-bit "
                   "mismatch — use the form your HAL expects (most want the 7-bit address).",
        why="A scan hit at address X while your code uses 2X (or X/2) is the canonical 7-bit-vs-8-bit "
            "shift bug: the device is fine, the constant is wrong."),
    "no_ack": DecisionNode(
        id="no_ack", zone="device power / SCL clocking",
        rules_out="The lines idle high, so this is NOT the missing-pull-up fault; with nothing "
                  "answering, the silence is the device or the master's clock.",
        candidates=("the target device isn't powered (or its enable/reset pin is wrong)",
                    "SCL never actually clocks — the I2C peripheral clock isn't enabled, or the pins "
                    "aren't on the I2C alternate function",
                    "the wrong I2C instance/pins are configured",
                    "bus speed set faster than the device supports"),
        proof_step="Confirm the device has power and its address/enable pins are set, then scope SCL "
                   "during a transfer. No pulses on SCL → the fault is the master/peripheral side; "
                   "clean clock but still no ACK → the device side.",
        why="With the electrical base good and nothing answering, you must separate 'the master never "
            "clocks' from 'the device never answers' — scoping SCL during a transfer is the single "
            "measurement that splits them."),
}

I2C_BRINGUP = ProblemPattern(
    name="i2c_bringup",
    title="I2C bring-up problem",
    match_groups=(
        ("i2c", "i²c", "iic", "sda", "scl", "two-wire", "sccb"),
        ("not working", "isn't working", "isnt working", "doesn't work", "doesnt work", "no ack",
         "nack", "not responding", "no response", "not respond", "won't respond", "wont respond",
         "not detected", "isn't detected", "can't find", "cant find", "not found", "no device",
         "reads 0xff", "all 0xff", "reads ff", "stuck low", "bus stuck", "bus hang", "hangs",
         "no data", "garbage", "won't ack", "wont ack", "not getting", "can't read", "cant read",
         "device is dead", "not showing"),
    ),
    zones=(
        ("Bus electrical (pull-ups)", "SDA and SCL idle HIGH via pull-ups — open-drain only pulls low"),
        ("Addressing", "7-bit vs 8-bit address; the <<1 shift and the R/W bit"),
        ("Device power / enable", "the target is powered and its address-strap pins are set"),
        ("Clocking / pinmux", "the I2C peripheral is clocked and SDA/SCL are on the I2C alt function"),
        ("Bus contention / speed", "clock-stretching, a stuck device, or a speed the slave can't meet"),
    ),
    required_evidence=(
        "Which board / MCU and which I2C instance?",
        "What is the device and its datasheet address (7-bit or 8-bit)?",
        "What pull-up resistors are on SDA/SCL (value, to what voltage)?",
        "How many devices are on the bus?",
        "What bus speed (100 kHz / 400 kHz)?",
    ),
    beginner_traps=(
        "I2C is open-drain — with no pull-ups the lines never go high and nothing works (the #1 cause)",
        "using the datasheet's 8-bit read/write address directly when the driver wants the 7-bit "
        "address (the <<1 shift)",
        "leaving the device's address-select pins (A0/A1/A2) floating",
        "driving SDA/SCL as plain GPIO instead of the I2C alternate function",
        "one stuck or unpowered device holding a line low kills the whole bus",
    ),
    entry="bus_idle_check",
    nodes=_I2C_NODES,
    evidence_vars={"bus_idle": _BUS_IDLE, "scan_result": _SCAN_RESULT},
)


# ================================================================================================
# SEED PATTERN #3 — SPI bring-up / silent-data failure (curated, deterministic). Encodes the real
# debugging order: chip-select first (a deaf slave), then the clock, then CPOL/CPHA-vs-MISO.
# ================================================================================================

_CS_STATE = EvidenceVar(
    name="cs_state",
    values={
        "not_asserted": ("stays high", "cs stays high", "cs is high", "never goes low",
                         "cs never", "chip select stays high", "cs high the whole", "cs floating",
                         "nss high", "cs not asserting", "cs doesn't go low", "cs doesnt go low",
                         "cs stuck high", "ss stays high"),
        "asserted": ("cs goes low", "cs asserts", "cs drops", "goes low", "nss low", "cs is low",
                     "cs pulses low", "asserts low", "cs low during", "chip select goes low",
                     "ss goes low"),
    },
    labels={"not_asserted": "chip-select never asserts (the slave is never selected)",
            "asserted": "chip-select asserts correctly for the transfer"},
)

_SCLK_STATE = EvidenceVar(
    name="sclk_state",
    values={
        "present": ("sclk toggles", "clock toggles", "sck toggles", "see the clock", "i see sclk",
                    "clock pulses", "8 clocks", "clock is present", "clock looks", "sclk is toggling",
                    "clock edges"),
        "absent": ("no clock", "no sclk", "sclk is dead", "clock is dead", "no sck", "sclk flat",
                   "clock not toggling", "no clock pulses", "no edges", "dead clock", "sck is dead",
                   "sclk stays"),
    },
    labels={"present": "SCLK is clocking the slave (clean edges during the transfer)",
            "absent": "SCLK never clocks — no edges leave the master"},
)

_SPI_NODES = {
    "cs_check": DecisionNode(
        id="cs_check", zone="Chip-select (CS/NSS)",
        proof_step="Scope the slave's CS (NSS/SS) pin during a transfer. It must go LOW for the whole "
                   "transaction and return HIGH after.",
        why="An SPI slave only listens and drives MISO while CS is asserted (almost always active-low). "
            "If CS never goes low — still a GPIO, the wrong pin, software never toggling it, or inverted "
            "polarity — the slave is electrically deaf and MISO just floats. Mode and data are moot "
            "until CS asserts.",
        expects="cs_state",
        branches={"not_asserted": "cs_fault", "asserted": "clk_check"}),
    "cs_fault": DecisionNode(
        id="cs_fault", zone="CS pin / polarity / control",
        rules_out="CS never asserting means the slave is never selected — so SPI mode, MOSI data and "
                  "the MISO line are NOT the problem yet.",
        candidates=("CS still configured as a plain GPIO, or never driven",
                    "software never pulls CS low around the transfer (hardware-NSS vs software-NSS mix-up)",
                    "the wrong pin is being used for CS",
                    "inverted polarity — the slave wants active-low but CS is driven the other way"),
        proof_step="Drive CS explicitly low in software immediately before the transfer and high after "
                   "(or confirm hardware NSS is enabled and mapped to the right pin), and confirm the "
                   "slave's CS is active-low.",
        why="A slave that never sees CS asserted can do nothing; fixing selection is a precondition for "
            "any clock, mode or data debugging."),
    "clk_check": DecisionNode(
        id="clk_check", zone="Clock (SCLK)",
        rules_out="CS asserts, so the slave is selected — selection is not the fault.",
        candidates=("the SPI peripheral clock isn't enabled",
                    "SCLK is on the wrong pin / not the SPI alternate function",
                    "the wrong SPI instance, or the peripheral isn't in master mode"),
        proof_step="With CS asserting, scope SCLK during the transfer — it must show the expected edges "
                   "(8 per byte).",
        why="With the slave selected, no clock means no data can move. A dead SCLK is the master's "
            "clock generation — peripheral clock, pinmux, instance, or not actually in master mode.",
        expects="sclk_state",
        branches={"absent": "clk_fault", "present": "mode_or_miso"}),
    "clk_fault": DecisionNode(
        id="clk_fault", zone="peripheral clock / pinmux / master mode",
        rules_out="CS asserts but SCLK is dead — so this is the master's clock generation, not the slave "
                  "and not the data lines.",
        candidates=("the SPI peripheral clock is not enabled before configuration",
                    "the SCLK pin is left as GPIO or on the wrong alternate function",
                    "the wrong SPI instance is configured",
                    "the peripheral is not actually in master mode"),
        proof_step="Enable the SPI peripheral clock, confirm SCLK is on the SPI alternate function and "
                   "the right instance, and that the peripheral is configured as master; re-scope SCLK.",
        why="A selected slave with no clock is always the master side; isolating clock/pinmux/instance "
            "before touching mode keeps you from chasing the data when nothing is even clocking."),
    "mode_or_miso": DecisionNode(
        id="mode_or_miso", zone="SPI mode (CPOL/CPHA) / MISO return",
        rules_out="CS selects the slave and SCLK clocks it, so selection and clocking are fine — the "
                  "fault is the data itself: SPI mode or the MISO return path.",
        candidates=("CPOL/CPHA (SPI mode 0-3) mismatch — bytes come back shifted or garbled",
                    "MISO floating (reads all 0xFF) or stuck (all 0x00) — the slave isn't driving it",
                    "MSB-first vs LSB-first mismatch",
                    "MOSI not carrying the command the slave expects (so it returns nothing useful)"),
        proof_step="Match the SPI mode (CPOL/CPHA) to the slave's datasheet exactly, then scope MISO "
                   "during a read: all-0xFF means nothing drives it (slave not selected/powered or wrong "
                   "pin), all-0x00 likewise, and garbled-but-present means a mode or bit-order mismatch.",
        why="A selected, clocked slave returning wrong data is almost always a CPOL/CPHA mismatch or a "
            "MISO nobody is driving — the datasheet's mode and a scope on MISO separate the two."),
}

SPI_BRINGUP = ProblemPattern(
    name="spi_bringup",
    title="SPI bring-up problem",
    match_groups=(
        ("spi", "mosi", "miso", "sclk", " sck ", "chip select", "chip-select", "nss"),
        ("not working", "isn't working", "isnt working", "doesn't work", "doesnt work", "no data",
         "reads all 0xff", "all 0xff", "reads 0xff", "reads all zeros", "all zeros", "reads 0x00",
         "all 0x00", "garbage", "garbled", "no response", "not responding", "miso floating",
         "miso is floating", "nothing back", "nothing comes back", "no miso", "silent", "not reading",
         "won't read", "wont read", "reads ff", "reads nothing", "shifted", "corrupt", "broken",
         "not getting", "dead"),
    ),
    zones=(
        ("Chip-select (CS/NSS)", "CS must assert (usually low) for the whole transfer or the slave is deaf"),
        ("Clock (SCLK)", "the master must actually generate SCLK edges — 8 per byte"),
        ("SPI mode (CPOL/CPHA)", "both ends must agree on mode 0-3 or data is shifted/garbled"),
        ("Data lines (MOSI/MISO)", "MOSI carries the command; MISO must be driven by a selected slave"),
        ("Bit order / speed", "MSB vs LSB first, and a clock the slave can keep up with"),
    ),
    required_evidence=(
        "Which board / MCU and which SPI instance?",
        "What is the slave device and its SPI mode (CPOL/CPHA)?",
        "Which pins for SCLK / MOSI / MISO / CS?",
        "Is CS hardware-NSS or driven in software?",
        "What does MISO read — all 0x00, all 0xFF, or garbled?",
    ),
    beginner_traps=(
        "leaving CS as a plain GPIO or never pulling it low around the transfer (the slave never wakes)",
        "a CPOL/CPHA mode mismatch — the data is there but shifted, so it reads garbled",
        "reading all 0xFF means nothing is driving MISO — usually CS not asserted or the slave unpowered",
        "MSB-first vs LSB-first mismatch between master and slave",
        "clocking the bus faster than the slave's maximum SCLK",
    ),
    entry="cs_check",
    nodes=_SPI_NODES,
    evidence_vars={"cs_state": _CS_STATE, "sclk_state": _SCLK_STATE},
)


# ================================================================================================
# SEED PATTERN #4 — unexpected reset / brown-out (curated, deterministic). Encodes the five-whys:
# read the reset-cause flag first, then brown-out (inrush → decoupling) vs watchdog vs reset-pin.
# ================================================================================================

_RESET_CAUSE = EvidenceVar(
    name="reset_cause",
    values={
        "brownout": ("brown-out", "brownout", "bor flag", "bor reset", " bor ", "voltage drop",
                     "voltage sag", "voltage dip", "rail drops", "rail sags", "power drop",
                     "under load", "when wifi", "when the wifi", "when wi-fi", "when the motor",
                     "when the radio", "sd card write", "resets under load", "dips below", "sags"),
        "watchdog": ("watchdog", "iwdg", "wwdg", " wdt ", "wdt reset", "watchdog reset",
                     "during a long", "during flash", "flash write", "flash erase", "long loop",
                     "blocking", "busy-wait", "busy wait", "long operation"),
        "pin": ("reset pin", "nrst", "floating reset", "reset line", "noise on reset",
                "external reset", "pin reset", "reset is floating", "glitch on reset"),
    },
    labels={"brownout": "the reset cause is a brown-out (the supply rail sagged)",
            "watchdog": "the reset cause is the watchdog timer firing",
            "pin": "the reset is coming from the external reset pin"},
)

_CAP_TEST = EvidenceVar(
    name="cap_test",
    values={
        # Outcome words only — naming the apparatus ("a bench supply") is NOT a positive result;
        # "still resets even with a bench supply" must read as no_change, not helped.
        "helped": ("cap helped", "that helped", "cap fixed", "capacitor fixed", "fixed it",
                   "that fixed", "stops with", "goes away with", "no longer resets", "solved it",
                   "stopped when i added"),
        "no_change": ("still resets", "still resetting", "no change", "didn't help", "didnt help",
                      "even with", "same with a bench", "persists", "still happens",
                      "doesn't help", "no difference"),
    },
    labels={"helped": "adding bulk capacitance / a stiff supply stops the resets",
            "no_change": "the resets persist even with a stiff supply"},
)

_RESET_NODES = {
    "reset_cause_check": DecisionNode(
        id="reset_cause_check", zone="Reset cause",
        proof_step="After a reset, read the MCU's reset-cause / status flags (e.g. the reset flags in "
                   "the power or clock status register) BEFORE clearing them, and note which is set: "
                   "brown-out, watchdog, pin, or software.",
        why="Every reset feels identical from outside, but the silicon records WHY. The reset-cause "
            "flag splits the whole problem in one read — power (brown-out) vs a watchdog timeout vs an "
            "external pin vs your own software reset — so you debug the real cause instead of guessing.",
        expects="reset_cause",
        branches={"brownout": "power_sag", "watchdog": "watchdog_starve", "pin": "reset_pin"}),
    "power_sag": DecisionNode(
        id="power_sag", zone="Power delivery (inrush / decoupling)",
        rules_out="The flag says brown-out, so the rail is sagging — this is power delivery, not the "
                  "watchdog, the reset pin, or (yet) your firmware.",
        candidates=("a current spike (Wi-Fi TX, motor start, SD write) the supply can't deliver fast "
                    "enough",
                    "insufficient bulk/local decoupling near the hungry load",
                    "an undersized or dropping-out regulator",
                    "a weak source (a thin USB port or nearly-flat battery)"),
        proof_step="Scope the Vdd rail DURING the triggering event (Wi-Fi TX / motor / SD write) and "
                   "watch for a dip below the brown-out threshold. Then add bulk capacitance "
                   "(e.g. 100uF + 0.1uF) close to the load, or power from a stiff bench supply, and see "
                   "if the resets stop.",
        why="A brown-out under load is the rail dipping below the BOR threshold when an inrush spike "
            "hits a supply that can't keep up. Scoping the rail proves the sag; the cap / bench-supply "
            "swap proves whether it is delivery (decoupling/inrush) or something stiffer can't fix.",
        expects="cap_test",
        branches={"helped": "decoupling_confirmed", "no_change": "supply_or_short"}),
    "decoupling_confirmed": DecisionNode(
        id="decoupling_confirmed", zone="Decoupling / inrush",
        rules_out="A stiff supply or extra bulk capacitance stops it — so the root cause is delivery: "
                  "inrush vs decoupling, not the regulator's headroom or a short.",
        candidates=("too little bulk capacitance for the load's inrush",
                    "decoupling caps too far from the hungry chip",
                    "the RF/motor rail sharing a weak supply with the MCU"),
        proof_step="Size the bulk cap to the inrush, place 0.1uF decoupling right at the load's power "
                   "pins, and consider a separate or stiffer regulator for the RF/motor rail; keep the "
                   "high-current traces short.",
        why="Once a cap fixes it, the engineering is delivery: enough local energy storage, close "
            "enough, on a rail stiff enough for the peak draw."),
    "supply_or_short": DecisionNode(
        id="supply_or_short", zone="Regulator headroom / short / firmware",
        rules_out="The resets persist even with a stiff supply — so it is NOT just decoupling/inrush.",
        candidates=("the regulator can't supply the peak current (undersized or dropping out)",
                    "a partial short pulling the rail down",
                    "the brown-out threshold set higher than the rail's normal level",
                    "a firmware fault/assert that reboots and looks like a hardware reset"),
        proof_step="Measure the regulator's input vs output under load, compare its current rating to "
                   "the peak draw, and check for shorts; then confirm the firmware isn't faulting (read "
                   "the fault registers) before blaming hardware.",
        why="When a stiff supply doesn't help, the fault is upstream of decoupling — regulator headroom, "
            "a short, a mis-set threshold, or software masquerading as a reset."),
    "watchdog_starve": DecisionNode(
        id="watchdog_starve", zone="Watchdog timing",
        rules_out="The flag says watchdog, so this is a starved kick, not power and not the reset pin.",
        candidates=("a long blocking operation (flash erase/write is the classic one) outruns the "
                    "timeout",
                    "the watchdog isn't kicked inside that path",
                    "the timeout is set too short for the real work"),
        proof_step="Find the longest blocking section (flash write/erase, a busy-wait, a long ISR) and "
                   "either kick the watchdog inside it, lengthen the timeout to cover it, or move the "
                   "work off the blocking path. Temporarily disable the watchdog — if the resets stop, "
                   "it is confirmed.",
        why="A watchdog reset during long work is a feeding problem, not a hardware fault; the disable "
            "test confirms it and the fix is to feed it or shorten the work."),
    "reset_pin": DecisionNode(
        id="reset_pin", zone="Reset pin / external",
        rules_out="The flag says an external pin reset — so it is not brown-out and not the watchdog.",
        candidates=("NRST floating or noisy (missing pull-up / cap)",
                    "a reset supervisor misfiring",
                    "a debugger/programmer asserting reset",
                    "ESD or coupling onto the reset line"),
        proof_step="Confirm NRST has its pull-up and a small cap to ground, disconnect the debugger, "
                   "and scope NRST for glitches during the resets.",
        why="An external-pin reset is electrical noise or a misbehaving supervisor/debugger on NRST — "
            "scoping the pin separates a real assert from a flag set for another reason."),
}

POWER_RESET = ProblemPattern(
    name="power_reset",
    title="unexpected-reset / brown-out problem",
    match_groups=(
        ("reset", "resets", "resetting", "reboot", "reboots", "rebooting", "restart", "restarts",
         "restarting", "brown-out", "brownout", "watchdog", "boot loop", "bootloop", "power cycl",
         "keeps dying"),
        ("random", "randomly", "by itself", "on its own", "unexpected", "keeps", "whenever",
         "when wifi", "when the motor", "under load", "every time", "intermittent", "won't stay",
         "wont stay", "spontaneous", "for no reason", "all of a sudden", "keeps happening",
         "crashes and reboot", "keeps rebooting"),
    ),
    zones=(
        ("Reset cause", "read the reset-cause flag — brown-out vs watchdog vs pin vs software"),
        ("Power delivery", "inrush vs decoupling vs regulator headroom on the rail that sags"),
        ("Watchdog timing", "long blocking work (flash write) outrunning the watchdog kick"),
        ("Reset pin", "NRST pull-up/cap, a supervisor, the debugger, or noise"),
        ("Firmware fault", "a fault handler or assert that reboots can masquerade as a reset"),
    ),
    required_evidence=(
        "When does it reset — at boot, randomly, or under a specific load (Wi-Fi/motor/SD)?",
        "What does the reset-cause register say (brown-out / watchdog / pin / software)?",
        "How is the board powered (USB, regulator, battery) and what is the peak current draw?",
        "Is a watchdog enabled, and what is its timeout?",
        "Does a stiffer supply or extra bulk capacitance change it?",
    ),
    beginner_traps=(
        "blaming firmware when the reset-cause flag would say brown-out or watchdog in one read",
        "a current spike (Wi-Fi TX, motor start, SD write) sagging the rail below the brown-out level",
        "a long flash erase/write outrunning the watchdog timeout because the kick is starved",
        "NRST left floating with no pull-up/cap, catching noise",
        "powering a hungry board from a weak USB port or an undersized regulator",
    ),
    entry="reset_cause_check",
    nodes=_RESET_NODES,
    evidence_vars={"reset_cause": _RESET_CAUSE, "cap_test": _CAP_TEST},
)


# ================================================================================================
# SEED PATTERN #5 — HardFault / memory crash (curated, deterministic). Encodes the real order:
# decode the fault registers FIRST (CFSR/HFSR + stacked PC), then bus vs usage/unaligned vs stack.
# Boundary: it fires only on a fault WITH a concrete mechanism — a bare "my code crashed" or a
# symptom-only "crashed with HardFault_Handler" stays with the Purpose gate (ask for evidence).
# ================================================================================================

_FAULT_TYPE = EvidenceVar(
    name="fault_type",
    values={
        "bus": ("busfault", "bus fault", "bus error", "imprecise", "precise bus", "bfar",
                "bad address", "bad pointer", "null pointer", "nonexistent", "non-existent",
                "unclocked", "peripheral not clocked", "accessing memory that"),
        "usage": ("usagefault", "usage fault", "unaligned", "divide by zero", "div by zero",
                  "undefined instruction", "ufsr", "divbyzero", "bad function pointer", "thumb bit"),
        "stack": ("memmanage", "mem manage", "stack overflow", "stack grew", "mmfar",
                  "stack corrupt", "mpu violation", "stack into", "blew the stack", "stack smash"),
    },
    labels={"bus": "a bus fault — an access to memory/peripheral that didn't answer",
            "usage": "a usage fault — an illegal instruction (alignment, divide, undefined)",
            "stack": "a stack/memmanage fault — the stack itself is the problem"},
)

_STACK_EVIDENCE = EvidenceVar(
    name="stack_evidence",
    values={
        "overlap": ("linker", ".map", "map file", "stack overlaps", "stack region", "too small",
                    "grew into", "collides", "_estack", "ebss", "into the heap", "stack size"),
        "runaway": ("recursion", "recursive", "deep call", "big buffer", "large array",
                    "local array", "huge local", "stack allocated", "alloca", "infinite recursion",
                    "deep nesting"),
    },
    labels={"overlap": "the linker map shows the stack region colliding or undersized",
            "runaway": "an adequate stack is blown by recursion or a large local"},
)

_FAULT_NODES = {
    "fault_decode": DecisionNode(
        id="fault_decode", zone="Fault decode (CFSR/HFSR + stacked PC)",
        proof_step="In the fault handler, read SCB->CFSR and SCB->HFSR and recover the stacked PC and "
                   "LR from the exception frame (the SP at fault). The CFSR bits name the fault; the "
                   "stacked PC names the faulting instruction.",
        why="A HardFault is just the CPU saying 'something illegal happened.' The fault status "
            "registers say exactly WHAT (bus, usage/unaligned, or memmanage/stack) and the stacked PC "
            "says WHERE. Decoding them turns a blind crash into a named fault at a known instruction — "
            "the one step that ends the guessing.",
        expects="fault_type",
        branches={"bus": "bad_pointer", "usage": "usage_fault", "stack": "stack_check"}),
    "bad_pointer": DecisionNode(
        id="bad_pointer", zone="Bad pointer / bus access",
        rules_out="The CFSR says a bus fault — so this is an access to memory that isn't there, not an "
                  "alignment problem and not the stack.",
        candidates=("dereferencing a null or uninitialised pointer",
                    "touching a peripheral whose clock isn't enabled (the bus never answers)",
                    "an address outside valid RAM/flash/peripheral space",
                    "a pointer corrupted earlier and only faulting now"),
        proof_step="Look up the stacked PC in your .map / disassembly to find the exact access, then "
                   "check what it touches: is the pointer valid, and is that peripheral's clock enabled "
                   "before you touch it?",
        why="A bus fault is almost always a bad pointer or an unclocked peripheral; the stacked PC takes "
            "you to the exact instruction so you can check the address it used."),
    "usage_fault": DecisionNode(
        id="usage_fault", zone="Usage fault (alignment / divide / undefined)",
        rules_out="The CFSR says a usage fault — so it is the instruction itself, not a bad bus address "
                  "and not the stack.",
        candidates=("an unaligned word/halfword access (a packed struct or a cast pointer)",
                    "divide-by-zero with the trap enabled",
                    "an undefined instruction (a bad function pointer or a wrong Thumb bit)"),
        proof_step="From the stacked PC, inspect the faulting instruction: an unaligned LDR/STR points "
                   "to a misaligned pointer/packed field; a UDIV points to divide-by-zero; a garbage "
                   "opcode points to a corrupted or Thumb-bit-wrong function pointer.",
        why="A usage fault names the illegal operation; the stacked instruction tells you which of "
            "alignment / divide / bad-call it actually was."),
    "stack_check": DecisionNode(
        id="stack_check", zone="Stack (overlap vs runaway)",
        rules_out="The fault points at the stack — so it is not a stray bus access and not an alignment "
                  "fault; the stack itself is the problem.",
        candidates=("the stack grew into .bss/heap (regions too close, or the stack too small)",
                    "runaway recursion or a huge stack-allocated buffer blew an adequate stack"),
        proof_step="Open the linker map: compare the stack region against _ebss / heap end to see if "
                   "they collide or the stack is undersized; and check the faulting call path for deep "
                   "recursion or large local buffers.",
        why="Stack faults are either layout (the map shows overlap/too-small) or usage (a function eats "
            "more stack than budgeted) — the map and the call path separate the two.",
        expects="stack_evidence",
        branches={"overlap": "linker_overlap", "runaway": "runaway_stack"}),
    "linker_overlap": DecisionNode(
        id="linker_overlap", zone="Linker layout",
        rules_out="The map shows the stack colliding with .bss/heap (or simply too small) — so it is a "
                  "layout/budget problem, not a single runaway function.",
        candidates=("the stack size in the linker script is too small for the worst-case depth",
                    "stack and heap/.bss placed too close with no guard",
                    "no stack-overflow detection (MPU guard / fill pattern) to catch it early"),
        proof_step="Increase the stack region in the linker script to cover the worst-case depth, "
                   "separate it from heap/.bss, and add a guard (an MPU region or a fill-pattern check) "
                   "so the next overflow is caught at the boundary instead of as a random fault.",
        why="When the map shows the collision, the fix is budget and separation in the linker script "
            "plus a guard so it fails loudly next time."),
    "runaway_stack": DecisionNode(
        id="runaway_stack", zone="Stack usage (recursion / large locals)",
        rules_out="The stack region is adequately sized but a specific call path blows it — so it is "
                  "usage, not layout.",
        candidates=("unbounded or deep recursion in the faulting path",
                    "a large buffer allocated on the stack (a big local array)",
                    "deep nested calls with heavy frames on a small stack"),
        proof_step="Bound or remove the recursion (make it iterative or cap the depth), move large "
                   "buffers off the stack (static or heap), and re-measure peak stack use with a "
                   "fill-pattern high-water mark.",
        why="When the budget is fine but one path overflows, the fix is in the code path that eats the "
            "stack, not the linker."),
}

HARDFAULT = ProblemPattern(
    name="hardfault",
    title="HardFault / crash problem",
    match_groups=(
        ("hardfault", "hard fault", "usagefault", "usage fault", "busfault", "bus fault", "memmanage",
         "fault handler", "segfault", "crash", "crashes", "crashing", "stack overflow", "stack smash"),
        ("jumps to", "ends up in", "stuck in the fault", "lands in", "goes to the fault",
         "hangs in the fault", "fault handler every", "keeps crashing", "unaligned", "null pointer",
         "bad pointer", "dereferenc", "wild pointer", "dangling pointer", "out of bounds",
         "buffer overflow", "writes past", "divide by zero", "div by zero", "imprecise", "bus error",
         "use after free", "double free", "at 0x", "on boot every"),
    ),
    zones=(
        ("Fault decode", "read CFSR/HFSR + the stacked PC — name the fault and the instruction"),
        ("Bus access", "a bad pointer or an unclocked peripheral the bus can't answer"),
        ("Usage fault", "unaligned access, divide-by-zero, or an undefined instruction"),
        ("Stack", "overflow into .bss/heap, or a runaway call path"),
        ("Linker layout", "stack size and separation in the linker script, plus a guard"),
    ),
    required_evidence=(
        "What does CFSR/HFSR say, and what is the stacked PC (the faulting instruction)?",
        "Is it a bus, usage/unaligned, or memmanage/stack fault?",
        "Does it fault on boot, randomly, or in a specific function?",
        "What is the stack size in the linker script vs the worst-case depth?",
        "Any recursion or large stack-allocated buffers in the faulting path?",
    ),
    beginner_traps=(
        "guessing instead of reading CFSR/HFSR + the stacked PC, which name the fault and the line",
        "dereferencing a null/uninitialised pointer, or touching an unclocked peripheral (bus fault)",
        "an unaligned access through a packed struct or a cast pointer (usage fault)",
        "the stack growing into .bss/heap because the linker stack size is too small",
        "runaway recursion or a giant local array blowing an otherwise-adequate stack",
    ),
    entry="fault_decode",
    nodes=_FAULT_NODES,
    evidence_vars={"fault_type": _FAULT_TYPE, "stack_evidence": _STACK_EVIDENCE},
)


# ================================================================================================
# SEED PATTERN #6 — RTOS firmware (curated, deterministic). A NEW project type (goal_type=rtos),
# not a new topic. Order: scheduler/kernel-handlers first (does ANY task run?), then per-task
# (never-runs = starvation/priority vs runs-then-breaks = stack/ISR-safety).
# ================================================================================================

_SCHEDULER_STATE = EvidenceVar(
    name="scheduler_state",
    values={
        "not_running": ("nothing runs", "no task runs", "no tasks run", "scheduler doesn't start",
                        "scheduler won't start", "scheduler wont start", "vtaskstartscheduler returns",
                        "scheduler returned", "scheduler never starts", "no task executes",
                        "doesn't reach any task", "tick isn't firing", "no systick", "hangs at startup",
                        "stuck before the scheduler", "nothing schedules"),
        "running": ("other tasks run", "some tasks run", "the other tasks run", "only one task",
                    "blink task works", "rest of the tasks", "everything else runs",
                    "other threads run", "the others run"),
    },
    labels={"not_running": "the scheduler never schedules — no task runs at all",
            "running": "the scheduler runs and other tasks work — one task misbehaves"},
)

_TASK_SYMPTOM = EvidenceVar(
    name="task_symptom",
    values={
        "never_runs": ("never runs", "doesn't run", "isn't running", "starved", "blocked forever",
                       "stuck waiting", "never gets cpu", "never scheduled", "blocked on",
                       "waiting forever", "doesn't get time", "never executes"),
        "crashes": ("crashes", "hardfault", "stack overflow", "corrupts", "overflowed", "faults",
                    "overwrites", "memory corruption", "overflow hook", "smashes"),
    },
    labels={"never_runs": "the task never runs (starved or blocked)",
            "crashes": "the task runs and then crashes or corrupts memory"},
)

_RTOS_NODES = {
    "rtos_check": DecisionNode(
        id="rtos_check", zone="Scheduler vs task",
        proof_step="Split scheduler from task first: confirm vTaskStartScheduler() was called and "
                   "never returns (if it returns, the heap couldn't even create the idle/timer task). "
                   "Then toggle an LED in the LOWEST-priority task — does ANY task run at all?",
        why="An RTOS fault is either 'the scheduler/config never gets going' or 'the scheduler runs "
            "but one task misbehaves.' One check — does any task run — splits the whole problem, "
            "because a dead scheduler makes per-task debugging meaningless.",
        expects="scheduler_state",
        branches={"not_running": "sched_fault", "running": "task_issue"}),
    "sched_fault": DecisionNode(
        id="sched_fault", zone="Scheduler / kernel handlers",
        rules_out="Nothing runs, so this is the scheduler/config — not a single task's stack or "
                  "priority.",
        candidates=("the FreeRTOS kernel handlers (SVC, PendSV, SysTick) are not routed in the vector "
                    "table — the #1 bring-up trap",
                    "the heap is too small to create the idle/timer task, so vTaskStartScheduler() "
                    "returns",
                    "the SysTick / tick clock isn't configured, so there is no tick to schedule on",
                    "the tick rate is computed from the wrong core clock"),
        proof_step="Confirm SVC_Handler, PendSV_Handler and SysTick_Handler are mapped to FreeRTOS's "
                   "handlers (a missing PendSV/SVC route is the classic instant HardFault / no-switch), "
                   "that the tick interrupt actually fires, and that vTaskStartScheduler() does not "
                   "return (grow configTOTAL_HEAP_SIZE if it does).",
        why="A scheduler that never schedules is almost always the three kernel handlers not wired or "
            "no tick — both are vector-table / clock issues, not your task code."),
    "task_issue": DecisionNode(
        id="task_issue", zone="Per-task",
        rules_out="Some tasks run, so the scheduler, kernel handlers and tick are all fine — the fault "
                  "is one task.",
        candidates=("a task never runs (starved or blocked forever)",
                    "a task runs and then crashes or corrupts memory"),
        proof_step="Narrow it: does the problem task NEVER run (starved / blocked), or does it run and "
                   "then crash or corrupt?",
        why="With the scheduler proven alive, the two task-level failure modes — never-scheduled vs "
            "runs-then-breaks — need different fixes, so separate them first.",
        expects="task_symptom",
        branches={"never_runs": "starvation", "crashes": "task_stack"}),
    "starvation": DecisionNode(
        id="starvation", zone="Priority / blocking",
        rules_out="The scheduler runs and other tasks work, so it isn't the kernel — this task is being "
                  "starved or blocked.",
        candidates=("a higher-priority task never blocks (it busy-loops without vTaskDelay/queue/"
                    "semaphore) and starves everything below it",
                    "this task's priority is set wrong",
                    "it is blocked forever on a queue/semaphore/event that is never given",
                    "priority inversion — a low-priority task holds a mutex this one needs (use a "
                    "priority-inheritance mutex, not a plain binary semaphore)"),
        proof_step="Check that every higher-or-equal-priority task BLOCKS on something (a delay, queue "
                   "or semaphore) so the scheduler can run others; inspect run-time stats for which "
                   "task hogs the CPU, and confirm anything this task waits on is actually given.",
        why="A task that never runs is almost always a higher task that never yields, a never-satisfied "
            "wait, or priority inversion — all visible from the priorities and run-time stats."),
    "task_stack": DecisionNode(
        id="task_stack", zone="Task stack / ISR-safety",
        rules_out="The task runs but breaks, so it isn't starvation or the scheduler — it's that task's "
                  "memory or an ISR-safety violation.",
        candidates=("the task's stack is too small and overflows, corrupting its neighbour (enable "
                    "configCHECK_FOR_STACK_OVERFLOW and the overflow hook — it reports the task name)",
                    "a large buffer allocated on a small task stack",
                    "calling a non-FromISR API from an ISR (use the ...FromISR variants and yield)"),
        proof_step="Turn on stack-overflow checking (the hook fires with the offending task's name), "
                   "read uxTaskGetStackHighWaterMark() for that task, and verify every RTOS call made "
                   "from an ISR uses the FromISR variant.",
        why="A task that runs then crashes is usually its own stack overflowing or an ISR using a "
            "non-ISR-safe API — both are caught by the overflow hook and a high-water-mark check."),
}

RTOS_BRINGUP = ProblemPattern(
    name="rtos_bringup",
    title="RTOS task/scheduler problem",
    match_groups=(
        ("freertos", "rtos", "scheduler", "vtaskstartscheduler", "xtaskcreate", "vtaskdelay",
         "pendsv", "zephyr", "threadx", "ucos", " task ", "tasks", "semaphore", "mutex"),
        ("not running", "isn't running", "isnt running", "doesn't run", "doesnt run", "never runs",
         "won't run", "wont run", "hangs", "freezes", "stuck", "crashes", "hardfault",
         "stack overflow", "doesn't start", "doesnt start", "won't start", "deadlock",
         "priority inversion", "starved", "not switching", "not scheduling", "blocked forever",
         "nothing runs", "not responding", "no task"),
    ),
    zones=(
        ("Scheduler / kernel handlers", "SVC, PendSV, SysTick routed to the kernel; the tick fires; "
         "heap creates idle/timer"),
        ("Priorities / blocking", "every task blocks so others run; no busy-loop starves lower tasks"),
        ("Task stack", "each task's stack covers its worst case; overflow checking on"),
        ("ISR safety", "ISRs use only the FromISR API and yield correctly"),
        ("Sync primitives", "priority-inheritance mutex vs a plain semaphore; queues actually given"),
    ),
    required_evidence=(
        "Which RTOS (FreeRTOS/Zephyr/…) and which board?",
        "Does ANY task run, or nothing at all?",
        "Are SVC / PendSV / SysTick routed to the kernel handlers?",
        "What are the task priorities, and does each task block?",
        "configTOTAL_HEAP_SIZE and each task's stack size?",
    ),
    beginner_traps=(
        "not routing PendSV/SVC/SysTick to the kernel handlers — instant HardFault or no task switching "
        "(the #1 trap)",
        "a high-priority task that never blocks (no vTaskDelay/queue/semaphore) starving lower tasks",
        "a task stack too small — it overflows and corrupts its neighbour (enable the overflow hook)",
        "calling a non-FromISR API from an ISR",
        "a binary semaphore where a priority-inheritance mutex is needed → priority inversion",
    ),
    entry="rtos_check",
    nodes=_RTOS_NODES,
    evidence_vars={"scheduler_state": _SCHEDULER_STATE, "task_symptom": _TASK_SYMPTOM},
)


# The pattern registry. The engine above is already general over all of these.
PATTERNS: dict[str, ProblemPattern] = {
    UART_BRINGUP.name: UART_BRINGUP,
    I2C_BRINGUP.name: I2C_BRINGUP,
    SPI_BRINGUP.name: SPI_BRINGUP,
    POWER_RESET.name: POWER_RESET,
    HARDFAULT.name: HARDFAULT,
    RTOS_BRINGUP.name: RTOS_BRINGUP,
}
