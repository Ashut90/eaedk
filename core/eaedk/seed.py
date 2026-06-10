"""Seed loader: load templates + boards + risk rules + eval cases from packages/ YAML.

Seeding is *not* a migration (spec §1.9): seed data is diffable YAML and reloadable with
``--force`` without a schema bump. Projects are never touched by seeding.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import yaml

from . import repo
from .paths import seed_dir, templates_dir

# Tables that hold seed-derived rows (cleared on --force). Projects are excluded.
_SEED_TABLES = [
    "template_items", "templates",
    "board_toolchain_reqs", "board_capabilities", "boards", "socs",
    "risk_rules", "eval_cases", "log_signatures",
    "citations", "sources",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _already_seeded(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0] > 0


def _clear(conn: sqlite3.Connection) -> None:
    for t in _SEED_TABLES:
        conn.execute(f"DELETE FROM {t}")


def _load_templates(conn: sqlite3.Connection) -> int:
    n = 0
    for path in sorted(templates_dir().glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cur = conn.execute(
            "INSERT INTO templates(key,name,version,goal_type,active) VALUES (?,?,?,?,1)",
            (data["key"], data["name"], int(data["version"]), data["goal_type"]),
        )
        tid = cur.lastrowid
        for ordinal, item in enumerate(data.get("items", [])):
            conn.execute(
                "INSERT INTO template_items(template_id,item_key,text,category,"
                "required_inputs_json,validation_rule_keys_json,ordinal) VALUES (?,?,?,?,?,?,?)",
                (tid, item["key"], item["text"], item["category"],
                 json.dumps(item.get("required_inputs", [])),
                 json.dumps(item.get("validation_rules", [])), ordinal),
            )
        n += 1
    return n


def _load_boards(conn: sqlite3.Connection) -> int:
    n = 0
    for path in sorted((seed_dir() / "boards").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        soc = data["soc"]
        board = data["board"]
        src = data.get("source", {})

        cur = conn.execute(
            "INSERT INTO sources(type,title,uri,hash,created_at) VALUES (?,?,?,?,?)",
            (src.get("type", "seed"), src.get("title", board["name"]),
             src.get("uri"), None, _now()),
        )
        source_id = cur.lastrowid
        conn.execute(
            "INSERT INTO citations(source_id,page,section,bbox_json,snippet) VALUES (?,?,?,?,?)",
            (source_id, src.get("page"), src.get("section"), None, src.get("snippet")),
        )

        soc_id = conn.execute(
            "INSERT INTO socs(name,vendor,arch,notes) VALUES (?,?,?,?)",
            (soc["name"], soc.get("vendor"), soc["arch"], soc.get("notes")),
        ).lastrowid

        board_id = conn.execute(
            "INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
            "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (soc_id, board["name"], board.get("flash_base"), board.get("flash_bytes"),
             board.get("ram_base"), board.get("ram_bytes"), board.get("ddr_type"),
             board.get("ddr_bytes"), board.get("primary_storage"),
             json.dumps(board.get("boot_modes", [])), source_id,
             board.get("confidence", "HIGH")),
        ).lastrowid

        for cap in data.get("capabilities", []):
            conn.execute(
                "INSERT INTO board_capabilities(board_id,capability,details_json) VALUES (?,?,?)",
                (board_id, cap["capability"], cap.get("details_json")),
            )
        # Toolchain profile (required build environment) via the repo write-through.
        for req in data.get("toolchain", []):
            repo.add_board_toolchain_req(
                conn, board_id, kind=req["kind"], name=req["name"],
                severity=req.get("severity", "MEDIUM"),
                target_triple=req.get("target_triple"), min_version=req.get("min_version"),
                why=req.get("why"))
        n += 1
    return n


def _load_risk_rules(conn: sqlite3.Connection) -> int:
    rules = yaml.safe_load((seed_dir() / "risk_rules.yaml").read_text(encoding="utf-8")) or []
    for r in rules:
        conn.execute(
            "INSERT INTO risk_rules(key,goal_type,condition_dsl,severity,explanation_tmpl,"
            "mitigation_tmpl) VALUES (?,?,?,?,?,?)",
            (r["key"], r.get("goal_type"), r["condition_dsl"], r["severity"],
             r["explanation_tmpl"], r.get("mitigation_tmpl")),
        )
    return len(rules)


def _load_eval_cases(conn: sqlite3.Connection) -> int:
    cases = yaml.safe_load((seed_dir() / "eval_cases.yaml").read_text(encoding="utf-8")) or []
    for c in cases:
        conn.execute(
            "INSERT INTO eval_cases(name,goal_type,inputs_json,expected_json) VALUES (?,?,?,?)",
            (c["name"], c["goal_type"], json.dumps(c["inputs"]), json.dumps(c["expected"])),
        )
    return len(cases)


def _load_log_signatures(conn: sqlite3.Connection) -> int:
    path = seed_dir() / "log_signatures.yaml"
    if not path.exists():
        return 0
    sigs = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    now = _now()
    for s in sigs:
        conn.execute(
            "INSERT INTO log_signatures(format,pattern_regex,cause,fix,severity,source_id,"
            "created_at) VALUES (?,?,?,?,?,NULL,?)",
            (s["format"], s["pattern_regex"], s["cause"], s["fix"], s["severity"], now))
    return len(sigs)


def seed_all(conn: sqlite3.Connection, force: bool = False) -> dict[str, int]:
    if _already_seeded(conn) and not force:
        raise RuntimeError("database already seeded; pass force=True to reseed")
    with conn:
        if force:
            _clear(conn)
        counts = {
            "templates": _load_templates(conn),
            "boards": _load_boards(conn),
            "risk_rules": _load_risk_rules(conn),
            "eval_cases": _load_eval_cases(conn),
            "log_signatures": _load_log_signatures(conn),
        }
    return counts
