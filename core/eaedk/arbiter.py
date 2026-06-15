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
