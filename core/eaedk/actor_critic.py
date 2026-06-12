"""Actor-Critic loop (Part 4) — harden a beginner scaffold, with the Validation Engine as the
deterministic arbiter.

- Actor (LLM): revises/explains the scaffold to fix CONFIRMED issues.
- Critic (LLM): reviews for beginner mistakes (missing clock enable, wrong init order, stack too
  small, buffer larger than RAM) and returns structured issues. It cannot invent hardware facts.
- Arbiter (deterministic): a Critic claim about memory is re-checked with the real RAM_BUDGET
  rule against the board's verified RAM before it's shown as CONFIRMED. The agents propose; the
  engine decides.

Time-sliced on one Ollama model: the same model is called with the Critic prompt, then the Actor
prompt (swapped system prompt). Max 2 epochs. Graceful when offline.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import repo
from .context import build_context
from .engines.validation.rules import RULES
from .engines.output import export, codegen
from .llm.gateway import Gateway
from .llm.postfilter import build_board_allowlist, filter_text

# Note: keep the words "review" and "JSON" in the Critic prompt and out of the Actor prompt —
# the fake provider in the test suite switches Critic vs Actor on exactly those tokens.
_CRITIC_SYSTEM = (
    "You review a firmware artifact for common mistakes, critiquing ONLY against the VERIFIED "
    "PROJECT INPUTS and BOARD GEOMETRY given to you — never against values you imagine. Look "
    "for: missing clock enable before using a peripheral; wrong peripheral init order; a stack "
    "too small OR larger than the available RAM; a buffer larger than RAM; overlapping A/B "
    "partition slots; a missing or placeholder device-tree 'compatible' string or missing "
    "interrupt-parent; and an implausible UART baud-rate divisor for the stated clock. Return "
    'ONLY JSON: {"issues":[{"kind":"...","message":"...","check":{"stack":<bytes>,"heap":<bytes>,'
    '"static":<bytes>}}]}. Include "check" only for a memory-size concern, with the sizes you '
    "suspect. NEVER invent a register address, clock, or timing value.")

_ACTOR_SYSTEM = (
    "You revise a beginner's firmware to fix the CONFIRMED issues listed. Explain each fix in "
    "one or two plain sentences. NEVER invent an address, register, clock, or timing value.")


@dataclass
class ActorCriticResult:
    scaffold: str
    available: bool = True
    epochs: int = 0
    confirmed: list[dict] = field(default_factory=list)
    advisory: list[dict] = field(default_factory=list)
    fixes: list[dict] = field(default_factory=list)
    reason: str = ""
    artifact_kind: str = "bare_metal_c"      # F3: what was actually reviewed


def arbitrate(issues: list[dict], board: dict, soc: dict) -> tuple[list[dict], list[dict]]:
    """Re-check each Critic claim deterministically. Memory claims go through the real
    RAM_BUDGET rule against the board's verified RAM; only FAILs are CONFIRMED."""
    confirmed, advisory = [], []
    for iss in issues:
        chk = iss.get("check") or {}
        sizes = {k: chk.get(k) for k in ("stack", "heap", "static")
                 if isinstance(chk.get(k), int)}
        base = {"kind": iss.get("kind"), "message": iss.get("message")}
        if sizes:
            ctx = build_context(
                {"stack_size": chk.get("stack", 0), "heap_size": chk.get("heap", 0),
                 "static_size": chk.get("static", 0)}, board, soc, "bare_metal_app")
            r = RULES["RAM_BUDGET"].func(ctx)
            if r.status == "FAIL":
                confirmed.append({**base, "verified": r.reason})
            else:
                advisory.append({**base, "note": f"not confirmed by RAM_BUDGET: {r.reason}"})
        else:
            advisory.append(base)        # structural reasoning — shown, not deterministically proven
    return confirmed, advisory


def grounded_confirmations(conn: sqlite3.Connection, project: sqlite3.Row) -> list[dict]:
    """F1/F2: deterministic CONFIRMED issues from the real Validation Engine over the project's
    actual inputs — the faults the engineer truly has (oversized stack, partition overlap, ...),
    independent of anything the Critic LLM says. A FAIL or an engaged gating UNKNOWN qualifies."""
    from .orchestrator import assess_project        # lazy: avoid any import cycle
    resp = assess_project(conn, project)
    out: list[dict] = []
    for v in resp.validations:
        gating = v.get("gating", True)
        is_fail = v["status"] == "FAIL"
        is_engaged_unknown = v["status"] == "UNKNOWN" and v.get("engaged")
        if gating and (is_fail or is_engaged_unknown):
            out.append({"kind": v["check"], "message": v["reason"],
                        "verified": v["reason"], "teach": v.get("teach", "")})
    return out


