"""Golden tests for the Web UI (v2.0.0). The routes are thin wrappers over the same engine the
CLI uses; these assert each page's API returns correct engine data and that errors come back as a
plain-English {error, next} envelope — never a traceback or bare HTTP code.

Skipped automatically if the optional `[web]` extra (FastAPI) isn't installed.
"""
import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient   # noqa: E402

from eaedk.store.db import connect           # noqa: E402
from eaedk.store.migrate import migrate      # noqa: E402
from eaedk.seed import seed_all              # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "web.db"
    conn = connect(str(db))
    migrate(conn)
    seed_all(conn, force=True)
    # The server reads default_db_path() (which honours EAEDK_DB) per request.
    monkeypatch.setenv("EAEDK_DB", str(db))
    from eaedk.web.server import app
    return TestClient(app)


# --- Page 1: Board Explorer ------------------------------------------------

def test_boards_list_has_geometry_and_light(client):
    boards = client.get("/api/boards").json()["boards"]
    assert len(boards) == 14
    bp = next(b for b in boards if b["name"] == "STM32F103-BluePill")
    assert bp["flash_bytes"] == 65536 and bp["ram_bytes"] == 20480
    assert bp["light"] == "GREEN"                      # HIGH confidence + full geometry


def test_board_detail_has_caps_and_path(client):
    d = client.get("/api/boards/STM32F103-BluePill").json()
    assert "uart" in {c["capability"] for c in d["capabilities"]}
    assert d["learning_path"][0]["title"] == "Blink an LED"


def test_board_not_found_is_friendly(client):
    r = client.get("/api/boards/NoSuchBoard").json()
    assert "error" in r and "next" in r and "traceback" not in str(r).lower()


# --- Page 2: Project Setup -------------------------------------------------

def test_goals_have_plain_labels(client):
    goals = client.get("/api/goals").json()["goals"]
    first = goals[0]
    assert first["value"] == "bare_metal_app" and "start here" in first["label"].lower()


def test_create_project_returns_feasibility_light(client):
    r = client.post("/api/projects", json={"name": "blink", "board": "STM32F103-BluePill",
                                           "goal": "bare_metal_app"}).json()
    assert r["light"] == "GREEN" and r["label"] == "Feasible"
    assert "blink" in {p["name"] for p in client.get("/api/projects").json()["projects"]}


