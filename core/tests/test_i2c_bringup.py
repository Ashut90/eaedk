"""I2C bring-up proof-path pattern — tree logic, match boundaries, paraphrase invariance,
and navigator integration. Mirrors the UART_BRINGUP test discipline."""
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

def test_i2c_failure_matches_at_entry():
    st = _state("my i2c sensor is not working, no ack")
    assert st.matched and st.pattern.name == "i2c_bringup"
    assert st.node.id == "bus_idle_check"


def test_concept_and_design_questions_do_NOT_trigger_proof_path():
    # A "teach me" question is not a fault.
    assert not _state("explain i2c to me").matched
    # The design-eval question we must NOT hijack into a proof path.
    assert not _state("I2C with 10 sensors, all 4.7k pull-ups, is that correct?").matched
    # SPI failure must not be captured by the I2C pattern.
    assert _state("my spi is not working").pattern is not (pp.PATTERNS.get("i2c_bringup"))


# ── Decision tree ─────────────────────────────────────────────────────────────────────────

def test_lines_low_branch_is_the_pullup_zone():
    st = _state("my i2c is not working", "sda is stuck low at idle")
    assert st.node.id == "lines_low"
    assert "pull-up" in st.node.candidates[0]


def test_idle_high_then_no_scan_hit_reaches_device_or_clock():
    st = _state("my i2c device won't respond",
                "both lines idle high at 3.3v",
                "the bus scan finds nothing")
    assert st.node.id == "no_ack"


def test_idle_high_then_scan_hit_reaches_address_format():
    st = _state("i2c not detected",
                "lines are high at idle",
                "the scanner acks at 0x68")
    assert st.node.id == "addr_mismatch"
    assert "7-bit" in st.node.zone


# ── Paraphrase invariance (branch on normalised evidence, not wording) ─────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("i2c no ack", "scl stays low")
    b = _state("i2c not responding", "the clock line is held low")
    assert a.node.id == b.node.id == "lines_low"


# ── Navigator integration ──────────────────────────────────────────────────────────────────

def test_navigator_routes_i2c_failure_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my i2c is not working, no ack on the bus"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "i2c_bringup"


def test_render_has_no_invented_board_facts(tmp_path):
    # The deterministic render must stay board-agnostic (no fabricated pins/registers/instances).
    st = _state("my i2c is not working", "sda stuck low")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
