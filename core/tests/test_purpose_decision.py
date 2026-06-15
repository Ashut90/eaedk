"""Purpose Decision gate (docs/29) — the mentor's first-step proof that it is NOT an answer engine.

Before any answer is generated, the mentor chooses an OUTCOME:
  ANSWER_NOW | ASK_CLARIFICATION | REDIRECT_TO_FOUNDATION | DECLINE_OUT_OF_SCOPE
ANSWER_NOW is never the default — it requires a resolvable intent AND grounding in EAEDK's curated
knowledge. The user's question is the subject; the selected board is only context.

These run with NO live model: the gate is deterministic, like detect_mentor_role.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import mentor_llm
from eaedk.mentor_llm import decide_purpose, mentor_chat

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _decide(conn, text, **page):
    return decide_purpose(conn, BOARD, text, page, [{"role": "user", "content": text}])


# --- The contract -------------------------------------------------------------------------------

def test_answer_now_is_not_the_default(tmp_path):
    """A question that resolves to no grounded knowledge and names no subject must NOT be answered."""
    conn = _seeded(tmp_path)
    d = _decide(conn, "tell me everything")
    assert d.purpose != "ANSWER_NOW"


# --- Case 1: out-of-scope hardware subject ------------------------------------------------------

def test_case1_jetson_is_declined_not_answered_about_the_board(tmp_path):
    conn = _seeded(tmp_path)
    d = _decide(conn, "How can I use Nvidia Jetson?")
    assert d.purpose == "DECLINE_OUT_OF_SCOPE"
    assert "Jetson" in d.subject

    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "How can I use Nvidia Jetson?"}])
    low = out.lower()
    assert "jetson" in low                       # it names the real subject
    assert "blink" not in low                    # does NOT fall back to the blink default
    assert BOARD not in out                       # does NOT answer about the selected board


# --- Case 2: career / foundation question -------------------------------------------------------

def test_direction_phrase_does_not_rescue_an_out_of_scope_subject(tmp_path):
    """'where to start' grounds the ACTION, not the SUBJECT — a question naming an out-of-scope
    platform must still be declined, never answered as a board default (regression: the gate used to
    let the direction phrase ground the whole turn)."""
    conn = _seeded(tmp_path)
    d = _decide(conn, "where to start in NVIDIA jetson ?")
    assert d.purpose == "DECLINE_OUT_OF_SCOPE"
    assert "NVIDIA" in d.subject
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "where to start in NVIDIA jetson ?"}])
    assert "blink" not in out.lower() and BOARD not in out      # not the board-default answer

    # but the SAME direction phrase with no out-of-scope subject still answers on this board
    assert _decide(conn, "where do I start?").purpose == "ANSWER_NOW"


def test_case2_career_redirects_to_foundation_not_board_default(tmp_path):
    conn = _seeded(tmp_path)
    q = "I want to become a firmware engineer but I don't know where to start."
    d = _decide(conn, q)
    assert d.purpose == "REDIRECT_TO_FOUNDATION"

    out = mentor_chat(conn, BOARD, [{"role": "user", "content": q}]).lower()
    assert "sequence" in out or "order" in out   # a learning path, not a single board answer


# --- Case 3: a grounded concept -> teach --------------------------------------------------------

def test_field_entry_start_question_redirects_to_foundation(tmp_path):
    """'where to start in firmware / programming' is field-entry (a learning path), not a board
    start — even with typos. The same phrase with no field word ('where do I start?') still answers
    on the board (regression: a beginner's field-entry question used to fall through to ANSWER_NOW)."""
    conn = _seeded(tmp_path)
    for q in ("as a begginer where to start in firmware in programmin",
              "where do I start in embedded?"):
        assert _decide(conn, q).purpose == "REDIRECT_TO_FOUNDATION"
    assert _decide(conn, "where do I start?").purpose == "ANSWER_NOW"        # no field word → board start
    assert _decide(conn, "where to start with SPI?").purpose == "ANSWER_NOW"  # anchored concept


def test_case3_spi_is_answer_now(tmp_path):
    conn = _seeded(tmp_path)
    d = _decide(conn, "What is SPI?")
    assert d.purpose == "ANSWER_NOW"             # SPI is in the curated concept/capability vocabulary


# --- Case 4: a fault report with no evidence ----------------------------------------------------

def test_case4_bare_crash_asks_for_evidence(tmp_path):
    conn = _seeded(tmp_path)
    d = _decide(conn, "My code crashed.")
    assert d.purpose == "ASK_CLARIFICATION"
    assert d.reason.startswith("fault report")

    out = mentor_chat(conn, BOARD, [{"role": "user", "content": "My code crashed."}]).lower()
    assert "error" in out or "log" in out        # asks for evidence, not a generic lecture


def test_case4_crash_WITH_evidence_is_answerable(tmp_path):
    """The same report, but with evidence, is no longer a clarification — it can be answered."""
    conn = _seeded(tmp_path)
    d = _decide(conn, "My code crashed with a HardFault at 0x0800123A")
    assert d.purpose == "ANSWER_NOW"


def test_named_fault_type_is_symptom_not_evidence(tmp_path):
    """Naming the exception ('crashed with HardFault_Handler') is the symptom — it must route to a
    debugging clarification that asks for evidence (address/registers/logs), NOT generic teaching."""
    conn = _seeded(tmp_path)
    q = "My code crashed with HardFault_Handler"
    d = _decide(conn, q)
    assert d.purpose == "ASK_CLARIFICATION"
    assert d.reason.startswith("fault report")
    out = mentor_chat(conn, BOARD, [{"role": "user", "content": q}]).lower()
    assert "address" in out or "register" in out      # asks for diagnostic evidence
    assert "unrecoverably wrong" not in out           # NOT the HardFault concept lecture


# --- Grounding rules ----------------------------------------------------------------------------

def test_selected_board_alone_is_not_grounding(tmp_path):
    """A vague question on a selected board still isn't answerable — the board is context, not subject."""
    conn = _seeded(tmp_path)
    d = _decide(conn, "what do you think")
    assert d.purpose != "ANSWER_NOW"


def test_engineering_topic_is_answer_now(tmp_path):
    conn = _seeded(tmp_path)
    assert _decide(conn, "should I use HAL or bare metal?").purpose == "ANSWER_NOW"


def test_validation_page_is_answer_now(tmp_path):
    conn = _seeded(tmp_path)
    assert _decide(conn, "anything", page_type="validate").purpose == "ANSWER_NOW"
