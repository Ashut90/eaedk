"""Actor-Critic-Arbiter for chat responses (v3.0 P4) — Validation Engine has final say.

Time-sliced single model: the LLM plays Actor (propose) then Critic (review its own answer), in
the same pipeline. Neither can certify truth. The ARBITER is a deterministic code path — the
post-filter, the feasibility gate, and the semantic cost table — and it has the last word. The LLM
cannot pass the arbiter by being convincing.

If any arbiter check fails (the project is not feasible, or a named intent cannot fit the board),
the Actor/Critic prose is DISCARDED and the deterministic override is returned with the math.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import repo, semantic_cost


@dataclass
class ArbiterResult:
    overridden: bool                       # True -> Actor output discarded, deterministic text wins
    text: str                              # the answer to show (override text, or the Actor's text)
    reason: str = ""                       # why the arbiter overrode (empty when it did not)
    checks: list[dict[str, Any]] = field(default_factory=list)   # per-check audit trail


_CRITIC_SYSTEM = (
    "You are the CRITIC reviewing your own previous answer as a strict senior firmware reviewer. "
    "Check it against these and rewrite it to be correct:\n"
    "- Does it contradict any stated validation result or feasibility verdict?\n"
    "- Does it assert a hardware fact (address, register, clock, memory size) not given in the "
    "context? If so, remove it.\n"
    "- Does it recommend something this board cannot support, or a stack that does not fit?\n"
    "- Does it use a cost estimate not in the provided COST DATA?\n"
    "Return the corrected answer only — no preamble, no meta-commentary.")


def critic_review(gw, system: str, actor_text: str, context: str) -> str:
    """The Critic pass: the model reviews and rewrites its own Actor answer. Best-effort — if the
    model is unavailable or errors, the Actor text passes through unchanged (the deterministic
    arbiter still runs and remains authoritative)."""
    try:
        prompt = (f"CONTEXT (the ground truth you must not contradict):\n{context}\n\n"
                  f"YOUR PREVIOUS ANSWER (the Actor draft to review):\n{actor_text}\n\n"
                  "Corrected answer:")
        out = gw.provider.generate(_CRITIC_SYSTEM, prompt)
        return out.strip() or actor_text
    except Exception:
        return actor_text


# ── Bounded "why" critic (Step 4) ───────────────────────────────────────────────────────────────
# For an UNCOVERED fault (one with no curated ProblemPattern — a matched pattern would already have
# returned a PROOF_PATH whose tree ends at a checkable step), the free-form LLM answer can jump to a
# software conclusion without ruling out the physical layers or ending at something the user can
# measure. This single bounded pass enforces the same discipline every proof-path already encodes:
# physical-first, end with a concrete measurement. Grounding is the curated layer order below (derived
# from the patterns' own zones) plus the board context — never the web. It is advisory: the post-
# filter, conceptual guards and the deterministic arbiter all still run AFTER it and keep the last say.

# Curated fault vocabulary — mirrors the structural fault signals but kept self-contained here so the
# why-critic does not depend on (or re-wire) the isolated structural_router.
_FAULT_SIGNALS: tuple[str, ...] = (
    "not working", "isn't working", "isnt working", "doesn't work", "doesnt work", "no output",
    "not printing", "no signal", "no data", "no response", "not responding", "nothing happens",
    "broken", "crash", "crashes", "crashing", "hang", "hangs", "freeze", "freezes", "frozen",
    "won't ", "wont ", "fails", "failing", "failed", "dead", "garbage", "garbled", "stuck",
    "reboots", "resetting", "keeps resetting", "disconnect", "drops packets", "not getting",
    "can't get", "cant get", "isn't receiving", "not receiving", "won't boot", "wont boot",
)


def _is_fault(user_text: str) -> bool:
    low = " " + (user_text or "").lower() + " "
    return any(s in low for s in _FAULT_SIGNALS)


_WHY_SYSTEM = (
    "You are the WHY-CRITIC reviewing a debugging answer for a hardware fault. Embedded faults are "
    "debugged physical-layer-first, never software-first. Before the answer concludes a software/code "
    "cause, it must rule out the layers beneath, in this order:\n"
    "  1. Electrical / power — rail stable, grounds tied, pull-ups / decoupling present\n"
    "  2. Clock — the peripheral and its pin/bus clock are actually enabled\n"
    "  3. Pinmux — the pin is on the right alternate function, not plain GPIO\n"
    "  4. Protocol / addressing — selection, address, mode, baud\n"
    "  5. Software — only after the above are addressed\n"
    "Rewrite the answer so it (a) does not jump to a software conclusion before the physical layers "
    "are addressed, and (b) ENDS with ONE concrete measurement the user can physically take right now "
    "(scope a pin, measure a rail, read a status register) to confirm the next cause. Do NOT invent "
    "specific pins, addresses, clock values, registers, or part numbers. Return the corrected answer "
    "only — no preamble, no meta-commentary.")


def why_review(gw, actor_text: str, context: str, user_text: str) -> str:
    """One bounded 'why' pass for an uncovered fault: push the answer physical-first and make it end
    at a checkable measurement. Fires ONLY on a fault report (concept/design answers pass straight
    through, no model call). Best-effort — offline / model error returns the answer unchanged, and the
    deterministic arbiter remains authoritative downstream."""
    if not _is_fault(user_text):
        return actor_text
    try:
        prompt = (f"CONTEXT (ground truth — do not contradict, do not invent specifics):\n{context}\n\n"
                  f"DRAFT ANSWER TO REVIEW:\n{actor_text}\n\nCorrected answer:")
        out = gw.provider.generate(_WHY_SYSTEM, prompt)
        return out.strip() or actor_text
    except Exception:
        return actor_text


# ── Relevance critic ──────────────────────────────────────────────────────────────────────────
# The one check that is NOT deterministic: "did it actually answer the question?". A bounded LLM
# self-review that rewrites an answer which dodged the question or recited a generic framework
# instead of addressing what was asked. Advisory — the deterministic fact/safety checks below still
# have the final say, and offline / model error returns the answer unchanged.

_RELEVANCE_SYSTEM = (
    "You are the RELEVANCE CRITIC. You are given a user's exact question and a draft answer. Decide "
    "whether the draft addresses the specific question that was asked.\n"
    "- If it does, return it EXACTLY unchanged.\n"
    "- A draft that teaches the trade-offs, asks the user a deciding question, or lays out options for "
    "an open 'should I / which' design question is a VALID answer — NOT a dodge. Do not force a blunt "
    "recommendation onto an open design question.\n"
    "- Only rewrite if the draft answered a DIFFERENT question, or is generic filler that ignores what "
    "was concretely asked (e.g. a folder-structure question answered with unrelated theory). Then "
    "rewrite it to answer the exact question, grounded; invent no pins/clocks/registers/addresses.\n"
    "CRITICAL: output ONLY the answer text itself. No preamble, no 'here is a rewritten answer', no "
    "commentary about the draft. Just the answer.")

# The small model sometimes ignores "no preamble" and prepends meta-commentary; strip a leading
# meta paragraph deterministically so it never reaches the user.
_META_MARK = ("rewritten answer", "corrected answer", "the draft answer", "here's a rewrit",
              "here is a rewrit", "here's the corrected", "here is the corrected", "i've rewritten",
              "i have rewritten", "revised answer", "dodges the question", "here's a revised",
              "here is a revised", "here's the revised")


def _strip_meta(text: str) -> str:
    parts = (text or "").split("\n\n", 1)
    if len(parts) == 2 and any(m in parts[0].lower() for m in _META_MARK):
        return parts[1].strip()
    return (text or "").strip()


def _is_open_decision(question: str) -> bool:
    """An open 'X vs Y / should I … or …' design question — where teaching the trade-off IS the right
    answer, so the relevance critic must not force a blunt recommendation."""
    q = " " + (question or "").lower().strip() + " "
    if " vs " in q or " versus " in q:
        return True
    if " or " in q and ("should i" in q or ("?" in (question or "") and len(q.split()) <= 12)):
        return True
    return False


def answer_check(gw, question: str, answer: str, context: str) -> str:
    """One bounded pass: does the answer address the SPECIFIC question? Skips open design decisions
    (Socratic teaching is valid there). Best-effort — offline / error returns the answer unchanged.
    Advisory; the deterministic arbiter still runs after it."""
    if not (question or "").strip() or len(answer or "") < 20 or _is_open_decision(question):
        return answer
    try:
        prompt = (f"USER QUESTION:\n{question}\n\nCONTEXT (ground truth — do not contradict or invent "
                  f"beyond it):\n{context}\n\nDRAFT ANSWER:\n{answer}\n\nReturn the answer (only the "
                  f"answer text):")
        out = _strip_meta(gw.provider.generate(_RELEVANCE_SYSTEM, prompt))
        return out or answer
    except Exception:
        return answer


def _feasibility_fail(conn: sqlite3.Connection, project: str | None) -> str | None:
    if not project:
        return None
    p = repo.get_project(conn, project)
    if p is None:
        return None
    from .orchestrator import assess_project
    resp = assess_project(conn, p)
    if resp.feasibility != "not_feasible":
        return None
    fails = [f"{v['check']}: {v['reason']}" for v in resp.validations
             if v.get("gating", True) and v["status"] == "FAIL"]
    return fails[0] if fails else "a hard validation failure"


def arbitrate(conn: sqlite3.Connection, board_name: str, project: str | None,
              user_text: str, actor_text: str) -> ArbiterResult:
    """The deterministic arbiter. Runs AFTER the Actor/Critic and overrides them on any hard fail."""
    checks: list[dict[str, Any]] = []

    # Check 1 — the project's feasibility verdict (the P1 gate from v2.7).
    feas = _feasibility_fail(conn, project)
    checks.append({"check": "feasibility", "failed": feas is not None, "detail": feas or "ok"})

    # Check 2 — named intents against the seeded semantic cost table.
    terms = semantic_cost.parse_intent(user_text)
    sem = semantic_cost.assess(conn, board_name, terms) if terms else None
    sem_failed = bool(sem and sem["verdict"] == "FAIL")
    checks.append({"check": "semantic_cost", "failed": sem_failed,
                   "detail": (", ".join(r["term"] for r in sem["terms"]) if sem else "none")})

    if feas is None and not sem_failed:
        return ArbiterResult(overridden=False, text=actor_text, reason="", checks=checks)

    # An arbiter check failed → discard the Actor/Critic prose, state the proven failure.
    L = ["⚖️ Arbiter override — the deterministic Validation Engine has the final say, and it "
         "discarded the proposed answer. Here is what is actually true:"]
    reason_bits = []
    if feas:
        L.append(f"  • This project is NOT feasible: {feas}")
        reason_bits.append("feasibility")
    if sem_failed:
        L.append("  • " + semantic_cost.chat_note(conn, board_name, user_text))
        reason_bits.append("semantic_cost")
    L.append("No optimisation makes a physical limit go away — fix the numbers or move to a larger "
             "board before anything else.")
    return ArbiterResult(overridden=True, text="\n".join(L),
                         reason="+".join(reason_bits), checks=checks)
