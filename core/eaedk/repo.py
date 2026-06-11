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


# --- engineering facts (canonical write-through) ---------------------------

# Engineering source_type -> sources.type. SCHEMATIC has no dedicated sources.type yet, so
# it links to a 'manual' source while the precise classifier lives on facts.source_type.
_SOURCE_TYPE_TO_DOC = {
    "USER_INPUT": "user", "DATASHEET": "datasheet", "TRM": "trm",
    "SDK_DOC": "sdk_doc", "SCHEMATIC": "manual",
}
# Default fine `kind` per domain (kind retains its existing CHECK enum).
_DOMAIN_TO_KIND = {
    "MEMORY": "memmap", "PINMUX": "pinmux", "CLOCK": "clock", "TIMING": "timing",
}


def record_fact(conn: sqlite3.Connection, *, board_id: int, domain: str, fact_key: str,
                fact_value: Any, source_type: str, confidence: str,
                kind: str | None = None, citation_section: str | None = None,
                citation_page: int | None = None, snippet: str | None = None,
                verified_by_human: bool | None = None, source_id: int | None = None) -> int:
    """Canonical write-through for one engineering fact.

    Single entry point for the onboarding wizard and the datasheet ingester. Creates the
    provenance chain (source -> citation) consistent with ``source_type`` and inserts a
    `facts` row carrying the new ``domain``/``source_type`` dimensions. Does NOT open its own
    transaction — the caller controls it. Pass ``source_id`` to attach the citation to an
    existing source (e.g. one datasheet shared by many confirmed facts).
    """
    if source_type not in _SOURCE_TYPE_TO_DOC:
        raise ValueError(f"unknown source_type: {source_type}")
    now = _now()
    doc_type = _SOURCE_TYPE_TO_DOC[source_type]
    if source_id is None:
        source_id = conn.execute(
            "INSERT INTO sources(type,title,uri,hash,created_at) VALUES (?,?,NULL,NULL,?)",
            (doc_type, f"{source_type}: {fact_key}", now)).lastrowid
    cit_id = conn.execute(
        "INSERT INTO citations(source_id,page,section,bbox_json,snippet) VALUES (?,?,?,NULL,?)",
        (source_id, citation_page, citation_section or "engineering fact",
         snippet if snippet is not None else str(fact_value))).lastrowid
    if verified_by_human is None:
        verified_by_human = confidence == "HIGH"
    value_str = (json.dumps(fact_value) if isinstance(fact_value, (list, dict))
                 else str(fact_value))
    return conn.execute(
        "INSERT INTO facts(board_id,kind,domain,source_type,key,value,citation_id,"
        "confidence,verified_by_human,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (board_id, kind or _DOMAIN_TO_KIND.get(domain, "memmap"), domain, source_type,
         fact_key, value_str, cit_id, confidence, 1 if verified_by_human else 0, now)
    ).lastrowid


# --- boards ----------------------------------------------------------------

def create_manual_source(conn: sqlite3.Connection, title: str, uri: str | None = None) -> int:
    """Create a 'manual' provenance source (e.g. for an interactively onboarded board)."""
    return conn.execute(
        "INSERT INTO sources(type,title,uri,hash,created_at) VALUES ('manual',?,?,NULL,?)",
        (title, uri, _now())).lastrowid


def get_or_create_soc(conn: sqlite3.Connection, name: str, vendor: str | None,
                      arch: str, notes: str | None = None) -> int:
    """Reuse an existing SoC by name (socs.name is UNIQUE) or create it."""
    row = conn.execute("SELECT id FROM socs WHERE name=?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    return conn.execute(
        "INSERT INTO socs(name,vendor,arch,notes) VALUES (?,?,?,?)",
        (name, vendor, arch, notes)).lastrowid


