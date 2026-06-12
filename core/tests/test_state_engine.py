"""Golden tests for the Engineering State Engine (v2.2.0). Progress is derived from evidence,
never stored as a number, never set by the LLM."""
import sqlite3

import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.engines.state import project_status


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _boot(conn):
    repo.create_project(conn, "boot", "bootloader", "STM32F103-BluePill")
    return repo.get_project(conn, "boot")


def test_item_not_started_when_rule_unknown(tmp_path):
    conn = _seeded(tmp_path)
    s = project_status(conn, _boot(conn))
    vt = next(i for i in s["items"] if i["item_key"] == "vector_table_placement")
    assert vt["status"] == "NOT_STARTED" and vt["why_it_matters"]   # UNKNOWN rule -> not started
    assert s["complete"] == 0 and s["percent"] == 0


def test_item_complete_on_validation_pass(tmp_path):
    conn = _seeded(tmp_path)
    p = _boot(conn)
    repo.set_input(conn, p["id"], "vector_table_addr", "0x08000000", confidence="HIGH")
    s = project_status(conn, p)
    vt = next(i for i in s["items"] if i["item_key"] == "vector_table_placement")
    assert vt["status"] == "COMPLETE" and vt["verified_by"] == "VALIDATION_ENGINE"
    assert "PASS" in vt["evidence"]
    assert s["complete"] >= 1                                       # derived, not stored


def test_user_confirmation_completes_item(tmp_path):
    conn = _seeded(tmp_path)
    p = _boot(conn)
    repo.set_checklist_status(conn, p["id"], "clock_init_sequence", "done", "confirmed by engineer")
    s = project_status(conn, p)
    ci = next(i for i in s["items"] if i["item_key"] == "clock_init_sequence")
    assert ci["status"] == "COMPLETE" and ci["verified_by"] == "USER"


def test_progress_percent_is_derived(tmp_path):
    conn = _seeded(tmp_path)
    p = _boot(conn)
    s0 = project_status(conn, p)
    repo.set_checklist_status(conn, p["id"], "clock_init_sequence", "done", "x")
    s1 = project_status(conn, p)
    assert s1["complete"] == s0["complete"] + 1
    assert s1["percent"] == round(100 * s1["complete"] / s1["total"])


def test_llm_cannot_set_progress_db_constraint(tmp_path):
    # The schema only permits the three deterministic sources; an 'LLM' value is rejected.
    conn = _seeded(tmp_path)
    p = _boot(conn)
    project_status(conn, p)                       # populate a row
    row = conn.execute("SELECT template_item_id FROM project_progress LIMIT 1").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_progress(project_id,template_item_id,status,evidence,"
            "verified_by,updated_at) VALUES (?,?,?,?,?,?)",
            (p["id"], row["template_item_id"], "COMPLETE", "made up", "LLM", "now"))


def test_next_recommended_is_first_incomplete(tmp_path):
    conn = _seeded(tmp_path)
    s = project_status(conn, _boot(conn))
    assert s["next"] is not None and s["next"]["why_it_matters"]
    # it's the first item that isn't complete
    first_incomplete = next(i for i in s["items"] if i["status"] != "COMPLETE")
    assert s["next"]["title"] == first_incomplete["title"]


# --- Piece 2: think-before-code uses board-specific facts from SQLite -------

def test_think_before_code_shows_board_specific_pin(tmp_path):
    from eaedk import mentor
    conn = _seeded(tmp_path)
    items = mentor.think_before_code(conn, "STM32F103-BluePill", "bare_metal_app")
    blob = " ".join(i["hint"] for i in items)
    assert "PC13" in blob and "RCC->APB2ENR" in blob          # concrete, from board_blink_facts
    # a board without seeded blink facts falls back to the generic prompt (no crash)
    items2 = mentor.think_before_code(conn, "STM32H743", "bare_metal_app")
    assert any("LED" in i["question"] for i in items2)


# --- Piece 4: dual-path START_HERE (Wokwi first) ---------------------------

def test_start_here_shows_wokwi_path_first_for_supported_board(tmp_path):
    from eaedk import repo
    from eaedk.engines.output import export_project
    conn = _seeded(tmp_path)
    repo.create_project(conn, "blink", "bare_metal_app", "STM32F103-BluePill")
    out = tmp_path / "fw"
    export_project(conn, repo.get_project(conn, "blink"), str(out), wokwi=True)
    sh = (out / "START_HERE.md").read_text()
    assert "PATH A — Simulate" in sh and "PATH B — Physical board" in sh
    assert sh.index("PATH A") < sh.index("PATH B")            # Wokwi path first
    assert "Build it" in sh and "learning path" in sh.lower() # preserved


def test_esp32_export_has_start_here_dual_path(tmp_path):
    # ESP32 (Xtensa) must give a Wokwi-only beginner a START_HERE + scaffold, like AVR/ARM.
    from eaedk import repo
    from eaedk.engines.output import export_project
    conn = _seeded(tmp_path)
    repo.create_project(conn, "e", "bare_metal_app", "ESP32-DevKitC")
    out = tmp_path / "fw"
    export_project(conn, repo.get_project(conn, "e"), str(out), wokwi=True)
    assert (out / "START_HERE.md").exists() and (out / "src" / "main.c").exists()
    sh = (out / "START_HERE.md").read_text()
    assert "PATH A — Simulate" in sh and sh.index("PATH A") < sh.index("PATH B")
    assert "idf.py" in sh and "GPIO2" in (out / "src" / "main.c").read_text()


def test_start_here_single_path_for_unsupported_board(tmp_path):
    from eaedk import repo
    from eaedk.engines.output import export_project
    conn = _seeded(tmp_path)
    repo.create_project(conn, "h7", "bare_metal_app", "STM32H743")   # not a Wokwi board
    out = tmp_path / "fw"
    export_project(conn, repo.get_project(conn, "h7"), str(out))
    sh = (out / "START_HERE.md").read_text()
    assert "PATH A" not in sh and "Wokwi simulation isn't available" in sh
