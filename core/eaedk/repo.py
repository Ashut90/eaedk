"""Database access helpers for boards, projects, inputs, checklist, decisions, risks.

Thin functions over SQLite; the orchestrator and CLI call these. Keeps SQL in one place.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .engines.risk.engine import RiskRule


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- boards ----------------------------------------------------------------

def load_board(conn: sqlite3.Connection, name: str
               ) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    row = conn.execute(
        "SELECT b.*, s.name AS soc_name, s.arch AS soc_arch, s.vendor AS soc_vendor "
        "FROM boards b JOIN socs s ON s.id = b.soc_id WHERE b.name = ?", (name,)
    ).fetchone()
    if row is None:
        return None, None
    board = {
        "name": row["name"], "flash_base": row["flash_base"], "flash_bytes": row["flash_bytes"],
        "ram_base": row["ram_base"], "ram_bytes": row["ram_bytes"], "ddr_type": row["ddr_type"],
        "ddr_bytes": row["ddr_bytes"], "primary_storage": row["primary_storage"],
        "confidence": row["confidence"],
    }
    soc = {"name": row["soc_name"], "arch": row["soc_arch"], "vendor": row["soc_vendor"]}
    return board, soc


def list_boards(conn: sqlite3.Connection, query: str | None = None) -> list[sqlite3.Row]:
    if query:
        return conn.execute(
            "SELECT b.name, s.name AS soc, s.arch FROM boards b JOIN socs s ON s.id=b.soc_id "
            "WHERE b.name LIKE ? OR s.name LIKE ? ORDER BY b.name",
            (f"%{query}%", f"%{query}%")).fetchall()
    return conn.execute(
        "SELECT b.name, s.name AS soc, s.arch FROM boards b JOIN socs s ON s.id=b.soc_id "
        "ORDER BY b.name").fetchall()


# --- risk rules ------------------------------------------------------------

def load_risk_rules(conn: sqlite3.Connection) -> list[RiskRule]:
    rows = conn.execute(
        "SELECT key,goal_type,condition_dsl,severity,explanation_tmpl,mitigation_tmpl "
        "FROM risk_rules").fetchall()
    return [RiskRule(r["key"], r["goal_type"], r["condition_dsl"], r["severity"],
                     r["explanation_tmpl"], r["mitigation_tmpl"]) for r in rows]


# --- templates -------------------------------------------------------------

def find_template(conn: sqlite3.Connection, goal_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM templates WHERE goal_type=? AND active=1 ORDER BY version DESC LIMIT 1",
        (goal_type,)).fetchone()


def template_items(conn: sqlite3.Connection, template_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM template_items WHERE template_id=? ORDER BY ordinal", (template_id,)
    ).fetchall()


# --- projects --------------------------------------------------------------

def create_project(conn: sqlite3.Connection, name: str, goal_type: str,
                   board_name: str | None) -> int:
    board_id = None
    if board_name:
        row = conn.execute("SELECT id FROM boards WHERE name=?", (board_name,)).fetchone()
        if row is None:
            raise ValueError(f"unknown board: {board_name}")
        board_id = row["id"]
    tpl = find_template(conn, goal_type)
    if tpl is None:
        raise ValueError(f"no template for goal_type: {goal_type}")
    now = _now()
    with conn:
        pid = conn.execute(
            "INSERT INTO projects(name,board_id,goal_type,status,template_id,created_at,updated_at)"
            " VALUES (?,?,?, 'active', ?,?,?)",
            (name, board_id, goal_type, tpl["id"], now, now)).lastrowid
        for item in template_items(conn, tpl["id"]):
            conn.execute(
                "INSERT INTO project_checklist(project_id,template_item_id,status) VALUES (?,?, 'todo')",
                (pid, item["id"]))
    return pid


def get_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()


def project_board_name(conn: sqlite3.Connection, project: sqlite3.Row) -> str | None:
    if project["board_id"] is None:
        return None
    row = conn.execute("SELECT name FROM boards WHERE id=?", (project["board_id"],)).fetchone()
    return row["name"] if row else None


def set_input(conn: sqlite3.Connection, project_id: int, key: str, value: str,
              confidence: str = "MEDIUM", source: str = "user",
              section: str | None = None) -> None:
    now = _now()
    citation_id = None
    with conn:
        src_id = conn.execute(
            "INSERT INTO sources(type,title,uri,hash,created_at) VALUES ('user',?,NULL,NULL,?)",
            (f"user input: {key}", now)).lastrowid
        if section:
            citation_id = conn.execute(
                "INSERT INTO citations(source_id,page,section,bbox_json,snippet) "
                "VALUES (?,NULL,?,NULL,?)", (src_id, section, str(value))).lastrowid
        conn.execute(
            "INSERT INTO project_inputs(project_id,key,value,source,citation_id,confidence,created_at)"
            " VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value,"
            " confidence=excluded.confidence, source=excluded.source, citation_id=excluded.citation_id",
            (project_id, key, str(value), source, citation_id, confidence, now))


def load_inputs(conn: sqlite3.Connection, project_id: int) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns (values, confidence_by_key). JSON-looking values are parsed to objects."""
    rows = conn.execute(
        "SELECT key,value,confidence FROM project_inputs WHERE project_id=?", (project_id,)
    ).fetchall()
    values: dict[str, Any] = {}
    conf: dict[str, str] = {}
    for r in rows:
        v = r["value"]
        if isinstance(v, str) and v and v[0] in "[{":
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        values[r["key"]] = v
        conf[r["key"]] = r["confidence"]
    return values, conf


def checklist(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pc.id, ti.item_key, ti.text, ti.category, ti.validation_rule_keys_json, pc.status, pc.note "
        "FROM project_checklist pc JOIN template_items ti ON ti.id=pc.template_item_id "
        "WHERE pc.project_id=? ORDER BY ti.ordinal", (project_id,)).fetchall()


def set_checklist_status(conn: sqlite3.Connection, project_id: int, item_key: str,
                         status: str, note: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE project_checklist SET status=?, note=COALESCE(?,note) "
            "WHERE project_id=? AND template_item_id=("
            "  SELECT ti.id FROM template_items ti JOIN projects p ON p.template_id=ti.template_id "
            "  WHERE p.id=? AND ti.item_key=?)",
            (status, note, project_id, project_id, item_key))


def add_decision(conn: sqlite3.Connection, project_id: int, title: str,
                 rationale: str | None, alternatives: list[str] | None) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO decisions(project_id,title,rationale,alternatives_json,made_at) "
            "VALUES (?,?,?,?,?)",
            (project_id, title, rationale,
             json.dumps(alternatives) if alternatives else None, _now())).lastrowid


def list_decisions(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT title,rationale,alternatives_json,made_at FROM decisions "
        "WHERE project_id=? ORDER BY made_at", (project_id,)).fetchall()


def replace_risks(conn: sqlite3.Connection, project_id: int,
                  findings: list[dict[str, Any]]) -> None:
    """Persist the latest risk evaluation (open ones); replaces prior open rows."""
    now = _now()
    with conn:
        conn.execute("DELETE FROM risks WHERE project_id=? AND status='open'", (project_id,))
        for f in findings:
            conn.execute(
                "INSERT INTO risks(project_id,rule_key,severity,explanation,mitigation,"
                "citation_id,status,created_at) VALUES (?,?,?,?,?,NULL,'open',?)",
                (project_id, f["rule_key"], f["severity"], f["explanation"],
                 f.get("mitigation"), now))
