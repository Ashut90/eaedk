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

_CRITIC_SYSTEM = (
    "You review a beginner's bare-metal firmware scaffold for common mistakes ONLY: missing "
    "clock enable before using a peripheral, wrong peripheral init order, a stack too small, or "
    "a buffer larger than the available RAM. Return ONLY JSON: "
    '{"issues":[{"kind":"...","message":"...","check":{"stack":<bytes>,"heap":<bytes>,'
    '"static":<bytes>}}]}. Include "check" only for a memory-size concern, with the sizes you '
    "suspect. NEVER invent register addresses or clock values.")

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


def _extract_json(raw: str) -> dict:
    s = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a:b + 1]) if a != -1 and b != -1 else {}
    except json.JSONDecodeError:
        return {}


def _critic(gw: Gateway, scaffold: str, board_name: str, soc: dict, facts: dict) -> list[dict]:
    prompt = (f"BOARD: {board_name} ({soc['arch']})\n"
              f"VERIFIED FACTS: {json.dumps(facts)}\n"
              f"SCAFFOLD:\n{scaffold[:2500]}\n\nReturn the JSON now:")
    data = _extract_json(gw.provider.generate(_CRITIC_SYSTEM, prompt))
    return [i for i in data.get("issues", []) if isinstance(i, dict)]


def _actor(gw: Gateway, confirmed: list[dict], board_name: str) -> str:
    prompt = (f"BOARD: {board_name}\nCONFIRMED ISSUES:\n"
              + "\n".join(f"- {c['kind']}: {c['message']} ({c.get('verified','')})"
                          for c in confirmed)
              + "\n\nExplain the fixes:")
    return gw.provider.generate(_ACTOR_SYSTEM, prompt).strip()


def run_actor_critic(conn: sqlite3.Connection, project: sqlite3.Row,
                     gateway: Gateway | None = None, max_epochs: int = 2) -> ActorCriticResult:
    data = export.gather(conn, project)
    scaffold = codegen.render_main_c(data)
    board_name = data["board_name"]
    board, soc = data["board"], data["soc"]
    gw = gateway or Gateway()
    if board is None or not gw.available():
        return ActorCriticResult(scaffold=scaffold, available=False,
                                 reason=("LLM unavailable" if board else "no board"))

    facts = data.get("facts", {})
    confirmed: list[dict] = []
    advisory: list[dict] = []
    fixes: list[dict] = []
    epoch = 0
    review_target = scaffold
    for epoch in range(1, max_epochs + 1):
        issues = _critic(gw, review_target, board_name, soc, facts)
        confirmed, advisory = arbitrate(issues, board, soc)
        if not confirmed:
            break
        fix = _actor(gw, confirmed, board_name)
        fixes.append({"epoch": epoch, "fix": fix})
        review_target = scaffold + "\n/* engineer applied: " + fix[:200] + " */"
    return ActorCriticResult(scaffold=scaffold, available=True, epochs=epoch,
                             confirmed=confirmed, advisory=advisory, fixes=fixes)
