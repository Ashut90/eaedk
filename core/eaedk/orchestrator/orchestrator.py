"""Orchestrator — runs deterministic engines FIRST and assembles the cited response.

The LLM is never on this path (MVP). It refuses to declare "feasible" while any rule
FAILs, and surfaces engaged UNKNOWNs as Missing Information (spec §4.1, §7 Q3).
"""
from __future__ import annotations

import inspect
import re
import sqlite3
from typing import Any

from ..context import build_context, GOAL_DEFAULTS
from ..engines.risk.engine import evaluate_risks
from ..engines.toolchain.engine import toolchain_checks
from ..engines.validation.rules import run_validations, feasibility, UNKNOWN, RULES
from ..schemas.response import AssessResponse
from .. import repo


# --- Validation key transparency (v2.7 P4B) ------------------------------------------------------
# Every engineer-facing input key the engines actually read. Auto-discovered so it never drifts:
# each validation rule's declared user_inputs plus every snake_case string literal in its source
# (rules read several keys via helpers like _ints(ctx, "ddr_base", ...), not just ctx.get).
_LIT_RE = re.compile(r"""["']([a-z_][a-z0-9_]{2,})["']""")
# Structural / derived keys that are legitimate inputs but not owned by a single rule.
_STRUCTURAL_INPUTS = {"board", "storage_target", "projected_device_lifetime_seconds", "write_rate"}


def _validation_input_keys() -> set[str]:
    keys: set[str] = set()
    for r in RULES.values():
        keys |= set(r.user_inputs)
        try:
            keys |= set(_LIT_RE.findall(inspect.getsource(r.func)))
        except (OSError, TypeError):
            pass
    return keys


_VALIDATION_KEYS = _validation_input_keys()        # static: computed once from rule source


def _recognized_input_keys(conn: sqlite3.Connection) -> set[str]:
    """The full set of engineer inputs the engines recognise (validation rules + risk-rule idents +
    goal defaults + structural keys). Anything an engineer supplies outside this set is ignored, and
    P4B warns about it rather than silently dropping it."""
    keys = set(_VALIDATION_KEYS) | set(_STRUCTURAL_INPUTS)
    for rr in repo.load_risk_rules(conn):
        keys |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", rr.condition_dsl))
    for d in GOAL_DEFAULTS.values():
        keys |= set(d)
    return {k for k in keys if not k.startswith(("board.", "soc.", "_")) and k not in ("and", "or")}


def _assemble(conn: sqlite3.Connection, goal_type: str, ctx: dict[str, Any], results, risks,
              inputs: dict[str, Any], conf: dict[str, str],
              template: str | None, checklist_counts: dict[str, int],
              intent: dict[str, Any] | None = None) -> AssessResponse:
    feas = feasibility(results)

    # A board with no flash AND no RAM size is a board we know nothing about — "FEASIBLE" would
    # mislead a beginner into thinking it's ready. Surface the real state instead.
    has_board = "board.flash_bytes" in ctx
    no_geometry = has_board and ctx.get("board.flash_bytes") is None \
        and ctx.get("board.ram_bytes") is None
    if feas == "feasible" and no_geometry:
        feas = "no_geometry"

    # v3.1 Gap 5: behavioural intent feasibility is aggregated into the SAME verdict. A hard intent
    # FAIL (it cannot fit, or a required peripheral is absent) makes the whole project not feasible.
    if intent is not None and intent.get("verdict") == "FAIL":
        feas = "not_feasible"

    validations = [{"check": r.check, "status": r.status, "reason": r.reason,
                    "engaged": r.engaged, "severity_on_fail": r.severity_on_fail,
                    "gating": r.gating, "teach": r.teach}
                   for r in results]
    risk_dicts = [{"rule_key": f.rule_key, "severity": f.severity,
                   "explanation": f.explanation, "mitigation": f.mitigation}
                  for f in risks if f.fired or f.severity == "UNKNOWN"]

    facts, assumptions = [], []
    for k, v in inputs.items():
        c = conf.get(k, "MEDIUM")
        entry = {"key": k, "value": v, "confidence": c}
        (facts if c == "HIGH" else assumptions).append(entry)

    # P4B Case 2: an engaged UNKNOWN means the rule started but couldn't decide. Name the dependent
    # input keys it still needs, so "UNKNOWN" is never a dead end.
    provided = ctx.get("_provided", set())
    unknowns = []
    for r in results:
        if r.status == UNKNOWN and r.engaged:
            rule = RULES.get(r.check)
            missing = [k for k in (rule.user_inputs if rule else ()) if k not in provided]
            suffix = f" (needs: {', '.join(missing)})" if missing else ""
            unknowns.append(f"{r.check}: {r.reason}{suffix}")
    unknowns += [f"risk {f.rule_key}: {f.explanation}" for f in risks
                 if f.severity == "UNKNOWN"]

    # Missing required inputs that were never engaged also surface as info.
    not_started = [f"{r.check}: not started ({r.reason})" for r in results
                   if r.status == UNKNOWN and not r.engaged]
    unknowns += not_started

    if feas == "no_geometry":
        next_step = ("Complete the board's flash/RAM geometry — onboard it with real values, "
                     "run `eaedk ingest` on its datasheet, or use a seeded board — then validate.")
    elif feas == "not_feasible":
        first = next((r for r in results if r.status == "FAIL"), None)
        if first is not None:
            next_step = f"Resolve FAIL — {first.check}: {first.reason}"
        elif intent is not None and intent.get("reasons"):   # intent-driven failure (Gap 5)
            next_step = f"Resolve intent — {intent['reasons'][0]}"
        else:
            next_step = "Resolve the hard failure above."
    elif feas == "blocked":
        next_step = f"Provide missing info — {unknowns[0]}" if unknowns else "Resolve unknowns."
    elif unknowns:
        # Feasible, but optional checks remain — never call it "clean" while listing unknowns.
        next_step = ("Feasible — ready to export. The Missing Information items are optional "
                     "advanced checks; run `eaedk export` to generate your build files.")
    else:
        next_step = "Validation clean — ready to export. Run `eaedk export`."

    # P4B Case 1: an engineer input no rule reads is silently doing nothing. Warn, don't drop it.
    recognized = _recognized_input_keys(conn)
    input_warnings = [
        f"Input '{k}' is not recognised by any validation or risk rule for goal '{goal_type}' and "
        "was ignored — check the spelling, or whether it applies to this goal type."
        for k in sorted(inputs) if k not in recognized]

    return AssessResponse(
        goal_type=goal_type, feasibility=feas, template=template,
        checklist_counts=checklist_counts, validations=validations, risks=risk_dicts,
        facts=facts, assumptions=assumptions, unknowns=unknowns,
        input_warnings=input_warnings, intent=intent, next_step=next_step)


