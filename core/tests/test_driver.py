"""Linux device-driver proof-path pattern (project type goal_type=driver). Brutal coverage:
branches, match/no-match boundary, paraphrase invariance, navigator integration, no-invented-facts."""
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
    st = _state("my kernel driver isn't loading")
    assert st.matched and st.pattern.name == "driver"
    assert st.node.id == "load_check"


@pytest.mark.parametrize("msg", [
    "my kernel module won't load, insmod fails",
    "the device driver loads but probe is never called",
    "insmod gives unknown symbol",
    "my platform driver doesn't bind to the device",
    "modprobe fails with version magic mismatch",
    "the kernel driver loads but nothing appears in /dev",
])
def test_fault_phrasings_match(msg):
    assert _name(msg) == "driver", msg


@pytest.mark.parametrize("msg", [
    "what is a kernel module?",
    "how do I get into driver development?",
    "explain the device tree to me",
    "should I write a platform driver or a misc device?",
])
def test_learning_and_concept_questions_do_NOT_match(msg):
    assert _name(msg) is None, msg


def test_load_fault_branch():
    st = _state("my kernel driver isn't loading", "insmod fails with version magic mismatch")
    assert st.node.id == "load_fault"


def test_no_match_branch():
    st = _state("my kernel driver isn't working",
                "the module loads fine and shows in lsmod",
                "but probe is never called and nothing in /dev")
    assert st.node.id == "no_match"
    assert "compatible" in st.node.candidates[0]


def test_probe_fault_branch():
    st = _state("my kernel driver isn't working",
                "the module loads ok",
                "probe fails with -EPROBE_DEFER")
    assert st.node.id == "probe_fault"


def test_paraphrases_reach_same_node():
    a = _state("my kernel driver isn't working", "module loads fine", "probe never runs, not bound")
    b = _state("my device driver isn't working", "it loads ok", "probe isn't called, no match")
    assert a.node.id == b.node.id == "no_match"


def test_navigator_routes_to_proof_path(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    msgs = [{"role": "user", "content": "my kernel driver won't load, modprobe fails"}]
    purpose = decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs)
    route = nav.classify(purpose, msgs)
    assert route.mode == nav.PROOF_PATH and route.proof_state.pattern.name == "driver"


def test_render_has_no_invented_board_facts():
    st = _state("my kernel driver isn't loading", "insmod fails with version magic")
    out = pp.render_proof_path(st, BOARD)
    safe, violations = pp.verify_voiced(out, pp.build_packet(st, BOARD))
    assert safe, violations
