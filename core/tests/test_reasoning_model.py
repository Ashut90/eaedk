"""Reasoning-model support: strip <think> chain-of-thought, and skip the slow LLM critic chain for
reasoning models (deepseek-r1 / QwQ / o1) — the deterministic verifiers still run."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk.llm.think import strip_think
from eaedk.mentor_llm import _is_reasoning_model
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.mentor_llm import mentor_chat
from eaedk.llm.gateway import Gateway


def test_strip_think_removes_reasoning():
    assert strip_think("<think>let me reason\nstep by step</think>The answer is 42.") == "The answer is 42."
    assert strip_think("reasoning leaked </think>\nThe real answer.") == "The real answer."   # stray close
    assert strip_think("no tags here") == "no tags here"
    assert strip_think("<THINK>caps</THINK>ok") == "ok"


def test_reasoning_model_detection():
    assert _is_reasoning_model("deepseek-r1:8b")
    assert _is_reasoning_model("qwq:32b")
    assert _is_reasoning_model("o1-mini")
    assert not _is_reasoning_model("llama3.1:8b")
    assert not _is_reasoning_model("qwen2.5-coder:7b")


class _Spy:
    def __init__(self, model): self.provider = self; self.model = model; self.calls = 0
    def available(self): return True
    def generate(self, system, prompt):
        self.calls += 1
        return "Interrupt-driven logging frees the CPU between bytes. Question: how fast is your link?"


def test_reasoning_model_skips_the_critic_chain(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)
    q = [{"role": "user", "content": "should my logging be interrupt-driven on this board?"}]

    reasoning = _Spy("deepseek-r1:8b")          # default/open shape would normally run the critic chain
    mentor_chat(conn, "STM32F103-BluePill", q, use_llm=True, gateway=Gateway(provider=reasoning))
    assert reasoning.calls == 1                  # reasoning model: a single Actor pass, no critics

    plain = _Spy("llama3.1:8b")
    mentor_chat(conn, "STM32F103-BluePill", q, use_llm=True, gateway=Gateway(provider=plain))
    assert plain.calls > 1                        # non-reasoning model still runs the critic chain