def create_board(conn: sqlite3.Connection, *, soc_id: int, name: str,
                 flash_base: int | None, flash_bytes: int | None,
                 ram_base: int | None, ram_bytes: int | None,
                 source_id: int, confidence: str,
                 ddr_type: str | None = None, ddr_bytes: int | None = None,
                 primary_storage: str = "internal_flash",
                 boot_modes: list[str] | None = None) -> int:
    """Insert a typed board identity row. Board identity stays typed (not EAV)."""
    return conn.execute(
        "INSERT INTO boards(soc_id,name,flash_base,flash_bytes,ram_base,ram_bytes,"
        "ddr_type,ddr_bytes,primary_storage,boot_modes_json,source_id,confidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (soc_id, name, flash_base, flash_bytes, ram_base, ram_bytes, ddr_type, ddr_bytes,
         primary_storage, json.dumps(boot_modes or []), source_id, confidence)).lastrowid


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


def near_match_boards(conn: sqlite3.Connection, name: str,
                      soc_name: str | None = None) -> list[sqlite3.Row]:
    """Existing boards whose name/SoC shares a 4+ char token with the query (for suggestions)."""
    import re as _re
    toks = [t for t in _re.split(r"[^a-z0-9]+", f"{name or ''} {soc_name or ''}".lower())
            if len(t) >= 4]
    if not toks:
        return []
    out: list[sqlite3.Row] = []
    for row in conn.execute(
            "SELECT b.name, b.flash_bytes, s.name AS soc, s.arch "
            "FROM boards b JOIN socs s ON s.id = b.soc_id").fetchall():
        hay = _re.sub(r"[^a-z0-9]", "", f"{row['name']}{row['soc'] or ''}".lower())
        if any(t in hay for t in toks):
            out.append(row)
    return out


def soc_defaults_for(conn: sqlite3.Connection, board_name: str) -> sqlite3.Row | None:
    """Standard geometry for the board's SoC, if known (lets export offer values)."""
    return conn.execute(
        "SELECT d.soc_name, d.flash_base, d.flash_bytes, d.ram_base, d.ram_bytes "
        "FROM soc_defaults d JOIN socs s ON s.name = d.soc_name "
        "JOIN boards b ON b.soc_id = s.id WHERE b.name = ?", (board_name,)).fetchone()


def apply_soc_defaults(conn: sqlite3.Connection, board_name: str) -> sqlite3.Row | None:
    """Fill the board's NULL geometry columns from its SoC's standard values. Returns the
    applied row, or None if the SoC isn't recognized."""
    d = soc_defaults_for(conn, board_name)
    if d is None:
        return None
    with conn:
        conn.execute(
            "UPDATE boards SET "
            "flash_base = COALESCE(flash_base, ?), flash_bytes = COALESCE(flash_bytes, ?), "
            "ram_base = COALESCE(ram_base, ?), ram_bytes = COALESCE(ram_bytes, ?) "
            "WHERE name = ?",
            (d["flash_base"], d["flash_bytes"], d["ram_base"], d["ram_bytes"], board_name))
    return d


