"""Bounded why-critic (Step 4) — fires only on uncovered faults, pushes physical-first + a
measurable step, and degrades safely. Pure unit tests with a fake gateway."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eaedk import arbiter


class _FakeGW:
    """Records calls; its single provider returns a fixed string for any generate()."""
    def __init__(self, out):
        self.provider = self
        self._out = out
        self.calls = 0

    def generate(self, system, prompt):
        self.calls += 1
        return self._out


class _BoomGW:
    def __init__(self):
        self.provider = self

    def generate(self, system, prompt):
        raise RuntimeError("model offline")


# ── Fault gating ────────────────────────────────────────────────────────────────────────────

def test_is_fault_boundary():
    assert arbiter._is_fault("my uart isn't working")
    assert arbiter._is_fault("my board keeps resetting")
    assert arbiter._is_fault("my sdr isn't receiving anything")
    assert not arbiter._is_fault("what is SPI?")
    assert not arbiter._is_fault("should I use an RTOS or a superloop?")
    assert not arbiter._is_fault("where do I start with communication protocols?")


# ── Behaviour ─────────────────────────────────────────────────────────────────────────────────

def test_fires_on_fault_and_returns_revision():
    gw = _FakeGW("Check the rail first. Try this: scope the 3V3 rail during the failure.")
    out = arbiter.why_review(gw, "Just rewrite your interrupt handler.",
                             "Board: STM32F103 (cortex-m3)", "my sensor read isn't working")
    assert "scope the 3V3 rail" in out
    assert gw.calls == 1                       # exactly one bounded pass


def test_skips_non_fault_with_no_model_call():
    gw = _FakeGW("SHOULD NOT BE RETURNED")
    draft = "SPI is a synchronous serial bus with a chip-select per device."
    out = arbiter.why_review(gw, draft, "Board: X", "what is SPI?")
    assert out == draft                        # concept answer untouched
    assert gw.calls == 0                        # and no model call at all


def test_degrades_to_draft_on_model_error():
    out = arbiter.why_review(_BoomGW(), "draft answer", "ctx", "my board keeps crashing")
    assert out == "draft answer"


def test_empty_model_output_falls_back_to_draft():
    out = arbiter.why_review(_FakeGW("   "), "draft answer", "ctx", "uart not working")
    assert out == "draft answer"
