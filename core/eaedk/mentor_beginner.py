"""Beginner mentor layer (v3.0 P1) — the original vision, fully grounded.

A zero-experience engineer plugs in a board and EAEDK answers, from the database alone:
  - what the board can do and what it cannot validate  (render_board_capabilities, 1A)
  - what to build first, and the next projects in dependency order  (render_recommendation, 1B)
  - a job-focused roadmap, sequenced as a dependency graph across boards  (render_roadmap, 1C)

Every hardware statement is a seeded fact or is explicitly flagged UNKNOWN. No LLM is on this
path; nothing here asserts a value the database has not verified.
"""
from __future__ import annotations

import math
import sqlite3

from . import repo, mentor


# --- plain-language helpers (deterministic, from facts) ------------------------------------------

def _fmt_size(n: int | None) -> str:
    if n is None:
        return "UNKNOWN"
    if n >= 1024 * 1024 and n % (1024 * 1024) == 0:
        return f"{n // (1024 * 1024)}MB"
    if n >= 1024:
        return f"{n // 1024}KB"
    return f"{n}B"


def arch_family_meaning(arch: str | None) -> tuple[str, str]:
    """(short label, what it means for a beginner) — derived from the SoC's architecture string."""
    a = (arch or "").lower()
    if "cortex-m0" in a:
        return ("Cortex-M0/M0+", "an integer-only core — no hardware floating point (float math is "
                "emulated in software and slow), a simple interrupt model, and little to "
                "misconfigure. An excellent place to learn.")
    if "cortex-m3" in a:
        return ("Cortex-M3", "integer-only (no FPU), but with the full NVIC interrupt controller and "
                "the classic ARM bare-metal model. The canonical board for learning firmware.")
    if "cortex-m4" in a:
        return ("Cortex-M4", "adds a single-precision FPU and DSP instructions — real signal "
                "processing is possible, but you must enable the FPU and save its context in "
                "interrupts.")
    if "cortex-m7" in a:
        return ("Cortex-M7", "FPU + DSP plus caches and a fast pipeline — powerful, but caches and "
                "flash wait-states make timing and coherency real concerns.")
    if "cortex-m33" in a:
        return ("Cortex-M33", "FPU/DSP plus TrustZone security — more capable, and more to "
                "configure before anything runs.")
    if "cortex-a" in a:
        return ("Cortex-A (application processor)", "runs Linux with an MMU. This is not bare-metal "
                "territory — you write user-space or kernel code, not register pokes from reset.")
    if "xtensa" in a:
        return ("Xtensa LX (ESP32)", "not an ARM core — a vendor CPU with Wi-Fi/BLE. You'll build on "
                "the ESP-IDF framework rather than raw registers.")
    if a == "avr" or "avr" in a:
        return ("AVR (8-bit)", "a tiny 8-bit Harvard core — no FPU, very little RAM, and F_CPU plus "
                "fuse bits drive everything. The simplest possible starting point.")
    return (arch or "unknown architecture", "architecture details are not seeded for this part.")


def flash_in_human_terms(flash_bytes: int | None) -> str:
    if flash_bytes is None:
        return "flash size is UNKNOWN — EAEDK cannot size your image against this board yet."
    kb = flash_bytes / 1024
    if kb < 32:
        note = "room for Blink, a UART logger, and a small driver — no network stack, no filesystem."
    elif kb < 128:
        note = ("fits a UART logger plus a sensor driver or two — not a TLS stack, an "
                "RTOS-with-networking, or a TFLite model.")
    elif kb < 512:
        note = "room for an RTOS, a small filesystem, and a light network stack — large ML still won't fit."
    elif kb < 2048:
        note = "an RTOS + networking (lwIP) + TLS is realistic; only large ML models are tight."
    else:
        note = "full stacks, filesystems, even small on-device ML — flash is rarely your limit here."
    return f"{_fmt_size(flash_bytes)} flash — {note}"