def debug_probes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Seeded debug-probe -> OpenOCD interface cfg map (for the FLASH.md 'other probes' table)."""
    return conn.execute(
        "SELECT name, interface_cfg, summary FROM debug_probes ORDER BY id").fetchall()


def soc_flash_profile_for(conn: sqlite3.Connection, board_name: str) -> sqlite3.Row | None:
    """OpenOCD target + default probe (with its interface cfg) for a board's SoC, if seeded.
    Lets FLASH.md emit a filled-in flash command instead of <probe>/<target> placeholders."""
    return conn.execute(
        "SELECT f.soc_name, f.openocd_target, f.default_probe, p.interface_cfg "
        "FROM soc_flash_profiles f "
        "JOIN socs s ON s.name = f.soc_name "
        "JOIN boards b ON b.soc_id = s.id "
        "LEFT JOIN debug_probes p ON p.name = f.default_probe "
        "WHERE b.name = ?", (board_name,)).fetchone()


def add_board_capability(conn: sqlite3.Connection, board_name: str, capability: str) -> bool:
    """Add a capability to a board (idempotent). Returns False if the board is unknown. Does
    NOT open a transaction — the caller controls it."""
    row = conn.execute("SELECT id FROM boards WHERE name = ?", (board_name,)).fetchone()
    if row is None:
        return False
    existing = {r["capability"] for r in conn.execute(
        "SELECT capability FROM board_capabilities WHERE board_id = ?", (row["id"],)).fetchall()}
    if capability not in existing:
        conn.execute(
            "INSERT INTO board_capabilities(board_id,capability,details_json) "
            "VALUES (?,?,NULL)", (row["id"], capability))
    return True


def list_boards(conn: sqlite3.Connection, query: str | None = None) -> list[sqlite3.Row]:
    if query:
        return conn.execute(
            "SELECT b.name, s.name AS soc, s.arch FROM boards b JOIN socs s ON s.id=b.soc_id "
            "WHERE b.name LIKE ? OR s.name LIKE ? ORDER BY b.name",
            (f"%{query}%", f"%{query}%")).fetchall()
    return conn.execute(
        "SELECT b.name, s.name AS soc, s.arch FROM boards b JOIN socs s ON s.id=b.soc_id "
        "ORDER BY b.name").fetchall()


# --- mentor layer ----------------------------------------------------------

def board_capability_map(conn: sqlite3.Connection, board_name: str) -> list[sqlite3.Row]:
    """Each board capability with its plain-language summary (NULL if not described yet)."""
    return conn.execute(
        "SELECT bc.capability AS capability, c.summary AS summary "
        "FROM board_capabilities bc JOIN boards b ON b.id = bc.board_id "
        "LEFT JOIN capabilities c ON c.name = bc.capability "
        "WHERE b.name = ? ORDER BY bc.capability", (board_name,)).fetchall()


def board_capability_names(conn: sqlite3.Connection, board_name: str) -> set[str]:
    return {r["capability"] for r in conn.execute(
        "SELECT bc.capability FROM board_capabilities bc JOIN boards b ON b.id = bc.board_id "
        "WHERE b.name = ?", (board_name,)).fetchall()}


def board_facts_map(conn: sqlite3.Connection, board_name: str) -> dict[str, str]:
    """All confirmed facts for a board as {fact_key: fact_value} (via the engineering_facts view)."""
    return {r["fact_key"]: r["fact_value"] for r in conn.execute(
        "SELECT ef.fact_key AS fact_key, ef.fact_value AS fact_value FROM engineering_facts ef "
        "JOIN boards b ON b.id = ef.board_id WHERE b.name = ?", (board_name,)).fetchall()}


def learning_steps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT step, key, title, goal_type, requires_json, why, before_you_start_json "
        "FROM learning_steps ORDER BY step").fetchall()


def get_concept(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT name, anchor FROM concepts WHERE name = ?",
                        (name.lower(),)).fetchone()


def list_concepts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT name, anchor FROM concepts ORDER BY name").fetchall()


# --- datasheet ingestion (fact candidates) ---------------------------------

def create_datasheet_source(conn: sqlite3.Connection, title: str, uri: str | None,
                            file_hash: str | None) -> int:
    return conn.execute(
        "INSERT INTO sources(type,title,uri,hash,created_at) VALUES ('datasheet',?,?,?,?)",
        (title, uri, file_hash, _now())).lastrowid


def add_fact_candidate(conn: sqlite3.Connection, *, board_id: int, source_id: int | None,
                       domain: str, fact_key: str, fact_value: str, method: str,
                       confidence: str, page: int | None = None, section: str | None = None,
                       snippet: str | None = None, kind: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO fact_candidates(board_id,source_id,domain,kind,fact_key,fact_value,"
        "method,confidence,page,section,snippet,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)",
        (board_id, source_id, domain, kind, fact_key, str(fact_value), method, confidence,
         page, section, snippet, _now())).lastrowid


def list_fact_candidates(conn: sqlite3.Connection, board_name: str,
                         status: str = "pending") -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.* FROM fact_candidates c JOIN boards b ON b.id = c.board_id "
        "WHERE b.name = ? AND c.status = ? ORDER BY c.confidence, c.domain, c.fact_key",
        (board_name, status)).fetchall()


def get_fact_candidate(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM fact_candidates WHERE id=?", (candidate_id,)).fetchone()


def set_candidate_status(conn: sqlite3.Connection, candidate_id: int, status: str) -> None:
    with conn:
        conn.execute("UPDATE fact_candidates SET status=? WHERE id=?", (status, candidate_id))


# --- toolchain -------------------------------------------------------------

def replace_toolchain(conn: sqlite3.Connection, components: list[dict[str, Any]]) -> int:
    """Replace the host's detected toolchain inventory (detect is a full re-scan)."""
    now = _now()
    with conn:
        conn.execute("DELETE FROM toolchain_components")
        for c in components:
            conn.execute(
                "INSERT INTO toolchain_components(kind,name,version,target_triple,path,raw,"
                "detected_at) VALUES (?,?,?,?,?,?,?)",
                (c["kind"], c["name"], c.get("version"), c.get("target_triple"),
                 c.get("path"), c.get("raw"), now))
    return len(components)


