"""Unexpected-reset / brown-out proof-path pattern — tree logic, match boundaries, paraphrase
invariance, and navigator integration. Mirrors the UART/I2C/SPI pattern test discipline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import problem_patterns as pp
from eaedk import navigator as nav
from eaedk.mentor_llm import decide_purpose

BOARD = "STM32F103-BluePill"


def _state(*user_turns):
    return pp.resolve([{"role": "user", "content": t} for t in user_turns])


# ── MATCH boundary ──────────────────────────────────────────────────────────────────────

def test_reset_failure_matches_at_entry():
    st = _state("my board keeps resetting randomly")
    assert st.matched and st.pattern.name == "power_reset"
    assert st.node.id == "reset_cause_check"


def test_design_question_does_NOT_trigger_proof_path():
    # "should I use a watchdog?" is a design question, not an involuntary reset.
    assert not _state("should I use a watchdog?").matched
    assert not _state("how do I reset the chip in software?").matched


# ── Decision tree: the reset-cause split ───────────────────────────────────────────────────

def test_brownout_then_cap_helps_reaches_decoupling():
    st = _state("my board keeps rebooting",
                "it resets under load when wifi transmits",
                "adding a bulk cap fixed it")
    assert st.node.id == "decoupling_confirmed"


def test_brownout_but_stiff_supply_no_change_reaches_supply_or_short():
    st = _state("board restarts on its own",
                "the 3v3 rail sags under load",
                "still resets even with a bench supply")
    assert st.node.id == "supply_or_short"


def test_watchdog_branch():
    st = _state("my board keeps resetting",
                "the watchdog keeps firing during a flash write")
    assert st.node.id == "watchdog_starve"


def test_reset_pin_branch():
    st = _state("the mcu reboots randomly", "there's noise on the reset pin")
    assert st.node.id == "reset_pin"


# ── Paraphrase invariance ───────────────────────────────────────────────────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("board keeps resetting", "resets under load when the motor starts")
    b = _state("mcu keeps rebooting", "the rail sags when wifi kicks in")
    assert a.node.id == b.node.id == "power_sag"


# ── Navigator integration ──────────────────────────────────────────────────────────────────

def test_navigator_routes_reset_failure_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my board keeps resetting on its own"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "power_reset"


def test_render_has_no_invented_board_facts():
    st = _state("my board keeps resetting", "it resets under load when wifi transmits")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
