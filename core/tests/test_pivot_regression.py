"""Regression guards for the 'LLM generates, deterministic verifies' pivot (docs/35).

The LLM output is non-deterministic, so these lock the MECHANISM that produces the four
guaranteed behaviours — the prompt framing, the relevance-critic routing, the offline honesty
note, and that proof-paths are untouched.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import arbiter, problem_patterns as pp, navigator as nav
from eaedk.mentor_llm import mentor_chat, decide_purpose, _ARCHITECT_TEMPLATE

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    return conn


class _Fixed:
    def __init__(self, out): self.provider = self; self._out = out; self.calls = 0
    def available(self): return True
    def generate(self, system, prompt): self.calls += 1; return self._out


# 1. A concrete question (folder structure) must be answered directly — never a trade-off essay.
def test_concrete_question_is_answered_directly_not_a_trade_off_essay():
    t = _ARCHITECT_TEMPLATE.lower()
    assert "concrete" in t and "answer it directly" in t          # the prompt instructs a direct answer
    # the relevance critic treats a folder-structure question as concrete (not an open decision) and
    # rewrites a trade-off essay into the concrete answer.
    assert not arbiter._is_open_decision("what folder structure should I use for the bootloader?")
    gw = _Fixed("Use src/ and include/ folders: bootloader.c, main_app.c, and headers.")
    out = arbiter.answer_check(gw, "what folder structure for the bootloader firmware?",
                               "A bootloader verifies the app and jumps to it. Trade-offs: single-bank vs A/B...",
                               "Board: ATmega2560 (avr)")
    assert "src/" in out and "trade-off" not in out.lower() and gw.calls == 1


# 2. HAL vs bare metal must stay Socratic — classified as an open decision, critic skips it.
def test_hal_vs_bare_metal_stays_socratic():
    assert arbiter._is_open_decision("should I use HAL or bare metal for my STM32 project?")
    t = _ARCHITECT_TEMPLATE.lower()
    assert "do not open with a recommendation" in t               # the open branch is Socratic
    gw = _Fixed("Use HAL.")                                       # a blunt recommendation
    socratic = "First, ask yourself: are you optimising to learn or to ship? The trade-off is ..."
    out = arbiter.answer_check(gw, "should I use HAL or bare metal?", socratic, "ctx")
    assert out == socratic and gw.calls == 0                      # left Socratic, critic did not fire


# 3. Offline must not pretend the stored reference is a tailored answer.
def test_offline_does_not_pretend(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD,
                      [{"role": "user", "content": "how should I structure a bootloader project?"}],
                      use_llm=False)
    assert "[offline reference]" in out and "--llm" in out


# 4. Debugging proof-paths must still work, untouched by the pivot.
def test_proof_paths_still_work(tmp_path):
    st = pp.resolve([{"role": "user", "content": "my uart prints nothing, no output"}])
    assert st.matched and st.pattern.name == "uart_bringup"
    conn = _seeded(tmp_path)
    msgs = [{"role": "user", "content": "my uart prints nothing, no output"}]
    route = nav.classify(decide_purpose(conn, BOARD, msgs[0]["content"], {}, msgs), msgs)
    assert route.mode == nav.PROOF_PATH and route.proof_state.pattern.name == "uart_bringup"


# 5. A concrete question must NOT get the decision-topic framework injected (weak models recite it);
#    an open design question still does.
def test_concrete_question_omits_the_decision_framework(tmp_path):
    conn = _seeded(tmp_path)

    from eaedk.llm.gateway import Gateway

    class _Rec:                                # the framework reference goes into the SYSTEM prompt
        def __init__(self): self.systems = []
        def available(self): return True
        def generate(self, s, p): self.systems.append(s); return "ok. Try this: x. Question: y?"

    concrete = _Rec()
    mentor_chat(conn, BOARD, [{"role": "user", "content": "what folder structure should I use for a bootloader?"}],
                use_llm=True, gateway=Gateway(provider=concrete))
    assert concrete.systems and not any("REFERENCE you MAY draw on" in s for s in concrete.systems)

    open_q = _Rec()
    mentor_chat(conn, BOARD, [{"role": "user", "content": "should I use a single-bank or A/B bootloader?"}],
                use_llm=True, gateway=Gateway(provider=open_q))
    assert any("REFERENCE you MAY draw on" in s for s in open_q.systems)
