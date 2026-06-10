"""Interactive project setup (`eaedk project init`).

Guided flow: name -> pick an onboarded board -> pick a goal type (auto-selects the template)
-> assess immediately and surface FAIL / UNKNOWN / open risks before the engineer writes any
code. If a HIGH-severity rule is UNKNOWN/FAIL, an explicit blockers banner is shown (not a
hard stop). Writes through repo helpers only — no raw SQL.
"""
from __future__ import annotations

from typing import Callable

from . import repo
from .orchestrator import assess_project

Ask = Callable[[str], str]
Out = Callable[[str], None]

# Friendly labels for the goal types that have templates; "custom" is appended.
GOAL_LABELS = {
    "bare_metal_app": "Bare-metal application (blink / UART) — start here",
    "bootloader": "Bare-metal bootloader",
    "uboot": "U-Boot bring-up",
    "linux": "Linux bring-up",
    "ota": "Fail-safe OTA update",
    "driver": "Linux device driver",
}
_GOAL_ORDER = ["bare_metal_app", "bootloader", "uboot", "linux", "ota", "driver"]


def _select_board(ask: Ask, out: Out, boards) -> str | None:
    out("Select a board (onboarded):")
    for i, b in enumerate(boards, 1):
        out(f"  {i}) {b['name']:26s} {b['soc']:14s} {b['arch']}")
    names = {b["name"] for b in boards}
    while True:
        raw = ask(f"Board [1-{len(boards)} or name] (blank to abort): ").strip()
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(boards):
            return boards[int(raw) - 1]["name"]
        if raw in names:
            return raw
        out("  ! pick a number from the list or an exact board name")


def _select_goal(ask: Ask, out: Out, goal_types: list[str]) -> tuple[str, bool]:
    """Returns (goal_type, is_custom)."""
    ordered = [g for g in _GOAL_ORDER if g in goal_types]
    ordered += [g for g in goal_types if g not in ordered]
    out("Select a goal type:")
    for i, g in enumerate(ordered, 1):
        out(f"  {i}) {GOAL_LABELS.get(g, g)}  ({g})")
    custom_idx = len(ordered) + 1
    out(f"  {custom_idx}) Custom (no template)")
    while True:
        raw = ask(f"Goal [1-{custom_idx}]: ").strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(ordered):
                return ordered[n - 1], False
            if n == custom_idx:
                gt = ask("  Custom goal_type identifier: ").strip() or "custom"
                return gt, True
        out(f"  ! enter a number 1-{custom_idx}")


def _print_assessment(out: Out, name: str, board: str | None, goal: str,
                      template: str | None, resp) -> list[dict]:
    out("")
    out(f"Project '{name}' created  (board: {board or '-'}, goal: {goal}, "
        f"template: {template or 'none (custom)'})")
    out(f"Initial assessment — feasibility: {resp.feasibility.upper()}")

    fails = [v for v in resp.validations if v["status"] == "FAIL"]
    unknowns = [v for v in resp.validations if v["status"] == "UNKNOWN" and v["engaged"]]
    if fails:
        out("  FAIL:")
        for v in fails:
            out(f"    - {v['check']}: {v['reason']}")
    if unknowns:
        out("  UNKNOWN (engaged):")
        for v in unknowns:
            out(f"    - {v['check']}: {v['reason']}")
    if resp.risks:
        out("  Open risks:")
        for r in resp.risks:
            out(f"    - [{r['severity']}] {r['rule_key']}: {r['explanation']}")
    if not (fails or unknowns or resp.risks):
        out("  No FAILs, engaged UNKNOWNs, or risks. Clean start.")

    # Blockers: HIGH-severity rules that are FAIL or engaged-UNKNOWN.
    blockers = [v for v in resp.validations
                if v["severity_on_fail"] == "HIGH"
                and (v["status"] == "FAIL" or (v["status"] == "UNKNOWN" and v["engaged"]))]
    if blockers:
        out("")
        out("  ⚠  YOU HAVE BLOCKERS — resolve these HIGH-severity items before building:")
        for v in blockers:
            out(f"     - {v['check']} [{v['status']}]: {v['reason']}")
    return blockers


def run_project_init(conn, ask: Ask, out: Out) -> str | None:
    out("EAEDK project init — set up a bring-up project.\n")

    name = ask("Project name: ").strip()
    if not name:
        out("error: a project name is required; aborting.")
        return None
    if repo.get_project(conn, name) is not None:
        out(f"error: a project named {name!r} already exists; aborting.")
        return None

    boards = repo.list_boards(conn)
    if not boards:
        out("error: no boards onboarded. Run `eaedk board add --interactive` first.")
        return None
    board = _select_board(ask, out, boards)
    if board is None:
        out("aborted.")
        return None

    goal_types = [r["goal_type"] for r in conn.execute(
        "SELECT DISTINCT goal_type FROM templates WHERE active=1")]
    goal, is_custom = _select_goal(ask, out, goal_types)

    try:
        repo.create_project(conn, name, goal, board, allow_missing_template=is_custom)
    except ValueError as e:
        out(f"error: {e}")
        return None

    project = repo.get_project(conn, name)
    template = None
    if project["template_id"] is not None:
        row = conn.execute("SELECT name,version FROM templates WHERE id=?",
                           (project["template_id"],)).fetchone()
        template = f"{row['name']}@v{row['version']}"
    resp = assess_project(conn, project)
    _print_assessment(out, name, board, goal, template, resp)
    return name
