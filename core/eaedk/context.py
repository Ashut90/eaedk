"""Resolve a flat evaluation context for the deterministic engines.

A *context* is a dict the validation and risk engines read. It merges:
  - engineer-provided inputs (project_inputs, or eval-case inputs),
  - board-derived values flattened under ``board.*`` and ``soc.*``,
  - goal-type defaults that encode "not confirmed" safety positions.

Values may arrive as ints, hex/dec strings, dicts (regions), or lists (partitions).
``as_int`` coerces scalars; structured values are passed through untouched.
"""
from __future__ import annotations

from typing import Any

# Inputs that the system injects to mean "not confirmed" for a goal type. These are
# deliberately falsy so the corresponding risk rule fires and the validation rule reports
# UNKNOWN rather than silently passing (spec §2, §7 Q3).
GOAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "uboot": {"ddr_timing_verified": 0},
    "bootloader": {"watchdog_enabled": 0},
}


def as_int(value: Any) -> int | None:
    """Coerce an int / hex-or-dec string to int. Returns None if not coercible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s, 0)  # 0 → auto base (handles 0x.., 0o.., decimal)
        except ValueError:
            return None
    return None


def flatten_board(board: dict[str, Any] | None, soc: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if board:
        for k, v in board.items():
            out[f"board.{k}"] = v
    if soc:
        for k, v in soc.items():
            out[f"soc.{k}"] = v
    return out


def build_context(
    inputs: dict[str, Any],
    board: dict[str, Any] | None,
    soc: dict[str, Any] | None,
    goal_type: str,
) -> dict[str, Any]:
    """Compose the evaluation context.

    Precedence: explicit engineer inputs override goal defaults override board-derived
    values. ``_provided`` records which keys the engineer (or injected defaults) supplied,
    so the engines can tell "not started" from "started but unknown" (spec §2.3 feasibility).
    """
    ctx: dict[str, Any] = {}
    ctx.update(flatten_board(board, soc))
    provided: set[str] = set()

    for k, v in GOAL_DEFAULTS.get(goal_type, {}).items():
        if k not in inputs:
            ctx[k] = v
            provided.add(k)

    for k, v in inputs.items():
        ctx[k] = v
        provided.add(k)

    ctx["_provided"] = provided
    ctx["_goal"] = goal_type
    return ctx