def load_toolchain(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT kind,name,version,target_triple,path,detected_at FROM toolchain_components "
        "ORDER BY kind,name").fetchall()


def add_board_toolchain_req(conn: sqlite3.Connection, board_id: int, kind: str, name: str,
                            severity: str, target_triple: str | None = None,
                            min_version: str | None = None, why: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO board_toolchain_reqs(board_id,kind,name,target_triple,min_version,"
        "severity,why) VALUES (?,?,?,?,?,?,?)",
        (board_id, kind, name, target_triple, min_version, severity, why)).lastrowid


def load_board_toolchain_reqs(conn: sqlite3.Connection, board_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.kind, r.name, r.target_triple, r.min_version, r.severity, r.why "
        "FROM board_toolchain_reqs r JOIN boards b ON b.id = r.board_id "
        "WHERE b.name = ?", (board_name,)).fetchall()


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
                   board_name: str | None, allow_missing_template: bool = False) -> int:
    """Create a project. A template is auto-selected by goal_type and its checklist seeded.
    With ``allow_missing_template`` a 'custom' goal with no template creates a template-less
    project (no checklist; global validation rules still apply)."""
    board_id = None
    if board_name:
        row = conn.execute("SELECT id FROM boards WHERE name=?", (board_name,)).fetchone()
        if row is None:
            raise ValueError(f"unknown board: {board_name}")
        board_id = row["id"]
    tpl = find_template(conn, goal_type)
    if tpl is None and not allow_missing_template:
        raise ValueError(f"no template for goal_type: {goal_type}")
    now = _now()
    template_id = tpl["id"] if tpl is not None else None
    with conn:
        pid = conn.execute(
            "INSERT INTO projects(name,board_id,goal_type,status,template_id,created_at,updated_at)"
            " VALUES (?,?,?, 'active', ?,?,?)",
            (name, board_id, goal_type, template_id, now, now)).lastrowid
        if tpl is not None:
            for item in template_items(conn, tpl["id"]):
                conn.execute(
                    "INSERT INTO project_checklist(project_id,template_item_id,status) "
                    "VALUES (?,?, 'todo')", (pid, item["id"]))
    return pid


def get_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()


