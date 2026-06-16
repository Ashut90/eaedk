"""Tests for the TEACH/concept Navigator framework — ensuring concept explanations use the
MATCH → SORT → CONFIRM → ORGANIZE → COMPARE → HELP structure instead of a bare anchor string.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import mentor_llm as ml
from eaedk import problem_patterns as pp, navigator, repo

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _convo(*turns):
    msgs = []
    for i, t in enumerate(turns):
        if i:
            msgs.append({"role": "assistant", "content": "..."})
        msgs.append({"role": "user", "content": t})
    return msgs


# --- TEACH mode routes to TEACH (no pattern match, no decision topic) ---------------------------

def test_what_is_spi_routes_to_teach(tmp_path):
    """'what is SPI?' must route to TEACH mode, not PROOF_PATH or DECISION_MAP."""
    conn = _seeded(tmp_path)
    messages = _convo("what is SPI?")
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    purpose = ml.decide_purpose(conn, BOARD, last, {"page_type": "", "current_code": ""}, messages)
    route = navigator.classify(purpose, messages)
    assert route.mode == navigator.TEACH, f"Expected TEACH, got {route.mode}"


# --- TEACH output must contain MATCH/SORT/CONFIRM/ORGANIZE/COMPARE/HELP -------------------------

def test_what_is_spi_contains_navigator_framework(tmp_path):
    """The answer for SPI must include the six Navigator sections."""
    conn = _seeded(tmp_path)
    out = ml.mentor_chat(conn, BOARD, _convo("what is SPI?"), use_llm=False)
    
    # 1. MATCH — identifies the concept
    assert "synchronous serial" in out.lower() or "SPI" in out
    
    # 2. SORT — explains signals / structure
    assert any(term in out.lower() for term in 
               ("clock", "data line", "chip-select", "chip select", "device selection", "cpol", "cpha"))
    
    # 3. CONFIRM — asks what the user wants to do
    assert any(term in out.lower() for term in
               ("what are you trying", "are you trying to", "what would you like",
                "would you like to", "your goal", "what do you need"))
    
    # 4. ORGANIZE — gives an order of thinking/learning
    assert any(term in out.lower() for term in
               ("first", "then", "order", "step", "next"))
    
    # 5. COMPARE — compares with other buses
    assert "i2c" in out.lower() and "uart" in out.lower()
    
    # 6. HELP — gives one safe next step
    assert any(term in out.lower() for term in
               ("loopback", "known byte", "draw", "signals", "0x55", "exercise"))


def test_what_is_spi_includes_spi_vs_i2c_vs_uart(tmp_path):
    """SPI explanation must compare SPI, I2C, and UART."""
    conn = _seeded(tmp_path)
    out = ml.mentor_chat(conn, BOARD, _convo("what is SPI?"), use_llm=False)
    # Must mention all three
    assert "spi" in out.lower() and "i2c" in out.lower() and "uart" in out.lower()


def test_what_is_spi_includes_safe_next_step(tmp_path):
    """SPI explanation must end with an actionable next step."""
    conn = _seeded(tmp_path)
    out = ml.mentor_chat(conn, BOARD, _convo("what is SPI?"), use_llm=False)
    # A next step is present
    assert any(term in out.lower() for term in
               ("try this:", "next step:", "would you like to", "your next step", "here is your next"))


def test_what_is_spi_no_rcc_blink_uart_scaffold(tmp_path):
    """SPI explanation must NOT include unrelated RCC/blink/UART clock scaffold."""
    conn = _seeded(tmp_path)
    out = ml.mentor_chat(conn, BOARD, _convo("what is SPI?"), use_llm=False)
    # Fail if these appear
    violations = []
    if "rcc" in out.lower():
        violations.append("RCC mention")
    if "blink" in out.lower():
        violations.append("blink mention")
    if "which clock your uart runs on" in out.lower():
        violations.append("UART clock question")
    if "find the line that enables your led pin's gpio clock" in out.lower():
        violations.append("LED GPIO clock experiment")
    if violations:
        import pytest
        pytest.fail(f"SPI answer contains unrelated scaffold: {', '.join(violations)}")


# --- Other concepts should still work (fallback to anchor) --------------------------------------

def test_other_concept_still_shows_anchor(tmp_path):
    """A concept without curated TEACH data should still show its anchor."""
    conn = _seeded(tmp_path)
    out = ml.mentor_chat(conn, BOARD, _convo("what is a watchdog?"), use_llm=False)
    assert "watchdog" in out.lower() and "timer" in out.lower()


# --- Existing tests still pass (these are the critical regression checks) ------------------------