def ram_in_human_terms(ram_bytes: int | None) -> str:
    if ram_bytes is None:
        return "RAM size is UNKNOWN — EAEDK cannot run a RAM budget for this board yet."
    kb = ram_bytes / 1024
    if kb < 4:
        note = "every byte counts; a stack overflow is your first enemy. Avoid dynamic allocation."
    elif kb < 32:
        note = "fine for bare-metal drivers and a superloop; an RTOS with several tasks will be tight."
    elif kb < 128:
        note = "an RTOS with a handful of tasks and small buffers fits comfortably."
    elif kb < 512:
        note = "RTOS, network buffers, and a filesystem cache are realistic."
    else:
        note = "large buffers and even ML tensors are realistic; RAM is rarely your limit."
    return f"{_fmt_size(ram_bytes)} RAM — {note}"


def _arch_family(arch: str | None) -> str:
    a = (arch or "").lower()
    for fam in ("cortex-m", "cortex-a"):
        if fam in a:
            return fam
    if "xtensa" in a:
        return "xtensa"
    if "avr" in a:
        return "avr"
    return a


def validatable_facts(board: dict, soc: dict) -> tuple[list[str], list[str]]:
    """What EAEDK CAN validate (confirmed facts) vs CANNOT (UNKNOWN/absent facts) for this board.
    Honest: a fact is only listed as validatable when the underlying value is actually present."""
    can: list[str] = []
    cannot: list[str] = []
    is_linux = "cortex-a" in (soc.get("arch") or "").lower()

    if board.get("flash_bytes") is not None:
        can.append(f"Flash capacity — your image vs {_fmt_size(board['flash_bytes'])} of flash.")
        if board.get("flash_base") is not None:
            can.append(f"Vector-table placement — flash begins at 0x{board['flash_base']:08X}.")
    else:
        cannot.append("Flash capacity — flash size is UNKNOWN for this board.")

    if board.get("ram_bytes") is not None:
        can.append(f"RAM budget — stack + heap + static vs {_fmt_size(board['ram_bytes'])} of RAM.")
    else:
        cannot.append("RAM budget — RAM size is UNKNOWN for this board.")

    if board.get("flash_endurance_cycles") is not None:
        can.append(f"Flash write-endurance — write rate vs {board['flash_endurance_cycles']} rated "
                   "erase cycles.")
    elif board.get("primary_storage") == "internal_flash":
        cannot.append("Flash write-endurance — the erase-cycle rating is UNCONFIRMED for this board.")

    if is_linux:
        if board.get("ddr_bytes") is not None:
            can.append("DDR load-address conflicts — kernel/DTB placement within DRAM.")
        else:
            cannot.append("DDR timing/placement — DDR geometry is UNKNOWN for this board.")
    return can, cannot


def closest_board(conn: sqlite3.Connection, board_name: str) -> dict | None:
    """The nearest other board by architecture family, then by memory bracket (v3.0 P1)."""
    rows = {r["name"]: r for r in repo.boards_overview(conn)}
    me = rows.get(board_name)
    if me is None:
        return None
    fam = _arch_family(me["arch"])
    # Prefer same exact arch, then same family; rank by flash distance (fall back to RAM for boards
    # with no flash geometry, e.g. Linux SBCs).
    mine = me["flash_bytes"] if me["flash_bytes"] is not None else me["ram_bytes"]
    best = None
    for r in rows.values():
        if r["name"] == board_name or _arch_family(r["arch"]) != fam:
            continue
        theirs = r["flash_bytes"] if r["flash_bytes"] is not None else r["ram_bytes"]
        if theirs is None or mine is None:
            continue
        score = (0 if r["arch"] == me["arch"] else 1,
                 abs(math.log2(theirs) - math.log2(mine)))
        if best is None or score < best[0]:
            best = (score, r)
    if best is None:
        return None
    r = best[1]
    rel = ""
    if me["flash_bytes"] and r["flash_bytes"]:
        ratio = r["flash_bytes"] / me["flash_bytes"]
        rel = (f" (~{ratio:.0f}x the flash)" if ratio >= 1.5
               else f" (~{1/ratio:.0f}x less flash)" if ratio <= 0.66 else " (similar flash)")
    return {"name": r["name"], "soc": r["soc"], "arch": r["arch"],
            "flash_bytes": r["flash_bytes"], "ram_bytes": r["ram_bytes"], "relation": rel}


