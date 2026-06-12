"""Golden tests for v2.1.0 — the four 'mentor complete' pieces. Deterministic (no live LLM):
the chat is tested through its offline backbone, which must always carry an answer + a board-tied
'Try this' + a follow-up question.
"""
import json

import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo, mentor
from eaedk.mentor_llm import mentor_chat
from eaedk.engines.output.wokwi import wokwi_files, supported_boards
from eaedk.engines.output import export_project


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- Piece 1: driver learning path ----------------------------------------

@pytest.mark.parametrize("board", ["BeagleBone-Black", "Raspberry-Pi-4", "STM32MP157",
                                   "i.MX8M-Mini-EVK"])
def test_driver_path_present_for_linux_boards(tmp_path, board):
    conn = _seeded(tmp_path)
    dp = mentor.driver_path(conn, board)
    assert [s["key"] for s in dp] == ["driver_chardev", "driver_platform", "driver_i2c",
                                      "driver_spi", "driver_irq", "driver_dma"]
    # each step carries kernel functions + a build exercise
    assert any("Kernel functions:" in b for b in dp[0]["before_you_start"])


def test_driver_path_absent_for_bare_metal_boards(tmp_path):
    conn = _seeded(tmp_path)
    assert mentor.driver_path(conn, "STM32F103-BluePill") == []
    assert mentor.driver_path(conn, "Arduino-Uno") == []


def test_driver_steps_dont_pollute_bare_metal_path(tmp_path):
    conn = _seeded(tmp_path)
    caps = repo.board_capability_names(conn, "STM32F103-BluePill")
    path = mentor.learning_path_for(conn, caps)
    dropped = mentor.dropped_steps_for(conn, set())
    assert not any("driver" in s["key"] for s in path)
    assert not any("driver" in s["title"].lower() for s in dropped)


def test_render_board_mentor_driver_section(tmp_path):
    conn = _seeded(tmp_path)
    assert "Driver development path" in mentor.render_board_mentor(conn, "BeagleBone-Black")
    assert "Driver development path" not in mentor.render_board_mentor(conn, "STM32F103-BluePill")


# --- Piece 2: Wokwi files ---------------------------------------------------

def test_wokwi_supported_board_diagram_and_toml(tmp_path):
    conn = _seeded(tmp_path)
    _b, soc = repo.load_board(conn, "STM32F103-BluePill")
    files = wokwi_files("STM32F103-BluePill", soc, "blink")
    assert "wokwi/diagram.json" in files and "wokwi/wokwi.toml" in files
    diagram = json.loads(files["wokwi/diagram.json"])
    assert diagram["parts"][0]["type"] == "board-blue-pill"        # from the board, not hardcoded
    assert "blink.elf" in files["wokwi/wokwi.toml"]


def test_wokwi_firmware_extension_per_arch(tmp_path):
    conn = _seeded(tmp_path)
    _b, soc_avr = repo.load_board(conn, "Arduino-Uno")
    assert "blink.hex" in wokwi_files("Arduino-Uno", soc_avr, "blink")["wokwi/wokwi.toml"]
    _b, soc_rp = repo.load_board(conn, "Raspberry-Pi-Pico")
    assert "blink.uf2" in wokwi_files("Raspberry-Pi-Pico", soc_rp, "blink")["wokwi/wokwi.toml"]


def test_wokwi_unsupported_board_gets_clear_message(tmp_path):
    conn = _seeded(tmp_path)
    _b, soc = repo.load_board(conn, "BeagleBone-Black")
    files = wokwi_files("BeagleBone-Black", soc, "blink")
    assert list(files) == ["wokwi/README.txt"]
    txt = files["wokwi/README.txt"]
    assert "not available" in txt and "STM32F103-BluePill" in txt


def test_export_wokwi_flag_additive(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "blink", "bare_metal_app", "STM32F103-BluePill")
    p = repo.get_project(conn, "blink")
    # default: no wokwi dir
    out1 = tmp_path / "o1"
    r1 = export_project(conn, p, str(out1))
    assert not (out1 / "wokwi").exists() and r1.written
    # with the flag: wokwi files appear, normal files unchanged
    out2 = tmp_path / "o2"
    export_project(conn, p, str(out2), wokwi=True)
    assert (out2 / "wokwi" / "diagram.json").exists()
    assert (out2 / "src" / "main.c").exists()       # normal export still present


