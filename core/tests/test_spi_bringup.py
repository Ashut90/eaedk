"""SPI bring-up / silent-data proof-path pattern — tree logic, match boundaries, paraphrase
invariance, and navigator integration. Mirrors the UART/I2C pattern test discipline."""
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

def test_spi_failure_matches_at_entry():
    st = _state("my spi sensor reads all 0xff, no data back")
    assert st.matched and st.pattern.name == "spi_bringup"
    assert st.node.id == "cs_check"


def test_concept_question_does_NOT_trigger_proof_path():
    # 'what is SPI?' is a concept question, not a fault — must NOT match.
    assert not _state("what is spi?").matched
    assert not _state("when would I use SPI vs I2C?").matched


def test_i2c_and_spi_patterns_do_not_cross_match():
    assert _state("my i2c is not working, no ack").pattern.name == "i2c_bringup"
    assert _state("my spi reads all zeros").pattern.name == "spi_bringup"


# ── Decision tree ─────────────────────────────────────────────────────────────────────────

def test_cs_not_asserted_branch_is_chip_select():
    st = _state("my spi reads all 0xff", "the cs line stays high the whole transfer")
    assert st.node.id == "cs_fault"
    assert "GPIO" in st.node.candidates[0] or "polarity" in st.node.zone


def test_cs_ok_but_no_clock_reaches_clock_fault():
    st = _state("spi reads nothing back", "cs goes low fine", "but there's no clock on sclk")
    assert st.node.id == "clk_fault"


def test_cs_and_clock_ok_reaches_mode_or_miso():
    st = _state("spi reads all 0xff", "cs asserts low", "sclk toggles, 8 clocks per byte")
    assert st.node.id == "mode_or_miso"
    assert "CPOL" in st.node.zone


# ── Paraphrase invariance ───────────────────────────────────────────────────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("spi no data", "cs stays high")
    b = _state("spi reads 0xff", "chip select never goes low")
    assert a.node.id == b.node.id == "cs_fault"


# ── Navigator integration ──────────────────────────────────────────────────────────────────

def test_navigator_routes_spi_failure_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my spi reads all zeros, nothing comes back"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "spi_bringup"


def test_render_has_no_invented_board_facts():
    st = _state("my spi reads all 0xff", "cs stays high")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
