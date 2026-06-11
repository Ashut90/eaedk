"""Mentor layer — a senior engineer next to a 0-experience beginner.

Part 1 (this file's deterministic core): a plain-language board capability map, a learning path
in deliberate order (filtered to what the board can do), and "what you need before you write
code" per step. All from seeded YAML — no jargon, no LLM, no hardcoding.

Parts 3/4 (LLM explanation + Actor-Critic) live in mentor_llm.py and reuse the post-filter.
"""
from __future__ import annotations

import json
import sqlite3

from . import repo


def capability_map(conn: sqlite3.Connection, board_name: str) -> list[dict]:
    return [{"capability": r["capability"], "summary": r["summary"]}
            for r in repo.board_capability_map(conn, board_name)]


def learning_path_for(conn: sqlite3.Connection, cap_names: set[str]) -> list[dict]:
    """Steps whose required capabilities the board actually has, in order."""
    out = []
    for s in repo.learning_steps(conn):
        if set(json.loads(s["requires_json"])) <= cap_names:
            out.append({"step": s["step"], "key": s["key"], "title": s["title"],
                        "goal_type": s["goal_type"], "why": s["why"],
                        "before_you_start": json.loads(s["before_you_start_json"])})
    return out


def render_board_mentor(conn: sqlite3.Connection, board_name: str) -> str | None:
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    caps = capability_map(conn, board_name)
    cap_names = {c["capability"] for c in caps}
    path = learning_path_for(conn, cap_names)

    L = [f"# Mentor — {board_name}  ({soc['name']}, {soc['arch']})", ""]
    L.append("## What this board can do")
    for c in caps:
        L.append(f"  • {c['summary'] or c['capability'] + ' — (no description yet)'}")
    if not caps:
        L.append("  (no capabilities recorded for this board)")
    L.append("")
    L.append("## Your learning path — do these in order")
    for s in path:
        L.append(f"  {s['step']}. {s['title']}")
        L.append(f"       why: {s['why']}")
        if s["before_you_start"]:
            L.append("       before you write code:")
            L.extend(f"         - {b}" for b in s["before_you_start"])
        L.append("")
    L.append(f"Ask me anything:  eaedk mentor --board \"{board_name}\" --ask \"what should I "
             f"build first\"")
    L.append(f"Explain a concept: eaedk mentor --board \"{board_name}\" --explain HardFault")
    L.append(f"Start step 1:      eaedk project init   (pick {board_name}, goal "
             f"'bare-metal application')")
    return "\n".join(L) + "\n"