def test_duplicate_and_empty_name_are_friendly(client):
    client.post("/api/projects", json={"name": "p", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    dup = client.post("/api/projects", json={"name": "p", "board": "STM32F103-BluePill",
                                             "goal": "bare_metal_app"}).json()
    assert "already exists" in dup["error"]
    empty = client.post("/api/projects", json={"name": "", "board": "X", "goal": "y"}).json()
    assert "name" in empty["error"].lower() and "next" in empty


# --- Page 3: Validate ------------------------------------------------------

def test_validate_returns_checks_with_teach(client):
    client.post("/api/projects", json={"name": "v", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    r = client.get("/api/validate/v").json()
    assert any(c["status"] == "UNKNOWN" and c["teach"] for c in r["checks"])
    assert any(c["gating"] for c in r["checks"]) and any(not c["gating"] for c in r["checks"])


def test_validate_unknown_project_friendly(client):
    r = client.get("/api/validate/nope").json()
    assert "Project not found" in r["error"] and "next" in r


# --- Page 4: Export --------------------------------------------------------

def test_export_roundtrip_files_view_zip(client):
    client.post("/api/projects", json={"name": "e", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    r = client.post("/api/export/e").json()
    assert "START_HERE.md" in r["files"] and "src/main.c" in r["files"]
    f = client.get("/api/export/e/file", params={"path": "START_HERE.md"}).json()
    assert "START HERE" in f["content"]
    # path traversal is blocked
    trav = client.get("/api/export/e/file", params={"path": "../../../etc/passwd"}).json()
    assert "error" in trav
    z = client.get("/api/export/e/download")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and z.content


def test_export_avr_board_has_start_here(client):
    # The v1.9.2 AVR fix flows through the web export too.
    client.post("/api/projects", json={"name": "uno", "board": "Arduino-Uno",
                                       "goal": "bare_metal_app"})
    r = client.post("/api/export/uno").json()
    assert "START_HERE.md" in r["files"]
    f = client.get("/api/export/uno/file", params={"path": "START_HERE.md"}).json()
    assert "avr-gcc" in f["content"] and "arm-none-eabi" not in f["content"]


# --- Page 5: Log Analyzer --------------------------------------------------

def test_log_hardfault_matches(client):
    log = "app: running\n[FAULT] HardFault_Handler: forced exception\nHFSR=0x40000000 PC=0x08000abc\n"
    r = client.post("/api/logs/analyze", json={"text": log, "project": None}).json()
    assert r["format"] == "mcu" and r["matches"]
    assert "HardFault" in r["matches"][0]["cause"] and r["matches"][0]["severity"] == "HIGH"


def test_log_empty_is_friendly(client):
    r = client.post("/api/logs/analyze", json={"text": "   "}).json()
    assert "no log" in r["error"].lower() and "next" in r


# --- Page 6: Mentor --------------------------------------------------------

def test_mentor_ask_offline_answer(client):
    r = client.post("/api/mentor/ask", json={"board": "STM32F103-BluePill",
                                             "question": "where do I start?", "use_llm": False}).json()
    assert "STM32F103-BluePill" in r["answer"]


def test_mentor_ask_empty_and_bad_board_friendly(client):
    e1 = client.post("/api/mentor/ask", json={"board": "STM32F103-BluePill", "question": ""}).json()
    assert "question" in e1["error"].lower()
    e2 = client.post("/api/mentor/ask", json={"board": "Nope", "question": "hi"}).json()
    assert "Board not found" in e2["error"]


# --- static pages ----------------------------------------------------------

def test_all_pages_served(client):
    assert client.get("/", follow_redirects=False).status_code in (302, 307)
    for page in ("boards", "setup", "validate", "export", "studio", "logs", "mentor"):
        assert client.get(f"/static/{page}.html").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


# --- v2.1.0 additions ------------------------------------------------------

def test_board_detail_includes_driver_path_for_linux_board(client):
    d = client.get("/api/boards/BeagleBone-Black").json()
    assert d["driver_path"] and d["driver_path"][0]["title"] == "Character device driver"
    bp = client.get("/api/boards/STM32F103-BluePill").json()
    assert bp["driver_path"] == []


def test_web_export_includes_wokwi_for_supported_board(client):
    client.post("/api/projects", json={"name": "w", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    r = client.post("/api/export/w").json()
    assert r["wokwi_supported"] is True
    assert "wokwi/diagram.json" in r["wokwi_files"] and "wokwi/wokwi.toml" in r["wokwi_files"]
    assert r["compile_command"]


def test_web_export_wokwi_unsupported_board(client):
    client.post("/api/projects", json={"name": "bbb", "board": "BeagleBone-Black",
                                       "goal": "linux"})
    r = client.post("/api/export/bbb").json()
    assert r["wokwi_supported"] is False
    assert "STM32F103-BluePill" in " ".join(r["wokwi_supported_list"])


def test_studio_returns_template_and_checklist(client):
    client.post("/api/projects", json={"name": "s", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    d = client.get("/api/studio/s").json()
    assert d["template"] and d["checklist"]
    assert any("RCC" in c["hint"] for c in d["checklist"])


def test_studio_review_returns_confirmed_advisory(client):
    client.post("/api/projects", json={"name": "s2", "board": "STM32F103-BluePill",
                                       "goal": "bare_metal_app"})
    r = client.post("/api/studio/s2/review", json={"project": "s2", "code": "int main(){}"}).json()
    assert "confirmed" in r and "advisory" in r       # keys always present


def test_mentor_chat_route(client):
    r = client.post("/api/mentor/chat", json={"board": "STM32F103-BluePill",
        "messages": [{"role": "user", "content": "where do I start?"}], "use_llm": False}).json()
    assert "Try this:" in r["answer"]
    empty = client.post("/api/mentor/chat", json={"board": "STM32F103-BluePill",
                                                  "messages": []}).json()
    assert "error" in empty and "next" in empty


# --- v2.2.0: State Engine progress + mark-complete + progress-aware chat -----

def test_progress_api_derives_from_evidence(client):
    client.post("/api/projects", json={"name": "pp", "board": "STM32F103-BluePill",
                                       "goal": "bootloader"})
    s = client.get("/api/progress/pp").json()
    assert s["total"] > 0 and s["percent"] == 0          # nothing proven yet
    assert all(i["light"] in ("GREEN", "YELLOW", "GREY") for i in s["items"])
    assert s["next"] and s["next"]["why_it_matters"]


def test_studio_mark_complete_feeds_state_engine(client):
    client.post("/api/projects", json={"name": "mc", "board": "STM32F103-BluePill",
                                       "goal": "bootloader"})
    rev = client.post("/api/studio/mc/review", json={"project": "mc", "code": "int main(){}"}).json()
    assert "can_complete" in rev
    if rev["can_complete"]:
        key = rev["can_complete"][0]["item_key"]
        before = client.get("/api/progress/mc").json()["complete"]
        done = client.post("/api/studio/mc/complete",
                           json={"project": "mc", "item_key": key}).json()
        assert done["complete"] == before + 1            # USER path completion recorded


def test_chat_progress_question_reads_state_engine(client):
    client.post("/api/projects", json={"name": "cp", "board": "STM32F103-BluePill",
                                       "goal": "bootloader"})
    r = client.post("/api/mentor/chat", json={"board": "STM32F103-BluePill", "project": "cp",
        "messages": [{"role": "user", "content": "how am I doing?"}], "use_llm": False}).json()
    assert "/" in r["answer"] and "next task" in r["answer"].lower()


# --- v2.3.0: datasheet intelligence + query --------------------------------

def test_ask_route_confidence(client):
    hi = client.post("/api/ask", json={"board": "STM32F103-BluePill",
                                       "question": "what is the flash size?"}).json()
    assert hi["confidence"] == "HIGH" and "64KB" in hi["answer"]
    unk = client.post("/api/ask", json={"board": "STM32F103-BluePill",
                                        "question": "boot pins?"}).json()
    assert unk["confidence"] == "UNKNOWN" and "BOOT0" in unk["answer"]


def test_similar_route(client):
    r = client.get("/api/similar/STM32F103-BluePill").json()
    assert r["similar"][0]["name"] == "Nucleo-F103RB"


def test_ingest_text_returns_report(client):
    text = ("3.2 Memory map. The Flash memory base address is 0x08000000 with 512 Kbytes of "
            "embedded Flash. SRAM base is 0x20000000 with 128 Kbytes of SRAM. Up to 168 MHz.")
    r = client.post("/api/ingest", json={"board": "Nucleo-F411RE", "text": text}).json()
    rep = r["report"]
    assert {"found", "missing", "priority", "risks", "similar", "can", "cannot", "next_step"} <= set(rep)
    assert any(m["label"].startswith("Boot") for m in rep["missing"])


def test_ingest_page_served(client):
    assert client.get("/static/ingest.html").status_code == 200


# --- v2.3.1: unknown-board on-ramp from the Datasheet tab -------------------

def test_arch_choices_route(client):
    labels = [c["label"] for c in client.get("/api/arch-choices").json()["choices"]]
    assert "Cortex-M4" in labels and "Xtensa-LX6" in labels and "AVR" in labels


def test_ingest_new_board_onramp(client):
    text = ("3.2 Memory mapping. The Flash memory base address is 0x08000000 with 512 Kbytes of "
            "Flash. The SRAM base address is 0x20000000 with 64 Kbytes of SRAM. Up to 72 MHz.")
    r = client.post("/api/ingest", json={"new_board": "GD32F303", "arch": "Cortex-M4",
                                         "text": text}).json()
    assert r["board"] == "GD32F303" and "report" in r           # auto-created, no dead end
    assert {"flash_base", "ram_base"} <= {f["key"] for f in r["report"]["found"]}
    # missing architecture for a new board -> friendly error, not a crash
    e = client.post("/api/ingest", json={"new_board": "X", "text": text}).json()
    assert "architecture" in e["error"].lower() and "next" in e


# --- Contextual chat box (v2.4.0) — one route, four page contexts ----------

def test_chat_datasheet_states_confidence(client):
    r = client.post("/api/chat", json={"page_type": "datasheet", "use_llm": False,
                    "board_name": "STM32F103-BluePill", "user_message": "how much RAM does it have?"})
    d = r.json()
    assert "error" not in d and d.get("confidence")     # every datasheet hardware answer has a confidence

def test_chat_boards_returns_answer_offline(client):
    r = client.post("/api/chat", json={"page_type": "boards", "use_llm": False,
                    "board_name": "STM32F103-BluePill", "user_message": "where do I start?"})
    d = r.json()
    assert "error" not in d and d.get("answer")         # offline backbone, never a dead end

def test_chat_studio_returns_answer(client):
    r = client.post("/api/chat", json={"page_type": "studio", "use_llm": False, "board_name": "STM32F103-BluePill",
                    "current_code": "int main(){}", "user_message": "my code does nothing"})
    assert r.json().get("answer")

def test_chat_empty_message_is_friendly_error(client):
    r = client.post("/api/chat", json={"page_type": "boards",
                    "board_name": "STM32F103-BluePill", "user_message": "   "})
    assert "error" in r.json()
