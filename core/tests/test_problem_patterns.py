"""Problem-pattern engine — the general MATCH → ProofPathState → EvidenceEvent → DecisionNode loop,
with UART bring-up as the first curated seed. These test the MECHANISM (normalised-evidence branching,
stateful replay), not the literal example phrasings.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import problem_patterns as pp
from eaedk.mentor_llm import mentor_chat

BOARD = "STM32F103-BluePill"


def _seeded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    seed_all(conn, force=True)
    return conn


def _convo(*turns):
    """Build a [{user},{assistant},{user},...] transcript from user turns."""
    msgs = []
    for i, t in enumerate(turns):
        if i:
            msgs.append({"role": "assistant", "content": "..."})
        msgs.append({"role": "user", "content": t})
    return msgs


# --- MATCH (fine match): a problem family, not a design/concept question ------------------------

def test_match_requires_symptom_and_trouble():
    assert pp.match_pattern("my UART is not working") is pp.UART_BRINGUP
    assert pp.match_pattern("nothing prints on serial") is pp.UART_BRINGUP
    # a design or concept question about the SAME peripheral must NOT match the failure pattern
    assert pp.match_pattern("what is UART?") is None
    assert pp.match_pattern("should I use UART or SPI?") is None
    assert pp.match_pattern("how do I drive the LED?") is None


# --- EvidenceEvent: many wordings normalise to ONE evidence value (not phrase matching) ----------

def test_evidence_normalisation_collapses_phrasings():
    for phrase in ("TX is silent", "no waveform on TX", "pin is not toggling",
                   "logic analyzer shows nothing", "tx is dead"):
        assert pp.extract_evidence(pp.UART_BRINGUP, phrase) == {"tx_activity": "absent"}
    assert pp.extract_evidence(pp.UART_BRINGUP, "I see the 0x55 toggling") == {"tx_activity": "present"}
    assert pp.extract_evidence(pp.UART_BRINGUP, "still nothing, stays dead as gpio") == \
        {"gpio_toggle": "static"}


# --- ProofPathState: stateful replay + branching on NORMALISED evidence --------------------------

def test_turn1_sits_at_entry_node():
    st = pp.resolve(_convo("my UART is not working"))
    assert st.matched and st.node.id == pp.UART_BRINGUP.entry


def test_branch_absent_vs_present():
    assert pp.resolve(_convo("my UART is not working", "TX is silent")).node.id == "tx_silent"
    assert pp.resolve(_convo("my UART is not working", "I see pulses on TX")).node.id == "tx_present"


def test_paraphrases_reach_the_same_node():
    a = pp.resolve(_convo("my UART is not working", "TX pin is silent")).node.id
    b = pp.resolve(_convo("my UART is not working", "no waveform on TX, pin is not toggling")).node.id
    assert a == b == "tx_silent"


def test_two_level_branch():
    # absent -> tx_silent, then static-as-GPIO -> clock_or_code; moves-as-GPIO -> af_or_instance
    assert pp.resolve(_convo("uart not working", "tx silent", "still dead as gpio")).node.id == "clock_or_code"
    assert pp.resolve(_convo("uart not working", "tx silent", "the pin toggles as gpio")).node.id == "af_or_instance"


def test_unresolved_reply_stays_and_awaits():
    st = pp.resolve(_convo("my UART is not working", "I'm not sure what I see"))
    assert st.node.id == pp.UART_BRINGUP.entry and st.awaiting


# --- Live integration through the REAL mentor path ----------------------------------------------

def test_live_turn1_is_a_proof_path_not_a_chatbot(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, _convo("my UART is not working"), use_llm=False)
    assert "UART bring-up problem" in out                 # named the pattern
    assert "First proof step" in out and "0x55" in out    # gave the first proof step
    assert "Which UART/USART instance?" in out            # asked for required evidence
    assert "blink" not in out.lower()                     # NOT the generic board/blink default


def test_live_turn2_branches_on_evidence(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, _convo("my UART is not working", "TX pin is silent"), use_llm=False)
    assert "RX wiring and baud are NOT the problem" in out  # branched: RX not relevant yet
    assert "plain GPIO output" in out                       # the next proof step
    assert "no activity on the TX pin" in out               # narrated from normalised evidence


def test_live_paraphrase_branches_identically(tmp_path):
    """Proves it is evidence-driven, not phrase-patched: a different wording yields the same branch."""
    conn = _seeded(tmp_path)
    a = mentor_chat(conn, BOARD, _convo("my UART is not working", "TX pin is silent"), use_llm=False)
    b = mentor_chat(conn, BOARD, _convo("my UART is not working",
                                        "logic analyzer shows nothing on TX"), use_llm=False)
    assert "plain GPIO output" in a and "plain GPIO output" in b


def test_live_no_invented_board_facts(tmp_path):
    """The deterministic proof path never asserts board specifics — it asks for them. So no fabricated
    pin/register/clock can appear (the verifier guarantee, here by construction)."""
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, _convo("my UART is not working", "TX pin is silent"), use_llm=False)
    assert "MHz" not in out and "RCC->" not in out
    import re
    assert not re.search(r"0x[0-9a-fA-F]{6,}", out)        # no fabricated peripheral address
    assert not re.search(r"\bPA\d+\b", out)                # no fabricated specific pin


def test_non_pattern_conversation_is_unaffected(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, _convo("should I use HAL or bare metal?"), use_llm=False)
    assert "trade-off" in out.lower() and "UART bring-up problem" not in out


# --- Milestone 2: LLM-voiced proof path with verifier -------------------------------------------

class _FakeGw:
    """A gateway whose model returns a fixed string — to test voicing + verifier deterministically."""
    model = "fake"

    def __init__(self, text):
        self.text = text
        self.provider = self

    def available(self):
        return True

    def generate(self, system, prompt):
        return self.text


def test_packet_is_board_agnostic_and_keeps_the_proof_step():
    st = pp.resolve(_convo("my UART is not working"))
    pk = pp.build_packet(st, "STM32F103-BluePill")
    assert pk["stage"] == "intro"
    assert pk["proof_step"] == st.node.proof_step       # the engine's step, verbatim
    # the packet never states a board fact — only asks for them
    blob = pp.render_packet_for_prompt(pk)
    import re
    assert not re.search(r"\bP[A-K]\d", blob) and "MHz" not in blob and "USART1" not in blob


def test_verifier_passes_clean_voice_and_allows_generic_terms():
    pk = pp.build_packet(pp.resolve(_convo("my UART is not working")), "Board")
    clean = ("Okay, this is a UART bring-up problem — let's not chase everything at once. Send 0x55 in "
             "a loop and watch the TX pin with a scope. Which board and serial port are you on?")
    safe, violations = pp.verify_voiced(clean, pk)
    assert safe and violations == []


def test_verifier_blocks_invented_board_facts():
    pk = pp.build_packet(pp.resolve(_convo("my UART is not working")), "Board")
    dirty = "Set PA9 to USART1 alternate function, enable RCC on APB2, the clock is 72MHz at 0x40013800."
    safe, violations = pp.verify_voiced(dirty, pk)
    assert not safe
    joined = " ".join(violations)
    assert "PA9" in joined and "USART1" in joined and "72MHz" in joined and "0x40013800" in joined


def test_live_voiced_answer_is_used_when_clean(tmp_path):
    conn = _seeded(tmp_path)
    voice = ("Okay — this is a UART bring-up problem, and we'll take it one zone at a time. First, send "
             "0x55 in a loop and watch the TX pin on a scope. Tell me which board, serial instance, and "
             "TX/RX pins you're using?")
    out = mentor_chat(conn, BOARD, _convo("my UART is not working"),
                      use_llm=True, gateway=_FakeGw(voice))
    assert out == voice                                  # the mentor voice is shown


def test_live_voiced_answer_is_blocked_and_falls_back_when_unsafe(tmp_path):
    conn = _seeded(tmp_path)
    dirty = "Easy — just set PA9 as USART1 TX, turn on RCC APB2, it runs at 72MHz. Done."
    out = mentor_chat(conn, BOARD, _convo("my UART is not working"),
                      use_llm=True, gateway=_FakeGw(dirty))
    assert "PA9" not in out and "72MHz" not in out       # invented facts never reach the user
    assert "First proof step" in out and "0x55" in out   # safe deterministic render instead


def test_live_branch_authority_stays_with_engine_even_when_voicing(tmp_path):
    """The branch/next-step come from the DecisionNode, not the model: a dirty voice on a branch turn
    is blocked and the deterministic branch (RX-not-relevant, GPIO step) is what the user sees."""
    conn = _seeded(tmp_path)
    dirty = "It's PB6 on USART2 at 115200, clock 48MHz."
    out = mentor_chat(conn, BOARD, _convo("my UART is not working", "TX pin is silent"),
                      use_llm=True, gateway=_FakeGw(dirty))
    assert "RX wiring and baud are NOT the problem" in out and "plain GPIO output" in out


def test_offline_still_deterministic(tmp_path):
    conn = _seeded(tmp_path)
    out = mentor_chat(conn, BOARD, _convo("my UART is not working"), use_llm=False)
    assert "UART bring-up problem" in out and "First proof step" in out