def _grounding_block(inputs: dict, board: dict | None) -> str:
    """Render the engineer's real inputs + board geometry so the Critic critiques against them."""
    geo = {}
    if board:
        for k in ("flash_base", "flash_bytes", "ram_base", "ram_bytes"):
            if isinstance(board.get(k), int):
                geo[k] = board[k]
    return (f"VERIFIED PROJECT INPUTS (critique against THESE, not imagined values): "
            f"{json.dumps(inputs, default=str)}\n"
            f"BOARD GEOMETRY: {json.dumps(geo)}\n")


def _extract_json(raw: str) -> dict:
    s = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a:b + 1]) if a != -1 and b != -1 else {}
    except json.JSONDecodeError:
        return {}


def _critic(gw: Gateway, scaffold: str, board_name: str, soc: dict, facts: dict,
            grounding: str = "", artifact_kind: str = "bare_metal_c") -> list[dict]:
    prompt = (f"BOARD: {board_name} ({soc['arch']})\n"
              f"ARTIFACT UNDER REVIEW: {artifact_kind}\n"
              f"{grounding}"
              f"VERIFIED BOARD FACTS: {json.dumps(facts)}\n"
              f"ARTIFACT:\n{scaffold[:2500]}\n\nReturn the JSON now:")
    data = _extract_json(gw.provider.generate(_CRITIC_SYSTEM, prompt))
    return [i for i in data.get("issues", []) if isinstance(i, dict)]


def _actor(gw: Gateway, confirmed: list[dict], board_name: str,
           allow=None) -> str:
    prompt = (f"BOARD: {board_name}\nCONFIRMED ISSUES:\n"
              + "\n".join(f"- {c['kind']}: {c['message']} ({c.get('verified','')})"
                          for c in confirmed)
              + "\n\nExplain the fixes:")
    raw = gw.provider.generate(_ACTOR_SYSTEM, prompt).strip()
    # F5: the Actor's text is governed by the same post-filter as every other LLM output —
    # any uncited address/clock is stripped before it can reach the engineer.
    if allow is not None:
        raw, _removed = filter_text(raw, allow)
    return raw


def run_actor_critic(conn: sqlite3.Connection, project: sqlite3.Row,
                     gateway: Gateway | None = None, max_epochs: int = 2,
                     code: str | None = None) -> ActorCriticResult:
    data = export.gather(conn, project)
    inputs, _conf = repo.load_inputs(conn, project["id"])        # F1/F2: the engineer's real inputs
    data["inputs"] = inputs                                      # F3: artifacts may read them
    artifact_kind, gen_scaffold = codegen.render_review_artifact(data)   # F3: goal-aware artifact
    # v2.1.0: Code Studio passes the user's edited code to review; default reviews the scaffold.
    scaffold = code if code is not None else gen_scaffold
    board_name = data["board_name"]
    board, soc = data["board"], data["soc"]
    gw = gateway or Gateway()
    if board is None or not gw.available():
        return ActorCriticResult(scaffold=scaffold, available=False, artifact_kind=artifact_kind,
                                 reason=("LLM unavailable" if board else "no board"))

    facts = data.get("facts", {})
    grounding = _grounding_block(inputs, board)                  # F1: critique against real inputs
    grounded = grounded_confirmations(conn, project)             # F2: deterministic real faults
    allow = build_board_allowlist(conn, board_name)             # F5: governs the Actor's text
    confirmed: list[dict] = list(grounded)
    advisory: list[dict] = []
    fixes: list[dict] = []
    epoch = 0
    review_target = scaffold
    for epoch in range(1, max_epochs + 1):
        issues = _critic(gw, review_target, board_name, soc, facts, grounding, artifact_kind)
        c_conf, advisory = arbitrate(issues, board, soc)
        confirmed = grounded + c_conf                           # deterministic faults always first
        if not confirmed:
            break
        fix = _actor(gw, confirmed, board_name, allow)
        fixes.append({"epoch": epoch, "fix": fix})
        review_target = scaffold + "\n/* engineer applied: " + fix[:200] + " */"
        if not c_conf:                 # only deterministic faults remain — one fix pass is enough
            break
    return ActorCriticResult(scaffold=scaffold, available=True, epochs=epoch,
                             confirmed=confirmed, advisory=advisory, fixes=fixes,
                             artifact_kind=artifact_kind)