def active_project(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recently updated active project — the 'current' project for project-aware ops."""
    return conn.execute(
        "SELECT * FROM projects WHERE status='active' ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()


def unverified_board_facts(conn: sqlite3.Connection, board_name: str) -> list[sqlite3.Row]:
    """Board facts that are assumptions, not confirmed truth: MEDIUM/LOW confidence or
    not human-verified. Read through the engineering_facts VIEW (provenance preserved)."""
    return conn.execute(
        "SELECT ef.domain AS domain, ef.fact_key AS fact_key, ef.fact_value AS fact_value, "
        "ef.confidence AS confidence, ef.source_type AS source_type, "
        "ef.citation_detail AS citation_detail "
        "FROM engineering_facts ef JOIN boards b ON b.id = ef.board_id "
        "WHERE b.name = ? AND (ef.verified_by_human = 0 OR ef.confidence IN ('MEDIUM','LOW')) "
        "ORDER BY ef.domain, ef.fact_key", (board_name,)).fetchall()


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


def append_checklist_note(conn: sqlite3.Connection, project_id: int, item_key: str,
                          note: str) -> bool:
    """Append a line to a checklist item's note (newline-joined). Returns True if matched."""
    with conn:
        cur = conn.execute(
            "UPDATE project_checklist "
            "SET note = CASE WHEN note IS NULL OR note='' THEN ? ELSE note || char(10) || ? END "
            "WHERE project_id=? AND template_item_id=("
            "  SELECT ti.id FROM template_items ti JOIN projects p ON p.template_id=ti.template_id "
            "  WHERE p.id=? AND ti.item_key=?)",
            (note, note, project_id, project_id, item_key))
        return cur.rowcount > 0


def items_for_rule(conn: sqlite3.Connection, project_id: int, rule_key: str) -> list[str]:
    """Checklist item_keys for this project whose template item owns the given rule."""
    out: list[str] = []
    for row in checklist(conn, project_id):
        if rule_key in json.loads(row["validation_rule_keys_json"]):
            out.append(row["item_key"])
    return out


def get_risk(conn: sqlite3.Connection, risk_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM risks WHERE id=?", (risk_id,)).fetchone()


def get_project_by_id(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


def list_risks_by_status(conn: sqlite3.Connection, project_id: int,
                         status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM risks WHERE project_id=? AND status=? ORDER BY severity, id",
        (project_id, status)).fetchall()


def resolve_risk(conn: sqlite3.Connection, risk_id: int, note: str | None) -> str:
    """Resolve a tracked risk. Returns one of:
    'not_found' | 'already' | 'not_tracked' | 'resolved'. Only tracked risks are resolvable
    (risk-engine 'open' rows are ephemeral and rewritten by replace_risks)."""
    row = get_risk(conn, risk_id)
    if row is None:
        return "not_found"
    if row["status"] == "resolved":
        return "already"
    if row["status"] != "tracked":
        return "not_tracked"
    with conn:
        conn.execute(
            "UPDATE risks SET status='resolved', resolved_at=?, resolution_note=? WHERE id=?",
            (_now(), note, risk_id))
    return "resolved"


def get_tracked_risk(conn: sqlite3.Connection, project_id: int, rule_key: str
                     ) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM risks WHERE project_id=? AND rule_key=? AND status='tracked' LIMIT 1",
        (project_id, rule_key)).fetchone()


def upsert_tracked_risk(conn: sqlite3.Connection, project_id: int, rule_key: str,
                        severity: str, explanation: str, mitigation: str | None = None) -> str:
    """Open a tracked risk for a triage-implicated rule, or append to the existing one.

    Tracked risks (status='tracked') are distinct from the deterministic risk-engine snapshot
    (status='open', which replace_risks() rewrites) — so they persist and never collide.
    Returns 'opened' or 'appended'.
    """
    now = _now()
    existing = get_tracked_risk(conn, project_id, rule_key)
    with conn:
        if existing is not None:
            conn.execute(
                "UPDATE risks SET explanation = explanation || char(10) || ? WHERE id=?",
                (explanation, existing["id"]))
            return "appended"
        conn.execute(
            "INSERT INTO risks(project_id,rule_key,severity,explanation,mitigation,"
            "citation_id,status,created_at) VALUES (?,?,?,?,?,NULL,'tracked',?)",
            (project_id, rule_key, severity, explanation, mitigation, now))
        return "opened"


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
