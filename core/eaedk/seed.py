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
    "capabilities", "learning_steps", "concepts", "soc_defaults",
    "debug_probes", "soc_flash_profiles",
    "first_mistakes", "learning_step_intro", "board_blink_facts",
    "semantic_cost_estimates",
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

        # Get-or-create the SoC: boards legitimately share one (e.g. two RP2040 boards), and
        # socs.name is UNIQUE — so reuse the row when it already exists.
        existing_soc = conn.execute(
            "SELECT id FROM socs WHERE name = ?", (soc["name"],)).fetchone()
        if existing_soc is not None:
            soc_id = existing_soc["id"]
        else:
            soc_id = conn.execute(
                "INSERT INTO socs(name,vendor,arch,notes) VALUES (?,?,?,?)",
                (soc["name"], soc.get("vendor"), soc["arch"], soc.get("notes")),
            ).lastrowid

        board_id = conn.execute(
            "INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
            "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence,"
            "flash_endurance_cycles) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (soc_id, board["name"], board.get("flash_base"), board.get("flash_bytes"),
             board.get("ram_base"), board.get("ram_bytes"), board.get("ddr_type"),
             board.get("ddr_bytes"), board.get("primary_storage"),
             json.dumps(board.get("boot_modes", [])), source_id,
             board.get("confidence", "HIGH"), board.get("flash_endurance_cycles")),
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
            "mitigation_tmpl,requires_json,severity_on_unknown) VALUES (?,?,?,?,?,?,?,?)",
            (r["key"], r.get("goal_type"), r["condition_dsl"], r["severity"],
             r["explanation_tmpl"], r.get("mitigation_tmpl"),
             json.dumps(r.get("requires", [])), r.get("severity_on_unknown", "UNKNOWN")),
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


def _load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []


def _load_capabilities(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "capabilities.yaml") or []
    for r in rows:
        conn.execute("INSERT INTO capabilities(name,summary) VALUES (?,?)",
                     (r["name"], r["summary"]))
    return len(rows)


def _load_learning_steps(conn: sqlite3.Connection) -> int:
    # The bare-metal path and the Linux driver path both live in learning_steps (distinguished by
    # goal_type); the driver path is a separate seed file (v2.1.0), loaded the same way.
    rows = (_load_yaml(seed_dir() / "learning_path.yaml") or []) + \
           (_load_yaml(seed_dir() / "driver_path.yaml") or [])
    for r in rows:
        conn.execute(
            "INSERT INTO learning_steps(step,key,title,goal_type,requires_json,why,"
            "before_you_start_json,peripherals_json,failure_mode,diagnose,proves,builds_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["step"], r["key"], r["title"], r.get("goal_type"),
             json.dumps(r.get("requires", [])), r["why"],
             json.dumps(r.get("before_you_start", [])),
             json.dumps(r.get("peripherals", [])), r.get("failure_mode"),
             r.get("diagnose"), r.get("proves"), r.get("builds_on")))
    return len(rows)


def _load_semantic_cost_estimates(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "semantic_cost_estimates.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO semantic_cost_estimates(term,flash_min_bytes,flash_max_bytes,"
            "ram_min_bytes,ram_max_bytes,notes,source,verified_by_human) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (r["term"], r["flash_min_bytes"], r["flash_max_bytes"], r["ram_min_bytes"],
             r["ram_max_bytes"], r.get("notes"), r.get("source"),
             1 if r.get("verified_by_human") else 0))
    return len(rows)


def _load_concepts(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "concepts.yaml") or []
    for r in rows:
        conn.execute("INSERT INTO concepts(name,anchor) VALUES (?,?)",
                     (r["name"].lower(), r["anchor"]))
    return len(rows)


def _load_soc_defaults(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "soc_defaults.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO soc_defaults(soc_name,flash_base,flash_bytes,ram_base,ram_bytes) "
            "VALUES (?,?,?,?,?)",
            (r["soc_name"], r.get("flash_base"), r.get("flash_bytes"),
             r.get("ram_base"), r.get("ram_bytes")))
    return len(rows)


def _load_debug_probes(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "debug_probes.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO debug_probes(name,interface_cfg,summary) VALUES (?,?,?)",
            (r["name"], r["interface_cfg"], r.get("summary")))
    return len(rows)


def _load_soc_flash_profiles(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "soc_flash_profiles.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO soc_flash_profiles(soc_name,openocd_target,default_probe) "
            "VALUES (?,?,?)",
            (r["soc_name"], r["openocd_target"], r.get("default_probe")))
    return len(rows)


def _load_first_mistakes(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "first_mistakes.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO first_mistakes(family,mistake,fix,severity) VALUES (?,?,?,?)",
            (r["family"], r["mistake"], r["fix"], r.get("severity", "HIGH")))
    return len(rows)


def _load_board_blink_facts(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "board_blink_facts.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO board_blink_facts(board_name,led_pin,led_domain,clock_hint) "
            "VALUES (?,?,?,?)",
            (r["board_name"], r.get("led_pin"), r.get("led_domain"), r.get("clock_hint")))
    return len(rows)


def _load_learning_step_intro(conn: sqlite3.Connection) -> int:
    rows = _load_yaml(seed_dir() / "learning_step_intro.yaml") or []
    for r in rows:
        conn.execute(
            "INSERT INTO learning_step_intro(step_key,introduces,concept) VALUES (?,?,?)",
            (r["step_key"], r["introduces"], r.get("concept")))
    return len(rows)


def seed_all(conn: sqlite3.Connection, force: bool = False) -> dict[str, int]:
    if _already_seeded(conn) and not force:
        raise RuntimeError("Database already seeded. To reseed: `eaedk db seed --force`")
    with conn:
        if force:
            _clear(conn)
        counts = {
            "templates": _load_templates(conn),
            "boards": _load_boards(conn),
            "risk_rules": _load_risk_rules(conn),
            "eval_cases": _load_eval_cases(conn),
            "log_signatures": _load_log_signatures(conn),
            "capabilities": _load_capabilities(conn),
            "learning_steps": _load_learning_steps(conn),
            "concepts": _load_concepts(conn),
            "soc_defaults": _load_soc_defaults(conn),
            "debug_probes": _load_debug_probes(conn),
            "soc_flash_profiles": _load_soc_flash_profiles(conn),
            "first_mistakes": _load_first_mistakes(conn),
            "learning_step_intro": _load_learning_step_intro(conn),
            "board_blink_facts": _load_board_blink_facts(conn),
            "semantic_cost_estimates": _load_semantic_cost_estimates(conn),
        }
    return counts