# --- 1A: board capability summary ----------------------------------------------------------------

def render_board_capabilities(conn: sqlite3.Connection, board_name: str) -> str | None:
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    caps = mentor.capability_map(conn, board_name)
    label, meaning = arch_family_meaning(soc["arch"])
    can, cannot = validatable_facts(board, soc)
    near = closest_board(conn, board_name)

    L = [f"# Board Capabilities — {board_name}  ({soc['name']}, {soc['arch']})", ""]
    L += ["## Architecture", f"  {label} — {meaning}", ""]
    L += ["## Memory (in human terms)",
          f"  • {flash_in_human_terms(board.get('flash_bytes'))}",
          f"  • {ram_in_human_terms(board.get('ram_bytes'))}", ""]
    L.append("## Confirmed peripherals (from seed data)")
    if caps:
        L += [f"  • {c['summary'] or c['capability']}" for c in caps]
    else:
        L.append("  (no peripherals are confirmed for this board — none are assumed)")
    L.append("")
    L.append("## What EAEDK CAN validate for this board")
    L += [f"  ✓ {c}" for c in can] or ["  (nothing — this board has no confirmed geometry yet)"]
    L.append("")
    L.append("## What EAEDK CANNOT yet validate (UNKNOWN — never assumed)")
    L += [f"  ✗ {c}" for c in cannot] or ["  (every relevant fact for this board is confirmed)"]
    if cannot:
        L.append("  → Onboard a missing fact with `eaedk ingest` (datasheet) or `eaedk board add`.")
    L.append("")
    L.append("## Closest board in the database")
    if near:
        L.append(f"  {near['name']} — same {_arch_family(soc['arch'])} family, "
                 f"{_fmt_size(near['flash_bytes'])} flash / {_fmt_size(near['ram_bytes'])} RAM"
                 f"{near['relation']}. A natural board to grow into.")
    else:
        L.append("  (no comparable board in the database)")
    L.append("")
    L.append("Every line above is a seeded fact or is explicitly flagged UNKNOWN — "
             "EAEDK does not guess hardware values.")
    return "\n".join(L) + "\n"


# --- 1B: project recommendation engine -----------------------------------------------------------

def recommend_projects(conn: sqlite3.Connection, board_name: str, level: int = 0) -> dict:
    """Structured recommendation: the dependency-ordered path the board can actually run, plus the
    steps held back because a peripheral they need is NOT confirmed (never assumed)."""
    cap_names = repo.board_capability_names(conn, board_name)
    path = mentor.learning_path_for(conn, cap_names)
    dropped = mentor.dropped_steps_for(conn, cap_names)
    # level 0 = complete beginner: start at the very first project. Higher levels could skip ahead;
    # only level 0 is fully specified, so anything else still starts from the top, honestly.
    start = 0
    return {"board": board_name, "level": level,
            "recommended": path[start:], "not_recommended": dropped}


