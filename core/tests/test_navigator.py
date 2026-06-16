"""The central Navigator — classifies a confused user's message into a confusion TYPE and routes to
the right kind of guidance. These test the general routing mechanism, not the example phrasings.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import navigator as nav
from eaedk.mentor_llm import decide_purpose, mentor_chat

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _route(conn, text, msgs=None):
    msgs = msgs or [{"role": "user", "content": text}]
    purpose = decide_purpose(conn, BOARD, text, {}, msgs)
    return nav.classify(purpose, msgs)


# --- The five+1 modes are distinguishable (acceptance 2) ----------------------------------------

def test_broken_system_routes_to_proof_path(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "my UART is not working").mode == nav.PROOF_PATH


def test_learning_direction_routes_to_learning_map(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "kernel programming").mode == nav.LEARNING_MAP
    assert _route(conn, "how do I get into driver development?").learning_map.name == "embedded_linux"
    assert _route(conn, "Yocto").learning_map.name == "build_systems"


def test_engineering_decision_routes_to_decision_map(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "RTOS or Linux?").mode == nav.DECISION_MAP
    assert _route(conn, "should I use HAL or bare metal?").mode == nav.DECISION_MAP
    assert _route(conn, "which MCU should I use?").mode == nav.DECISION_MAP


def test_vague_routes_to_clarify(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "asdkfj qwer help").mode == nav.CLARIFY


def test_out_of_scope_routes_to_decline(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "How can I use Nvidia Jetson?").mode == nav.DECLINE


def test_career_routes_to_learning_map_foundation(tmp_path):
    conn = _seeded(tmp_path)
    r = _route(conn, "I want to become a firmware engineer but don't know where to start")
    assert r.mode == nav.LEARNING_MAP and r.learning_map is None     # foundation/career route


def test_grounded_concept_routes_to_teach(tmp_path):
    conn = _seeded(tmp_path)
    assert _route(conn, "what is SPI?").mode == nav.TEACH


# --- Proof path wins over everything (conversation-aware) ----------------------------------------

def test_proof_path_takes_precedence_mid_conversation(tmp_path):
    conn = _seeded(tmp_path)
    msgs = [{"role": "user", "content": "my UART is not working"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "TX is silent"}]
    assert nav.classify(decide_purpose(conn, BOARD, "TX is silent", {}, msgs), msgs).mode == nav.PROOF_PATH


# --- Generality: adding a map is a registry entry, matching is data-driven -----------------------

def test_learning_maps_are_a_registry():
    assert set(nav.LEARNING_MAPS) >= {"embedded_linux", "build_systems"}
    # match is AND-of-ORs over the map's own groups — no per-area code
    assert nav.match_learning("device tree probe failure").name == "embedded_linux"
    assert nav.match_learning("how do I use SPI?") is None           # not a learning-direction area


# --- Live: learning map guides DIRECTION, not a board/blink dump (acceptance 4, 8) ---------------

def test_live_kernel_routes_to_learning_map_not_generic(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "kernel programming"}], use_llm=False)
    assert "learning-direction question" in out          # framed as direction
    assert "route that works, in order" in out           # gives a route
    assert "First step to prove you're moving" in out     # one first step
    assert "cannot run Linux" in out                      # grounded: this board can't run Linux
    assert "Blink an LED" not in out                      # NOT the generic board default


def test_live_uart_still_proof_path(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "my UART is not working"}], use_llm=False)
    assert "UART bring-up problem" in out and "First proof step" in out


def test_live_decision_still_reaches_reasoning_framework(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "should I use HAL or bare metal?"}],
                      use_llm=False)
    assert "trade-off" in out.lower()
