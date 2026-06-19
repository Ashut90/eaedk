"""Edge-ML inference proof-path pattern (project type goal_type=ml_inference). Brutal coverage:
branches, boundary, paraphrase invariance, navigator integration, plus the FULL 9-pattern
cross-collision check (each canonical symptom hits its own pattern)."""
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


def _state(*turns):
    return pp.resolve([{"role": "user", "content": t} for t in turns])


def _name(*turns):
    st = _state(*turns)
    return st.pattern.name if st.matched else None


def test_matches_at_entry():
    st = _state("my tflite model doesn't fit on the MCU")
    assert st.matched and st.pattern.name == "ml_inference"
    assert st.node.id == "fit_check"


@pytest.mark.parametrize("msg", [
    "my tflite micro model is too big for flash",
    "my tinyml inference gives wrong predictions",
    "my neural network on the MCU is too slow",
    "the tensor arena fails to allocate, out of memory",
    "my edge ml model outputs garbage on device",
])
def test_fault_phrasings_match(msg):
    assert _name(msg) == "ml_inference", msg


@pytest.mark.parametrize("msg", [
    "what is tinyml?",
    "should I quantize my model to int8?",
    "how does tflite micro work?",
])
def test_concept_and_decision_questions_do_NOT_match(msg):
    assert _name(msg) is None, msg


def test_size_fault_branch():
    st = _state("my tflite model isn't working", "it doesn't fit, the tensor arena alloc fails")
    assert st.node.id == "size_fault"


def test_accuracy_fault_branch():
    st = _state("my tinyml model isn't working",
                "the model loads and runs fine",
                "but the predictions are wrong and don't match the host")
    assert st.node.id == "accuracy_fault"


def test_latency_fault_branch():
    st = _state("my neural network inference isn't working",
                "it runs but it's way too slow",
                "the latency is too high for real-time")  # reinforces 'slow'
    assert st.node.id == "latency_fault"


def test_paraphrases_reach_same_node():
    a = _state("my tflite model isn't working", "it runs but gives wrong output")
    b = _state("my tinyml inference isn't working", "model fits and runs, but predictions are wrong")
    assert a.node.id == b.node.id == "accuracy_fault"


def test_navigator_routes_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my tflite model is too big to fit in flash"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH and route.proof_state.pattern.name == "ml_inference"


def test_render_has_no_invented_board_facts():
    st = _state("my tflite model doesn't fit", "the tensor arena alloc fails, out of memory")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations


# ── BRUTAL: full 9-pattern cross-collision — each canonical symptom hits its own pattern ────
@pytest.mark.parametrize("msg,expected", [
    ("my uart prints nothing, no output",                       "uart_bringup"),
    ("my i2c sensor isn't working, no ack",                     "i2c_bringup"),
    ("my spi reads all 0xff, nothing back",                     "spi_bringup"),
    ("my board keeps resetting under load when wifi transmits", "power_reset"),
    ("my code keeps crashing into the hardfault handler",       "hardfault"),
    ("my freertos scheduler hangs, no task runs",               "rtos_bringup"),
    ("my bootloader doesn't jump to the app",                   "bootloader"),
    ("my kernel driver won't load, insmod fails",               "driver"),
    ("my tflite model doesn't fit on the MCU",                  "ml_inference"),
])
def test_each_canonical_symptom_hits_its_own_pattern(msg, expected):
    assert _name(msg) == expected, f"{msg!r} hit the wrong pattern"
