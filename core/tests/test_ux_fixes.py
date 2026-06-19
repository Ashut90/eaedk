"""Regression tests for the persona black-box UX fixes:
  #1 DECLINE no longer over-fires on background / chip-family words
  #3 the off-topic UART-clock closing question is gone from decision/concept answers
  #4 a basic concept (microcontroller vs microprocessor) is answered, not refused
  #2 a proof-path the user has stopped engaging with gives up (no indefinite bleed)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.mentor_llm import decide_purpose, mentor_chat
from eaedk import problem_patterns as pp

BOARD = "Nucleo-F411RE"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    return conn


def _purpose(conn, text):
    return decide_purpose(conn, BOARD, text, {}, [{"role": "user", "content": text}]).purpose


# ── #1 DECLINE precision ────────────────────────────────────────────────────────────────────

def test_background_and_family_words_do_not_decline(tmp_path):
    conn = _seeded(tmp_path)
    # Background tech / role words named as context must NOT be declined as out-of-scope hardware.
    assert _purpose(conn, "I know C++ well, what's different about embedded firmware?") != "DECLINE_OUT_OF_SCOPE"
    assert _purpose(conn, "I just moved from software QA to a firmware role, where do I focus?") != "DECLINE_OUT_OF_SCOPE"
    # A supported chip FAMILY must not be declined (an STM32F4 board is seeded).
    assert _purpose(conn, "My team uses an STM32F4 board, how do I get oriented?") != "DECLINE_OUT_OF_SCOPE"


def test_genuinely_out_of_scope_still_declines(tmp_path):
    conn = _seeded(tmp_path)
    assert _purpose(conn, "How can I use NVIDIA Jetson?") == "DECLINE_OUT_OF_SCOPE"
    assert _purpose(conn, "where to start in NVIDIA jetson?") == "DECLINE_OUT_OF_SCOPE"


# ── #4 basic concept answered ────────────────────────────────────────────────────────────────

def test_microcontroller_vs_microprocessor_is_answered(tmp_path):
    conn = _seeded(tmp_path)
    q = "What's the difference between a microcontroller and microprocessor?"
    assert _purpose(conn, q) == "ANSWER_NOW"
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": q}], use_llm=False).lower()
    assert "microcontroller" in out and "microprocessor" in out
    assert "i don't have enough to answer" not in out          # not the old refusal


# ── #3 off-topic footer removed from decision answers ────────────────────────────────────────

def test_decision_answer_drops_the_uart_clock_question(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, "STM32F103-BluePill",
                      [{"role": "user", "content": "should I use interrupts or polling?"}], use_llm=False)
    assert "which clock your UART runs on" not in out          # the off-topic canned footer is gone
    assert out.rstrip().endswith("?")                          # still closes with a question


# ── #2 proof-path disengagement (no indefinite bleed) ────────────────────────────────────────

def _state(*turns):
    return pp.resolve([{"role": "user", "content": t} for t in turns])


def test_proof_path_gives_up_after_two_ignored_turns():
    fault = "my device randomly resets under load"
    # One unrelated turn: the proof path still (reasonably) awaits its evidence.
    assert _state(fault, "explain this to my manager").matched
    # Two unrelated turns with no evidence: it gives up so the new question can route fresh.
    assert not _state(fault, "explain this to my manager", "is shipping fast a bad idea?").matched


def test_real_evidence_keeps_the_proof_path_alive():
    # Providing evidence advances the tree — disengagement must NOT trigger.
    st = _state("my device randomly resets under load",
                "the reset-cause flag says brown-out",
                "adding a bulk cap fixed it")
    assert st.matched and st.node.id == "decoupling_confirmed"