# --- Piece 3: think-before-code checklist ----------------------------------

def test_checklist_is_board_family_aware(tmp_path):
    conn = _seeded(tmp_path)
    stm = mentor.think_before_code(conn, "STM32F103-BluePill", "bare_metal_app")
    assert any("RCC" in c["hint"] for c in stm)            # STM32 clock-enable concern
    assert any("LED" in c["question"] for c in stm)
    avr = mentor.think_before_code(conn, "Arduino-Uno", "bare_metal_app")
    assert any("F_CPU" in c["question"] or "fuse" in c["hint"].lower() for c in avr)
    # different families -> different guidance
    assert any("RCC" in c["hint"] for c in stm) and not any("RCC" in c["hint"] for c in avr)


def test_checklist_uses_verified_flash_base(tmp_path):
    conn = _seeded(tmp_path)
    items = mentor.think_before_code(conn, "STM32F103-BluePill", "bare_metal_app")
    # the vector-table question cites the board's real flash base (a SQLite fact), not a guess
    assert any("0x08000000" in c["hint"] for c in items)


# --- Piece 4: Code Studio surfaces the existing Actor-Critic ----------------

def test_run_actor_critic_accepts_code_override(tmp_path):
    # additive `code` param: passing it must not break the offline path (no LLM -> available False)
    from eaedk.actor_critic import run_actor_critic
    from eaedk.llm.gateway import Gateway

    class _Down:
        model = "x"
        def available(self): return False
        def generate(self, s, p): return ""
    conn = _seeded(tmp_path)
    repo.create_project(conn, "p", "bare_metal_app", "STM32F103-BluePill")
    res = run_actor_critic(conn, repo.get_project(conn, "p"),
                           gateway=Gateway(provider=_Down()), code="int main(){}")
    assert res.available is False                    # graceful when the model is down


def test_grounded_confirmations_catch_real_fault(tmp_path):
    from eaedk.actor_critic import grounded_confirmations
    conn = _seeded(tmp_path)
    repo.create_project(conn, "p", "bare_metal_app", "Raspberry-Pi-Pico")  # RAM 270336
    pid = repo.get_project(conn, "p")["id"]
    for k, v in {"stack_size": "524288", "heap_size": "0", "static_size": "0"}.items():
        repo.set_input(conn, pid, k, v, confidence="HIGH")
    g = grounded_confirmations(conn, repo.get_project(conn, "p"))
    assert any(c["kind"] == "RAM_BUDGET" for c in g)   # deterministic CONFIRMED, no LLM needed


# --- Piece 5: conversational mentor (offline backbone) ----------------------

def test_chat_backbone_always_has_answer_try_and_question(tmp_path):
    conn = _seeded(tmp_path)
    a = mentor_chat(conn, "STM32F103-BluePill",
                    [{"role": "user", "content": "where do I start?"}], use_llm=False)
    assert "Try this:" in a
    assert a.rstrip().endswith("?") or "Question:" in a   # always ends with an action
    assert len(a) > 40


def test_chat_uses_concept_anchor(tmp_path):
    conn = _seeded(tmp_path)
    a = mentor_chat(conn, "STM32F103-BluePill",
                    [{"role": "user", "content": "what is a hardfault?"}], use_llm=False)
    assert "unrecoverably wrong" in a                 # the seeded HardFault anchor
    assert "Try this:" in a


def test_chat_unknown_board_is_friendly(tmp_path):
    conn = _seeded(tmp_path)
    a = mentor_chat(conn, "NoSuchBoard", [{"role": "user", "content": "hi"}], use_llm=False)
    assert "can't find" in a.lower() and "Try this:" in a


def test_chat_avr_board_gets_avr_try_this(tmp_path):
    conn = _seeded(tmp_path)
    a = mentor_chat(conn, "Arduino-Uno",
                    [{"role": "user", "content": "help"}], use_llm=False)
    assert "F_CPU" in a                               # board-family-appropriate experiment
