"""Golden tests for v2.5.0 — role detection happens in Python, before the model (docs/25)."""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import repo
from eaedk.mentor_llm import (detect_mentor_role, _select_try_this, _role_ctx,
                              build_architect_prompt, build_peer_mentor_prompt,
                              build_reverse_mentor_prompt)


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


# --- role detection (the four trigger sets, in priority order) -------------

def test_role_sponsor_on_validate_export():
    assert detect_mentor_role("anything", {"page_type": "validate"}) == "SPONSOR"
    assert detect_mentor_role("anything", {"page_type": "export"}) == "SPONSOR"


def test_role_reverse_on_wokwi():
    assert detect_mentor_role("how do I blink", {"wokwi_flag": True}) == "REVERSE_MENTOR"


def test_role_peer_on_code_or_debug():
    assert detect_mentor_role("my code compiles but nothing happens", {}) == "PEER_MENTOR"
    long_code = "int main(void){ GPIOA->ODR = 1; }" + " // padding" * 8   # > 50 chars
    assert detect_mentor_role("look at this", {"current_code": long_code}) == "PEER_MENTOR"


def test_role_architect_on_design_questions():
    for q in ("can I do robotics?", "which board should I pick?", "should I use HAL or bare metal?"):
        assert detect_mentor_role(q, {}) == "SYSTEM_ARCHITECT"


def test_role_priority_sponsor_beats_wokwi():
    assert detect_mentor_role("blink", {"page_type": "validate", "wokwi_flag": True}) == "SPONSOR"


# --- domain "Try this" matches the detected project type (Fix 4) -----------

def test_try_this_matches_project_type():
    assert "pwm" in _select_try_this("can I do robotics?", "stm32").lower()
    assert "tim1" in _select_try_this("a robot with motors", "stm32").lower()     # STM32 family
    assert "tim1" not in _select_try_this("a robot with motors", "avr").lower()   # gated off for AVR
    assert "i2c" in _select_try_this("read a sensor", "stm32").lower()
    assert "servo" in _select_try_this("drive a servo", "stm32").lower()
    assert "flash" in _select_try_this("a fail-safe bootloader", "stm32").lower()


# --- role -> prompt: board context precedes the examples, all three build ---

def test_role_prompts_put_board_context_before_examples(tmp_path):
    conn = _seeded(tmp_path)
    board, soc = repo.load_board(conn, "STM32F103-BluePill")
    ctx = _role_ctx("STM32F103-BluePill", soc, board, {"gpio", "uart", "timer"}, None, None,
                    "int main(){}")
    for build in (build_architect_prompt, build_peer_mentor_prompt, build_reverse_mentor_prompt):
        p = build(ctx)                                  # also proves .format() has no stray braces
        assert "STM32F103-BluePill" in p
        assert p.index("Peripherals:") < p.index("STUDY THESE EXAMPLES")   # context BEFORE examples
