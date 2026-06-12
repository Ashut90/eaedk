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


def dropped_steps_for(conn: sqlite3.Connection, cap_names: set[str]) -> list[dict]:
    """Steps filtered OUT of the path, with the capabilities they're missing (Fix 6).

    A beginner must learn *why* a step is hidden and how to unlock it — never a silent drop.
    """
    out = []
    for s in repo.learning_steps(conn):
        required = set(json.loads(s["requires_json"]))
        missing = required - cap_names
        if missing:
            out.append({"step": s["step"], "title": s["title"],
                        "missing": sorted(missing)})
    return out


def family_of(soc_name: str | None) -> str | None:
    """Map a board's SoC name to a first-mistakes chip family. Pure; None if unrecognised."""
    s = (soc_name or "").upper()
    if s.startswith("STM32"):
        return "stm32"
    if s == "RP2040":
        return "rp2040"
    if s.startswith("ESP32"):
        return "esp32"
    if s.startswith("ATMEGA") or s.startswith("ATTINY"):
        return "avr"
    return None


def render_common_mistakes(conn: sqlite3.Connection, board_name: str) -> str | None:
    """B3 (v1.8.0): the mistakes a beginner makes first on this board's chip family."""
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    fam = family_of(soc["name"])
    rows = repo.first_mistakes_for_family(conn, fam) if fam else []
    L = [f"# Common first mistakes — {board_name}  ({soc['name']})", ""]
    if not rows:
        L.append("No common-mistakes list is seeded for this chip family yet.")
        L.append("General rule: enable a peripheral's clock before using it, and match baud "
                 "rates on both ends of a UART.")
        return "\n".join(L) + "\n"
    for r in rows:
        L.append(f"• [{r['severity']}] {r['mistake']}")
        L.append(f"      fix: {r['fix']}")
        L.append("")
    L.append("Board boots but nothing prints? See `eaedk mentor --board "
             f"\"{board_name}\" --explain UART-debug`.")
    return "\n".join(L) + "\n"


def render_next_step(conn: sqlite3.Connection, board_name: str,
                     completed_key: str | None = None) -> str | None:
    """B4 (v1.8.0): hand-hold to the next project — what it introduces, the new concept, and
    what to set up first. ``completed_key`` = the step just finished (None -> the first step)."""
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    path = learning_path_for(conn, repo.board_capability_names(conn, board_name))
    if not path:
        return (f"# What's next — {board_name}\n\n"
                "No learning steps are unlocked for this board yet. Run "
                f"`eaedk mentor --board \"{board_name}\"` to see why.\n")
    nxt = path[0]
    if completed_key:
        keys = [s["key"] for s in path]
        if completed_key in keys:
            i = keys.index(completed_key)
            if i + 1 < len(path):
                nxt = path[i + 1]
            else:
                return (f"# What's next — {board_name}\n\n🎉 You've finished the unlocked "
                        f"learning path (last: {path[-1]['title']}). Add capabilities or a new "
                        "board to unlock more, or start your own project.\n")

    intro = repo.learning_step_intro(conn, nxt["key"])
    L = [f"# What's next — {board_name}", "",
         f"## Next project: {nxt['title']}  (step {nxt['step']})", ""]
    if intro:
        L.append(f"**What it introduces:** {intro['introduces']}")
        if intro["concept"]:
            anchor = repo.get_concept(conn, intro["concept"])
            if anchor:
                L.append(f"**New concept — {anchor['name']}:** {anchor['anchor'].splitlines()[0]}")
                L.append(f"  (full explanation: `eaedk mentor --board \"{board_name}\" "
                         f"--explain {anchor['name']}`)")
    L.append(f"**Why now:** {nxt['why']}")
    if nxt["before_you_start"]:
        L.append("")
        L.append("**Set this up before you start:**")
        L.extend(f"  - {b}" for b in nxt["before_you_start"])
    L.append("")
    L.append(f"Ready? Run `eaedk project init` (pick {board_name}, goal '{nxt['goal_type']}').")
    L.append(f"Finished it? Run `eaedk mentor --board \"{board_name}\" --next {nxt['key']}` "
             "for the one after.")
    return "\n".join(L) + "\n"


def render_board_mentor(conn: sqlite3.Connection, board_name: str) -> str | None:
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    caps = capability_map(conn, board_name)
    cap_names = {c["capability"] for c in caps}
    path = learning_path_for(conn, cap_names)
    dropped = dropped_steps_for(conn, cap_names)

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
    if not path:
        L.append("  (no steps unlocked yet — see below)")
        L.append("")
    if dropped:
        L.append("## Steps not shown yet — and how to unlock them")
        for s in dropped:
            caps_list = ", ".join(s["missing"])
            cmd = f"eaedk board capability add \"{board_name}\" {s['missing'][0].upper()}"
            L.append(f"  {s['step']}. {s['title']} — needs {caps_list} capability. "
                     f"Add it with `{cmd}`")
        L.append("")
    # B1 (v1.8.0): the two failures every beginner hits right after flashing — surface the
    # guided flows so they're never left staring at a dead board.
    L.append("## Stuck after flashing?")
    L.append(f"  Board boots but nothing on serial:  eaedk mentor --board \"{board_name}\" "
             f"--explain UART-debug")
    L.append(f"  No output at all / common pitfalls:  eaedk mentor --board \"{board_name}\" "
             f"--common-mistakes")
    L.append("")
    L.append(f"Ask me anything:  eaedk mentor --board \"{board_name}\" --ask \"what should I "
             f"build first\"")
    L.append(f"Explain a concept: eaedk mentor --board \"{board_name}\" --explain HardFault")
    L.append(f"What's next:       eaedk mentor --board \"{board_name}\" --next")
    L.append("")
    # v1.9.1: a first-time CLI user doesn't know the project name carries across commands, or
    # that export is the immediate next step. Spell out the exact sequence, with prompt hints.
    L.append("Start step 1 — run these in order:")
    L.append("")
    L.append("  eaedk project init")
    L.append("  # at \"Project name:\"  → type a short name, e.g. blink")
    L.append("  #                       (you will use this same name in every command after this)")
    L.append(f"  # at \"Select a board\" → type the number next to {board_name}")
    L.append("  # at \"Goal [1-9]:\"    → press Enter  (picks \"bare-metal application — start here\")")
    L.append("")
    L.append("  eaedk export blink --out ~/blink-fw")
    L.append("  # replace \"blink\" with whatever name you typed above")
    L.append("")
    L.append("  cat ~/blink-fw/START_HERE.md")
    L.append("  # this tells you how to build and flash")
    return "\n".join(L) + "\n"
