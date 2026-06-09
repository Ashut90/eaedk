"""Golden eval harness (spec §5). Runs each case through the deterministic engines and
compares against its expected (subset) assertions. LLM-free and deterministic.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .orchestrator import assess


def _check_case(conn: sqlite3.Connection, inputs: dict[str, Any], goal_type: str,
                expected: dict[str, Any]) -> list[str]:
    board_name = inputs.get("board")
    case_inputs = {k: v for k, v in inputs.items() if k != "board"}
    resp = assess(conn, goal_type, case_inputs, board_name=board_name)

    diffs: list[str] = []

    if "feasibility" in expected and resp.feasibility != expected["feasibility"]:
        diffs.append(f"feasibility: expected {expected['feasibility']}, got {resp.feasibility}")

    by_check = {v["check"]: v["status"] for v in resp.validations}
    for check, want in expected.get("validations", {}).items():
        got = by_check.get(check, "MISSING")
        if got != want:
            diffs.append(f"validation {check}: expected {want}, got {got}")

    fired = {r["rule_key"] for r in resp.risks}
    for key in expected.get("risks_contains", []):
        if key not in fired:
            diffs.append(f"risk {key}: expected present, absent")
    for key in expected.get("risks_absent", []):
        if key in fired:
            diffs.append(f"risk {key}: expected absent, present")

    unk_blob = " | ".join(resp.unknowns)
    for needle in expected.get("unknowns_contains", []):
        if needle not in unk_blob:
            diffs.append(f"unknowns: expected substring {needle!r}, not found")

    return diffs


def run_eval(conn: sqlite3.Connection, case_name: str | None = None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT name,goal_type,inputs_json,expected_json FROM eval_cases"
        + (" WHERE name=?" if case_name else "") + " ORDER BY name",
        (case_name,) if case_name else ()).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    results = []
    passed_count = 0
    for r in rows:
        inputs = json.loads(r["inputs_json"])
        expected = json.loads(r["expected_json"])
        diffs = _check_case(conn, inputs, r["goal_type"], expected)
        ok = not diffs
        passed_count += ok
        results.append({"name": r["name"], "passed": ok, "diffs": diffs})
        with conn:
            cid = conn.execute("SELECT id FROM eval_cases WHERE name=?", (r["name"],)).fetchone()[0]
            conn.execute(
                "INSERT INTO eval_runs(case_id,passed,diff_json,run_at) VALUES (?,?,?,?)",
                (cid, 1 if ok else 0, json.dumps(diffs), now))

    return {"total": len(results), "passed": passed_count,
            "failed": len(results) - passed_count, "cases": results}
