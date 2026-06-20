"""Relevance critic (arbiter.answer_check) — rewrites answers that dodge the question, degrades
safely, and is wired into the mentor LLM path. The one non-deterministic verifier check."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile, os
from eaedk import arbiter
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.mentor_llm import mentor_chat
from eaedk.llm.gateway import Gateway


class _Fixed:
    def __init__(self, out): self.provider = self; self._out = out; self.calls = 0
    def available(self): return True
    def generate(self, system, prompt): self.calls += 1; return self._out


class _Boom:
    def __init__(self): self.provider = self
    def generate(self, system, prompt): raise RuntimeError("offline")


def test_rewrites_a_dodging_answer():
    gw = _Fixed("Here is a 3-folder layout: bootloader/, application/, common/.")
    out = arbiter.answer_check(gw, "what folder structure for a bootloader?",
                               "A bootloader checks the app and jumps to it. Trade-offs: ...",
                               "Board: ATmega2560 (avr)")
    assert "bootloader/" in out and gw.calls == 1


def test_skips_when_no_question_or_too_short():
    gw = _Fixed("SHOULD NOT BE USED")
    assert arbiter.answer_check(gw, "", "a real long enough answer here ok", "ctx") == "a real long enough answer here ok"
    assert arbiter.answer_check(gw, "a question?", "short", "ctx") == "short"
    assert gw.calls == 0


def test_degrades_on_model_error():
    assert arbiter.answer_check(_Boom(), "q?", "a draft answer long enough", "ctx") == "a draft answer long enough"


def test_relevance_critic_is_wired_into_mentor_chat(tmp_path):
    conn = connect(str(tmp_path / "t.db")); migrate(conn); seed_all(conn, force=True)

    class _Rec:
        def __init__(self): self.sys = []
        def available(self): return True
        def generate(self, s, p): self.sys.append(s); return "an answer of sufficient length. Try this: x. Question: y?"

    rec = _Rec()
    mentor_chat(conn, "STM32F103-BluePill",
                [{"role": "user", "content": "how should I structure a bootloader project?"}],
                use_llm=True, gateway=Gateway(provider=rec))
    assert any("RELEVANCE CRITIC" in s for s in rec.sys)
