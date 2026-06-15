"""Golden tests for v2.4.0 Part 1 — the mentor prompts reason, they don't retrieve (docs/22).

These assert prompt *shape* deterministically, with no live model: the system prompts encode the
five jobs, and the chat prompt injects the board's real context from SQLite.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk.llm.gateway import Gateway
from eaedk import mentor_llm, repo
from eaedk import actor_critic as ac


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


class _Recorder:
    """A fake LLM provider that records the (system, prompt) it is asked to generate from."""
    model = "fake"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        return "A consequence first. Try this: blink the LED. Question: which clock drives it?"


# Job 1 — open with a consequence or a question, never a definition.
def test_job1_consequence_not_definition():
    assert "consequence" in mentor_llm._CHAT_SYSTEM.lower()
    assert "never a definition" in mentor_llm._CHAT_SYSTEM.lower()
    assert "consequence" in mentor_llm._EXPLAIN_SYSTEM.lower()
    assert "never open with a definition" in mentor_llm._EXPLAIN_SYSTEM.lower()


# Job 2 — the board's capability map (real peripherals) is injected into the chat prompt.
def test_job2_capability_map_injected(tmp_path):
    conn = _seeded(tmp_path)
    rec = _Recorder()
    mentor_llm.mentor_chat(conn, "STM32F103-BluePill",
                           [{"role": "user", "content": "how do I drive the LED?"}],
                           use_llm=True, gateway=Gateway(provider=rec))
    _system, prompt = rec.calls[0]   # the Actor pass (P4 adds a Critic pass at calls[1])
    assert "peripherals" in prompt.lower()
    assert "uart" in prompt.lower() and "gpio" in prompt.lower()     # the board's actual caps


# Job 3 — the four goal questions are in the Actor prompt, before any code.
def test_job3_four_goal_questions_in_actor():
    s = ac._ACTOR_SYSTEM.lower()
    assert "what is the goal" in s and "why" in s and "how" in s and "when is it done" in s


# Job 4 — the Critic checks hardware consequences, not style.
def test_job4_critic_hardware_consequence_not_style():
    s = ac._CRITIC_SYSTEM.lower()
    assert "consequence" in s and "never code style" in s
    assert "alternate-function" in s                                # the AF-mismatch reasoning


# Job 5 — the Wokwi flag is injected and Role D (simulator downgrade) is present.
def test_job5_wokwi_flag_and_role_d(tmp_path):
    conn = _seeded(tmp_path)
    rec = _Recorder()
    mentor_llm.mentor_chat(conn, "STM32F103-BluePill",
                           [{"role": "user", "content": "what about boot pins?"}],
                           use_llm=True, gateway=Gateway(provider=rec), has_hardware=False)
    _system, prompt = rec.calls[0]   # the Actor pass (P4 adds a Critic pass at calls[1])
    assert "wokwi" in prompt.lower()                                 # the flag reaches the model
    assert "wokwi" in mentor_llm._CHAT_SYSTEM.lower()                # Role D in the system prompt


# Code Studio chat — the editor's current code is injected into the prompt (docs/23).
def test_studio_code_injected_via_extra_context(tmp_path):
    conn = _seeded(tmp_path)
    rec = _Recorder()
    code = "int main(){ GPIOA->ODR = 1; }   // MARKER_UNIQUE_42"
    mentor_llm.mentor_chat(conn, "STM32F103-BluePill",
                           [{"role": "user", "content": "why does nothing happen?"}],
                           use_llm=True, gateway=Gateway(provider=rec), extra_context=code)
    _system, prompt = rec.calls[0]   # the Actor pass (P4 adds a Critic pass at calls[1])
    assert "MARKER_UNIQUE_42" in prompt                              # the editor code reached the model


# Mentor chat — State-Engine progress for the current project is injected (docs/23).
def test_mentor_progress_injected(tmp_path):
    conn = _seeded(tmp_path)
    repo.create_project(conn, "p1", "bare_metal_app", "STM32F103-BluePill")
    rec = _Recorder()
    mentor_llm.mentor_chat(conn, "STM32F103-BluePill",
                           [{"role": "user", "content": "what should I build?"}],
                           use_llm=True, gateway=Gateway(provider=rec), project="p1")
    _system, prompt = rec.calls[0]   # the Actor pass (P4 adds a Critic pass at calls[1])
    assert "progress" in prompt.lower()                              # State-Engine progress reached the model


# v2.4.1 — a named project type reasons about THIS board's specific peripherals, not generically.
def test_boards_robotics_reasons_about_tim1_not_generic(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_llm.mentor_chat(conn, "STM32F103-BluePill",     # STM32 + timer => TIM1 reasoning
                                 [{"role": "user", "content": "can I do robotics?"}],
                                 use_llm=False).lower()
    assert "tim1" in out and "complementary" in out and "h-bridge" in out
    assert "encoder" in out                                      # position sensing, not "blink an LED"


def test_boards_robotics_family_gated_for_avr(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_llm.mentor_chat(conn, "Arduino-Uno",           # AVR, not STM32 => no TIM1 claim
                                 [{"role": "user", "content": "can I build a robot with motors?"}],
                                 use_llm=False).lower()
    assert "tim1" not in out
    assert "h-bridge" in out                                     # still the motor-driver safety reasoning
