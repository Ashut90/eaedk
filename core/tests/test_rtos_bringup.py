"""RTOS firmware proof-path pattern (project type goal_type=rtos) — tree logic, match boundaries,
paraphrase invariance, navigator integration. Mirrors the bare-metal pattern test discipline."""
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

def test_rtos_failure_matches_at_entry():
    st = _state("my freertos task isn't running")
    assert st.matched and st.pattern.name == "rtos_bringup"
    assert st.node.id == "rtos_check"


def test_decision_and_concept_questions_do_NOT_match():
    assert not _state("should I use an RTOS or a superloop?").matched
    assert not _state("what is an RTOS?").matched


# ── Decision tree: scheduler vs task ────────────────────────────────────────────────────────

def test_scheduler_not_running_branch():
    st = _state("my freertos tasks won't run", "nothing runs at all, the scheduler doesn't start")
    assert st.node.id == "sched_fault"
    assert "PendSV" in st.node.candidates[0]


def test_task_runs_then_starved():
    st = _state("my freertos task isn't running",
                "the other tasks run fine",
                "but my sensor task never runs, it's starved")
    assert st.node.id == "starvation"


def test_task_runs_then_stack_crash():
    st = _state("one of my freertos tasks crashes",
                "the other tasks run fine",
                "that task crashes with a stack overflow")
    assert st.node.id == "task_stack"


# ── Paraphrase invariance ───────────────────────────────────────────────────────────────────

def test_paraphrases_reach_the_same_node():
    a = _state("freertos scheduler not switching tasks", "nothing runs, scheduler doesn't start")
    b = _state("my rtos tasks won't run", "no task executes, the scheduler never starts")
    assert a.node.id == b.node.id == "sched_fault"


# ── Navigator integration ──────────────────────────────────────────────────────────────────

def test_navigator_routes_rtos_failure_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my freertos scheduler hangs, no task runs"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH
    assert route.proof_state.pattern.name == "rtos_bringup"


def test_render_has_no_invented_board_facts():
    st = _state("my freertos task isn't running", "nothing runs, scheduler doesn't start")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
