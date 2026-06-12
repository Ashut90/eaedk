"""Engineering State Engine (v2.2.0) — project progress derived from EVIDENCE.

Progress is never a stored number and is never set by the LLM. Each checklist item's status is
derived deterministically, in priority order:
  1. VALIDATION_ENGINE — the item's owning validation rule(s) all PASS
  2. LOG_TRIAGE       — the item's owning rule is a *resolved* tracked risk
  3. USER             — the engineer confirmed it (`eaedk checklist done`)
The percentage is computed (completed / total), never persisted. The LLM only explains the result.
"""
from __future__ import annotations

import json
import sqlite3

from .. import repo
from ..orchestrator import assess_project
from .validation.rules import RULE_TEACH

# Plain-English "why it matters" for checklist items that have no validation rule of their own.
# Beginner-clear, mid-level-true. Keyed by template item_key; falls back to the item text.
_WHY_NO_RULE = {
    # bare_metal_app items that have no validation rule of their own (v2.2.1).
    "peripheral_confirmed":
        "Without knowing which peripheral you're using, the template can't generate the right "
        "register setup — you'll get code for the wrong pins.",
    "clock_init":
        "Every peripheral is off by default to save power. If you don't enable its clock first, "
        "it silently does nothing — no error, just nothing.",
    "main_loop":
        "Embedded firmware never exits. The main loop is where your code runs forever — without "
        "it, the CPU falls off the end of main and crashes.",
    "build_and_flash":
        "If your compiler targets the wrong architecture, the firmware runs on your PC but won't "
        "run on the chip — or won't build at all.",
    # bootloader items below.
    "clock_init_sequence":
        "If clocks aren't set up first, every peripheral you touch afterwards may silently do "
        "nothing — no error, just nothing.",
    "watchdog_strategy":
        "Without a watchdog, a hung bootloader stays hung forever; with one, the chip resets and "
        "recovers.",
    "integrity_verification":
        "Without a CRC/hash check, a corrupted image boots anyway and crashes in the field.",
    "fallback_rollback":
        "Without rollback, a failed update permanently bricks the device — there's no way back.",
    "recovery_entry_condition":
        "Recovery mode is your only way back in if the main firmware is corrupt.",
    "update_interface":
        "This is how new firmware gets onto the device — without it there's no way to update.",
    "flash_write_protection":
        "Without write protection, any buggy or malicious code can overwrite the bootloader "
        "itself.",
}

_COMPLETE, _IN_PROGRESS, _NOT_STARTED = "COMPLETE", "IN_PROGRESS", "NOT_STARTED"


def _why_it_matters(item: dict, rules: list[str]) -> str:
    for r in rules:
        if r in RULE_TEACH:
            return RULE_TEACH[r]
    return _WHY_NO_RULE.get(item["item_key"], f"Complete this so EAEDK can verify: {item['text']}.")


def project_status(conn: sqlite3.Connection, project: sqlite3.Row) -> dict:
    """Derive and record per-item progress; return a structured, plain-English status report."""
    rows = repo.checklist_with_item_ids(conn, project["id"])
    resp = assess_project(conn, project)
    by_check = {v["check"]: v for v in resp.validations}
    resolved = {r["rule_key"] for r in repo.list_risks_by_status(conn, project["id"], "resolved")}

    items: list[dict] = []
    for row in rows:
        rules = json.loads(row["validation_rule_keys_json"])
        rule_pass = bool(rules) and all(by_check.get(r, {}).get("status") == "PASS" for r in rules)
        rule_engaged = any(by_check.get(r, {}).get("engaged") for r in rules)
        user_done = row["status"] == "done"
        log_resolved = any(r in resolved for r in rules)

        if rule_pass:
            status, by, ev = _COMPLETE, "VALIDATION_ENGINE", \
                f"{', '.join(rules)} rule(s): PASS"
        elif user_done:
            status, by, ev = _COMPLETE, "USER", "confirmed by engineer"
        elif log_resolved:
            status, by, ev = _COMPLETE, "LOG_TRIAGE", "resolved from log triage"
        elif rule_engaged:
            status, by, ev = _IN_PROGRESS, None, "started — values provided but not yet verified"
        else:
            status, by, ev = _NOT_STARTED, None, None

        repo.record_progress(conn, project["id"], row["template_item_id"], status, ev, by)
        items.append({
            "item_key": row["item_key"], "title": row["text"], "category": row["category"],
            "status": status, "evidence": ev, "verified_by": by,
            "why_it_matters": "" if status == _COMPLETE else _why_it_matters(dict(row), rules),
        })

    total = len(items)
    complete = sum(1 for i in items if i["status"] == _COMPLETE)
    pct = round(100 * complete / total) if total else 0
    nxt = next((i for i in items if i["status"] != _COMPLETE), None)
    # v2.2.1: a beginner shouldn't feel they must do all of these to blink — most are filled in
    # by the engine. Reassure them for the first-project goal.
    note = ("Most of these are optional for a first blink — the engine fills in what it can. "
            "Focus on the first incomplete item below.") if project["goal_type"] == "bare_metal_app" else ""
    return {
        "project": project["name"], "complete": complete, "total": total, "percent": pct,
        "note": note, "items": items,
        "next": ({"title": nxt["title"], "why_it_matters": nxt["why_it_matters"]} if nxt else None),
    }
