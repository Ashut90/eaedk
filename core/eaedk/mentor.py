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
    """Steps whose required capabilities the board actually has, in order. The Linux driver path
    (goal_type='driver') is its own section and is excluded here."""
    out = []
    for s in repo.learning_steps(conn):
        if s["goal_type"] == "driver":
            continue
        if set(json.loads(s["requires_json"])) <= cap_names:
            out.append({"step": s["step"], "key": s["key"], "title": s["title"],
                        "goal_type": s["goal_type"], "why": s["why"],
                        "before_you_start": json.loads(s["before_you_start_json"])})
    return out


def dropped_steps_for(conn: sqlite3.Connection, cap_names: set[str]) -> list[dict]:
    """Steps filtered OUT of the path, with the capabilities they're missing (Fix 6).

    A beginner must learn *why* a step is hidden and how to unlock it — never a silent drop.
    The driver path is shown in its own section, so it's not listed here.
    """
    out = []
    for s in repo.learning_steps(conn):
        if s["goal_type"] == "driver":
            continue
        required = set(json.loads(s["requires_json"]))
        missing = required - cap_names
        if missing:
            out.append({"step": s["step"], "title": s["title"],
                        "missing": sorted(missing)})
    return out


def supports_linux(soc: dict | None) -> bool:
    """A board runs Linux when its SoC is an application processor (Cortex-A). Deterministic."""
    return "cortex-a" in ((soc or {}).get("arch") or "").lower()


def driver_path(conn: sqlite3.Connection, board_name: str) -> list[dict]:
    """The Linux device-driver learning path — only for boards that run Linux, else []."""
    board, soc = repo.load_board(conn, board_name)
    if board is None or not supports_linux(soc):
        return []
    return [{"step": s["step"], "key": s["key"], "title": s["title"], "why": s["why"],
             "before_you_start": json.loads(s["before_you_start_json"])}
            for s in repo.driver_learning_steps(conn)]


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


def think_before_code(conn: sqlite3.Connection, board_name: str, goal: str) -> list[dict]:
    """Beginner questions to think through BEFORE writing code, generated from the board's
    verified facts (SoC family, geometry, capabilities) + goal — deterministic, not LLM, not
    hardcoded per board. Each item is {question, hint}. Honest: where a fact isn't in the DB,
    the hint points the user at where to find it rather than asserting it."""
    board, soc = repo.load_board(conn, board_name)
    if board is None:
        return []
    fam = family_of(soc["name"])
    caps = {c["capability"] for c in capability_map(conn, board_name)}
    items: list[dict] = []

    def add(q: str, hint: str):
        items.append({"question": q, "hint": hint})

    bf = repo.blink_facts(conn, board_name)        # board-specific answers from SQLite, if seeded
    if goal in ("bare_metal_app", "bootloader") or supports_linux(soc) is False:
        clock_covered = False
        if bf and bf["led_pin"]:
            add("Which pin is your LED on?", f"{board_name}: {bf['led_pin']}.")
            if bf["led_domain"] or bf["clock_hint"]:
                add("Which clock domain does that pin belong to, and is it enabled first?",
                    f"{bf['led_domain'] or 'see the reference manual'} — "
                    f"{bf['clock_hint'] or 'enable its clock before use'}. "
                    "If you skip this, the pin silently does nothing — no error, just nothing.")
                clock_covered = True       # don't ask the same RCC-enable question again below
        else:
            add("Which pin is your LED on?",
                "It's the board's on-board user LED — check the board's pinout/schematic.")
        # Family-specific clock concern (the #1 beginner bug), derived from the SoC family.
        if fam == "stm32":
            # The seeded clock-domain question above already covers RCC enable for STM32 — only
            # ask the generic version when we have no board-specific facts (no duplicate concept).
            if not clock_covered:
                add("Which GPIO port is that pin on, and do you enable that port's clock first?",
                    "On STM32 every peripheral is OFF at reset — you must enable its clock in RCC "
                    "BEFORE you touch the pin, or it silently does nothing.")
        elif fam == "rp2040":
            add("Is the second-stage bootloader (boot2) in place and your code linked for the "
                "flash window?",
                f"RP2040 runs code from external flash mapped at "
                f"{('0x%08X' % board['flash_base']) if board.get('flash_base') is not None else '0x10000000'}; "
                "without boot2 it never reaches your code.")
        elif fam == "esp32":
            add("Is your partition table valid and NVS initialised before Wi-Fi/BLE?",
                "ESP32 reboot-loops on a bad partition table; Wi-Fi/NVS calls fail if "
                "nvs_flash_init() didn't run first.")
        elif fam == "avr":
            add("Is F_CPU set to your real clock, and your fuses set to match?",
                "If F_CPU and the fuse clock source disagree, every delay and baud rate is wrong.")
        else:
            add("Does your code enable the peripheral's clock before using it?",
                "On most MCUs peripherals are off at reset — enable the clock first.")
        add("What order do your init functions run in?",
            "Clocks first, then the pin/peripheral config, then your loop — order matters.")
        add("What happens if the CPU reaches your code before the clock is stable?",
            "Reads can return 0 or the chip can fault; make sure clock setup completes first.")
        if board.get("flash_base") is not None:
            add("Is your interrupt vector table at the start of flash?",
                f"On this board flash starts at 0x%08X — the vector table must sit there or the "
                "first interrupt faults." % board["flash_base"])
        if "uart" in caps:
            add("Which UART/serial peripheral and TX pin will you print on, and is its clock on?",
                "You'll want serial printing to debug everything after blink — set it up early.")

    if goal == "driver":
        add("What is your device's compatible string, and does it match the driver's "
            "of_match_table?", "Linux binds a driver to a device-tree node by this exact string.")
        add("Does your device-tree node declare the right interrupt-parent and reg?",
            "A missing interrupt-parent or wrong reg base means probe() never runs correctly.")
        add("Will you start with a character device to learn the kernel API first?",
            "See the Driver development path — build a /dev node before touching real hardware.")

    return items


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
    # v2.1.0: Linux boards (Cortex-A) get a first-class device-driver path as its own section.
    dpath = driver_path(conn, board_name)
    if dpath:
        L.append("## Driver development path (this board runs Linux)")
        L.append("  Once Linux boots, this is the order to learn writing kernel drivers:")
        for s in dpath:
            L.append(f"  {s['step']}. {s['title']}")
            L.append(f"       why: {s['why']}")
            for b in s["before_you_start"]:
                L.append(f"         - {b}")
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
