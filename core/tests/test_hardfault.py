"""HardFault / memory-crash proof-path pattern — tree logic, match boundaries (esp. the
Purpose-gate bare-crash boundary it must NOT cross), paraphrase invariance, navigator integration."""
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


# ── MATCH boundary — must respect the Purpose gate's bare-crash handling ────────────────────

def test_fault_with_mechanism_matches_at_entry():
    st = _state("it keeps crashing into the hardfault handler on an unaligned access")
    assert st.matched and st.pattern.name == "hardfault"
    assert st.node.id == "fault_decode"


def test_bare_or_named_only_crash_does_NOT_match():
    # The Purpose gate owns these (ask for evidence) — the pattern must NOT hijack them.
    assert not _state("My code crashed.").matched
    assert not _state("My code crashed with HardFault_Handler").matched
    # A bare 'stack overflow' (concept/decision) without a fault mechanism must not match either.
    assert not _state("what is a stack overflow?").matched
    assert not _state("what is a hardfault?").matched


# ── Decision tree: the fault-type split ─────────────────────────────────────────────────────

def test_bus_fault_branch():
    st = _state("it keeps crashing into the fault handler",
                "the cfsr shows a bus fault on a null pointer")
    assert st.node.id == "bad_pointer"


def test_usage_fault_branch():
    st = _state("my code jumps to the hardfault handler",
                "cfsr says usage fault, an unaligned access")
    assert st.node.id == "usage_fault"


def test_stack_overflow_then_linker_overlap():
    st = _state("it keeps crashing into the hardfault handler",
                "the cfsr shows a memmanage stack overflow",
                "the linker map shows the stack region is too small")
    assert st.node.id == "linker_overlap"


def test_stack_overflow_then_runaway():
    st = _state("my firmware keeps crashing in the fault handler",
                "it's a stack overflow",
                "caused by infinite recursion in my parser")
    assert st.node.id == "runaway_stack"


# ── Paraphrase invariance ───────────────────────────────────────────────────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("keeps crashing into the fault handler", "bus fault on a null pointer")
    b = _state("my code jumps to the fault handler", "the cfsr shows a bad pointer / bus fault")
    assert a.node.id == b.node.id == "bad_pointer"


# ── Navigator integration ──────────────────────────────────────────────────────────────────

def test_navigator_routes_hardfault_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my code keeps crashing into the hardfault handler"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "hardfault"


def test_render_has_no_invented_board_facts():
    st = _state("keeps crashing into the fault handler", "bus fault on a null pointer")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