def _intent_result(conn: sqlite3.Connection, board_name: str | None,
                   intent: str | None, inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the behavioural-intent feasibility for a unified report (v3.1 Gap 5). Intent comes
    from the explicit ``--intent`` string or a stored project input named ``intent``."""
    spec = intent or inputs.get("intent")
    if not spec or not board_name:
        return None
    from .. import semantic_cost
    known, unknown = semantic_cost.classify_terms(spec)
    return semantic_cost.assess(conn, board_name, known, unknown)


def assess(conn: sqlite3.Connection, goal_type: str, inputs: dict[str, Any],
           board_name: str | None = None, only: list[str] | None = None,
           conf: dict[str, str] | None = None, intent: str | None = None) -> AssessResponse:
    """Stateless assessment from raw inputs (used by eval and ad-hoc checks)."""
    board, soc = repo.load_board(conn, board_name) if board_name else (None, None)
    ctx = build_context(inputs, board, soc, goal_type)
    results = run_validations(ctx, goal_type, only=only)
    risks = evaluate_risks(ctx, repo.load_risk_rules(conn), goal_type)
    intent_res = _intent_result(conn, board_name, intent, inputs)
    return _assemble(conn, goal_type, ctx, results, risks, inputs, conf or {}, None, {}, intent_res)


def assess_project(conn: sqlite3.Connection, project: "sqlite3.Row",
                   only: list[str] | None = None, intent: str | None = None) -> AssessResponse:
    """Assessment for a persisted project: loads inputs, template, checklist counts. v3.1 Gap 5:
    project structural validation and behavioural intent run as one block into a unified report."""
    inputs, conf = repo.load_inputs(conn, project["id"])
    board_name = repo.project_board_name(conn, project)
    board, soc = repo.load_board(conn, board_name) if board_name else (None, None)
    goal_type = project["goal_type"]

    ctx = build_context(inputs, board, soc, goal_type)
    results = run_validations(ctx, goal_type, only=only)

    # Toolchain checks: the build environment as a first-class validated entity. Reads stored
    # detection (never probes the host here); before any detection it surfaces as non-gating.
    tool = toolchain_checks(conn, board_name, soc, goal_type)
    if only is not None:
        tool = [t for t in tool if t.check in only]
    results += tool

    risks = evaluate_risks(ctx, repo.load_risk_rules(conn), goal_type)

    template = None
    counts: dict[str, int] = {}
    if project["template_id"] is not None:
        tpl = conn.execute("SELECT name,version FROM templates WHERE id=?",
                           (project["template_id"],)).fetchone()
        template = f"{tpl['name']}@v{tpl['version']}"
        for row in repo.checklist(conn, project["id"]):
            counts[row["status"]] = counts.get(row["status"], 0) + 1

    repo.replace_risks(conn, project["id"],
                       [{"rule_key": f.rule_key, "severity": f.severity,
                         "explanation": f.explanation, "mitigation": f.mitigation}
                        for f in risks if f.fired])
    intent_res = _intent_result(conn, board_name, intent, inputs)
    return _assemble(conn, goal_type, ctx, results, risks, inputs, conf, template, counts, intent_res)