def render_recommendation(conn: sqlite3.Connection, board_name: str, level: int = 0) -> str | None:
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return None
    rec = recommend_projects(conn, board_name, level)
    path = rec["recommended"]
    L = [f"# What to build first — {board_name}  (level {level}: "
         f"{'complete beginner' if level == 0 else 'experienced'})", ""]
    if not path:
        L.append("No projects are unlocked yet — this board has no confirmed peripherals for the "
                 "beginner path. Confirm its capabilities first "
                 f"(`eaedk board capabilities --board \"{board_name}\"`).")
        return "\n".join(L) + "\n"

    first = path[0]
    L += [f"## Start here: {first['title']}",
          f"  Why this before any other: {first['why']}",
          f"  What it teaches: {first['proves'] or first['why']}",
          f"  Peripherals it exercises: {', '.join(first['peripherals']) or 'core CPU only'}"]
    if first["failure_mode"]:
        L.append(f"  Expect it to break like this: {first['failure_mode']}")
        L.append(f"    How to diagnose: {first['diagnose']}")
    L.append("")

    nxt = path[1:4]                                  # the next 3, in dependency order
    if nxt:
        L.append("## Then, in dependency order (each validates what the next one needs):")
        for s in nxt:
            L.append(f"  {s['step']}. {s['title']}")
            if s["builds_on"]:
                L.append(f"       depends on: {s['builds_on']}")
            L.append(f"       teaches: {s['proves'] or s['why']}")
            L.append(f"       exercises: {', '.join(s['peripherals']) or 'core CPU only'}")
            if s["failure_mode"]:
                L.append(f"       expect: {s['failure_mode']}  →  diagnose: {s['diagnose']}")
            L.append("")

    if rec["not_recommended"]:
        L.append("## NOT recommended yet (a peripheral they need is not confirmed for this board):")
        for d in rec["not_recommended"]:
            caps_list = ", ".join(d["missing"])
            L.append(f"  • {d['title']} — needs {caps_list}, which is NOT confirmed for "
                     f"{board_name}. EAEDK will not assume it. Confirm it with "
                     f"`eaedk board capability add \"{board_name}\" {d['missing'][0].upper()}`.")
        L.append("")
    L.append("Sequenced by dependency, not by difficulty — each project proves the foundation the "
             "next one stands on.")
    return "\n".join(L) + "\n"


# --- 1C: job-focused learning roadmap (dependency graph, multi-board) ----------------------------

_ADVANCED_KEYS = {"bootloader", "rtos"}              # the HAL/RTOS/memory-layout tier


def render_roadmap(conn: sqlite3.Connection, board_names: list[str],
                   goal: str = "firmware-job") -> str | None:
    valid = [(n, *repo.load_board(conn, n)) for n in board_names]
    valid = [(n, b, s) for (n, b, s) in valid if b is not None]
    if not valid:
        return None
    primary = valid[0]
    multi = len(valid) > 1

    # Multi-board: the smallest-memory board carries the bare-metal fundamentals; the most capable
    # board carries the HAL/RTOS tier. Deterministic split by flash, then RAM.
    fundamentals = advanced = primary[0]
    if multi:
        ranked = sorted(valid, key=lambda t: (t[1].get("flash_bytes") or math.inf,
                                              t[1].get("ram_bytes") or math.inf))
        fundamentals, advanced = ranked[0][0], ranked[-1][0]

    cap_names = repo.board_capability_names(conn, primary[0])
    path = mentor.learning_path_for(conn, cap_names)

    goal_label = "land a firmware job" if goal in ("firmware-job", "firmware_job") else goal
    L = [f"# Firmware roadmap — goal: {goal_label}", ""]
    if multi:
        L.append(f"Boards in play: {', '.join(n for n, _, _ in valid)}.")
        L.append(f"  • Bare-metal fundamentals → **{fundamentals}** (simpler, smaller — learn the "
                 "registers with the least to misconfigure).")
        L.append(f"  • HAL / RTOS / memory-layout tier → **{advanced}** (more flash and RAM — room "
                 "for an RTOS and a HAL once the fundamentals are solid).")
        L.append("")
    L.append("## The path — a dependency graph, not a flat list")
    L.append("")
    prev = None
    for s in path:
        use_board = advanced if (s["key"] in _ADVANCED_KEYS and multi) else (
            fundamentals if multi else primary[0])
        arrow = f"{prev['title']}  →  " if prev else ""
        L.append(f"### {s['step']}. {s['title']}" + (f"   (on {use_board})" if multi else ""))
        if s["builds_on"]:
            L.append(f"  Depends on: {s['builds_on']}")
        elif prev is None:
            L.append("  Depends on: nothing — this is the foundation everything else stands on.")
        L.append(f"  Proves to an interviewer: {s['proves'] or s['why']}")
        L.append(f"  Exercises: {', '.join(s['peripherals']) or 'core CPU only'}")
        L.append("")
        prev = s
    if not path:
        L.append("  (no steps unlocked — confirm this board's peripherals first)")
    L.append("Build them in this order. Each project is a line on your résumé AND the prerequisite "
             "for the next — that sequence is the story you tell in the interview.")
    return "\n".join(L) + "\n"
