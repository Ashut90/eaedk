"""Orchestrator — runs deterministic engines FIRST and assembles the cited response.

The LLM is never on this path (MVP). It refuses to declare "feasible" while any rule
FAILs, and surfaces engaged UNKNOWNs as Missing Information (spec §4.1, §7 Q3).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..context import build_context
from ..engines.risk.engine import evaluate_risks
from ..engines.toolchain.engine import toolchain_checks
from ..engines.validation.rules import run_validations, feasibility, UNKNOWN
from ..schemas.response import AssessResponse
from .. import repo


def _assemble(goal_type: str, ctx: dict[str, Any], results, risks,
              inputs: dict[str, Any], conf: dict[str, str],
              template: str | None, checklist_counts: dict[str, int]) -> AssessResponse:
    feas = feasibility(results)

    # A board with no flash AND no RAM size is a board we know nothing about — "FEASIBLE" would
    # mislead a beginner into thinking it's ready. Surface the real state instead.
    has_board = "board.flash_bytes" in ctx
    no_geometry = has_board and ctx.get("board.flash_bytes") is None \
        and ctx.get("board.ram_bytes") is None
    if feas == "feasible" and no_geometry:
        feas = "no_geometry"

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

    unknowns = [f"{r.check}: {r.reason}" for r in results
                if r.status == UNKNOWN and r.engaged]
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
        first = next(r for r in results if r.status == "FAIL")
        next_step = f"Resolve FAIL — {first.check}: {first.reason}"
    elif feas == "blocked":
        next_step = f"Provide missing info — {unknowns[0]}" if unknowns else "Resolve unknowns."
    elif unknowns:
        # Feasible, but optional checks remain — never call it "clean" while listing unknowns.
        next_step = ("Feasible — ready to export. The Missing Information items are optional "
                     "advanced checks; run `eaedk export` to generate your build files.")
    else:
        next_step = "Validation clean — ready to export. Run `eaedk export`."

    return AssessResponse(
        goal_type=goal_type, feasibility=feas, template=template,
        checklist_counts=checklist_counts, validations=validations, risks=risk_dicts,
        facts=facts, assumptions=assumptions, unknowns=unknowns, next_step=next_step)


def assess(conn: sqlite3.Connection, goal_type: str, inputs: dict[str, Any],
           board_name: str | None = None, only: list[str] | None = None,
           conf: dict[str, str] | None = None) -> AssessResponse:
    """Stateless assessment from raw inputs (used by eval and ad-hoc checks)."""
    board, soc = repo.load_board(conn, board_name) if board_name else (None, None)
    ctx = build_context(inputs, board, soc, goal_type)
    results = run_validations(ctx, goal_type, only=only)
    risks = evaluate_risks(ctx, repo.load_risk_rules(conn), goal_type)
    return _assemble(goal_type, ctx, results, risks, inputs, conf or {}, None, {})


def assess_project(conn: sqlite3.Connection, project: "sqlite3.Row",
                   only: list[str] | None = None) -> AssessResponse:
    """Assessment for a persisted project: loads inputs, template, checklist counts."""
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
    return _assemble(goal_type, ctx, results, risks, inputs, conf, template, counts)
