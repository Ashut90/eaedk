"""Bootloader / firmware-update proof-path pattern (project type goal_type=bootloader).
Brutal coverage: branches, a wide match/no-match boundary, cross-pattern non-collision across
the whole 7-pattern library, full node reachability, paraphrase invariance, navigator integration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import problem_patterns as pp
from eaedk import navigator as nav
from eaedk.mentor_llm import decide_purpose

BOARD = "STM32F103-BluePill"


def _state(*user_turns):
    return pp.resolve([{"role": "user", "content": t} for t in user_turns])


def _name(*turns):
    st = _state(*turns)
    return st.pattern.name if st.matched else None


# ── MATCH boundary ──────────────────────────────────────────────────────────────────────

def test_matches_at_entry():
    st = _state("my bootloader doesn't jump to the application")
    assert st.matched and st.pattern.name == "bootloader"
    assert st.node.id == "jump_check"


@pytest.mark.parametrize("msg", [
    "my bootloader won't jump to the app",
    "the app never starts, it stays in the bootloader",
    "stuck in the bootloader, the app doesn't boot",
    "my OTA update bricked the device",
    "after DFU the app doesn't run",
    "bootloader runs but the firmware update won't flash",
    "the second stage bootloader hangs and never jumps",
    "u-boot doesn't boot my image",
])
def test_many_fault_phrasings_all_match(msg):
    assert _name(msg) == "bootloader", msg


@pytest.mark.parametrize("msg", [
    "what is a bootloader?",
    "should I use a failsafe bootloader or not?",
    "how does a bootloader hand off to the application?",
    "explain VTOR to me",
    "do I need a bootloader for OTA updates?",
])
def test_concept_and_decision_questions_do_NOT_match(msg):
    assert _name(msg) is None, msg


# ── Decision tree — every node reachable ────────────────────────────────────────────────────

def test_jump_code_branch():
    st = _state("my bootloader doesn't jump to the app",
                "the app never starts, it's stuck in the bootloader",
                "but the vector pair at the app base looks valid")
    assert st.node.id == "jump_code"


def test_flash_program_branch():
    st = _state("my bootloader won't boot the application",
                "the app never starts",
                "there's garbage at the app base, all 0xff")
    assert st.node.id == "flash_program"


def test_app_vector_branch():
    st = _state("my bootloader crashes the app",
                "the app starts then hardfaults right after the jump")
    assert st.node.id == "app_vector"


def test_all_nodes_reachable():
    reached = {
        _state("bootloader doesn't jump to the app").node.id,                                  # entry
        _state("bootloader won't jump", "app never starts").node.id,                            # handoff_check
        _state("bootloader won't jump", "app never starts", "valid vector pair").node.id,       # jump_code
        _state("bootloader won't jump", "app never starts", "garbage at the app base").node.id, # flash_program
        _state("bootloader crashes the app", "app starts then crashes").node.id,                # app_vector
    }
    assert {"jump_check", "handoff_check", "jump_code", "flash_program", "app_vector"} <= reached


# ── Paraphrase invariance ───────────────────────────────────────────────────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("my bootloader doesn't jump to the app", "app never starts", "vector pair looks valid")
    b = _state("the app won't boot from my bootloader", "doesn't reach the app",
               "the image is there, valid vector")
    assert a.node.id == b.node.id == "jump_code"


# ── BRUTAL: cross-pattern non-collision across the whole library ────────────────────────────

@pytest.mark.parametrize("msg,expected", [
    ("my uart prints nothing, no output",                       "uart_bringup"),
    ("my i2c sensor isn't working, no ack",                     "i2c_bringup"),
    ("my spi reads all 0xff, nothing back",                     "spi_bringup"),
    ("my board keeps resetting under load when wifi transmits", "power_reset"),
    ("my code keeps crashing into the hardfault handler",       "hardfault"),
    ("my freertos scheduler hangs, no task runs",               "rtos_bringup"),
    ("my bootloader doesn't jump to the app",                   "bootloader"),
])
def test_each_canonical_symptom_hits_its_own_pattern(msg, expected):
    assert _name(msg) == expected, f"{msg!r} hit the wrong pattern"


# ── Navigator integration + safety ──────────────────────────────────────────────────────────

def test_navigator_routes_bootloader_failure_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my bootloader won't jump to the application, stuck in the bootloader"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "bootloader"


def test_render_has_no_invented_board_facts():
    st = _state("my bootloader doesn't jump to the app", "app never starts", "valid vector pair")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
