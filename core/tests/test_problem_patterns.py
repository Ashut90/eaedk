"""Problem-pattern engine — the general MATCH → ProofPathState → EvidenceEvent → DecisionNode loop,
with UART bring-up as the first curated seed. These test the MECHANISM (normalised-evidence branching,
stateful replay), not the literal example phrasings.
"""
from eaedk.store.db import connect
from eaedk.store.migrate import migrate
from eaedk.seed import seed_all
from eaedk import problem_patterns as pp
from eaedk.mentor_llm import mentor_chat, _voice_proof_path
from eaedk.community_cases import CommunityCase, rank_community_cases

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


class _CaptureGw(_FakeGw):
    """Capture proof-path voicing prompts while returning a safe mentor voice."""

    def __init__(self, text):
        super().__init__(text)
        self.system = ""
        self.prompt = ""

    def generate(self, system, prompt):
        self.system = system
        self.prompt = prompt
        return self.text


def _community_case(**overrides):
    defaults = dict(
        source="EE Stack Exchange",
        source_type="stackexchange",
        symptom="TX pin silent",
        context="",
        suspected_cause="wrong pinmux or alternate-function setting",
        confirmed_cause="",
        verification_step="Scope showed no activity on PA9; GPIOA->AFR confirmed AF1 in register dump",
        fix="Changed the alternate-function setting",
        evidence_quality="proven",
        confidence=0.9,
        reference_link="https://electronics.stackexchange.com/q/100",
    )
    defaults.update(overrides)
    return CommunityCase(**defaults)


def _community_report(*cases):
    return rank_community_cases(
        list(cases),
        local_evidence={"tx_activity": "absent"},
        local_symptoms=("TX is silent",),
    )


def test_packet_is_board_agnostic_and_keeps_the_proof_step():
    st = pp.resolve(_convo("my UART is not working"))
    pk = pp.build_packet(st, "STM32F103-BluePill")
    assert pk["stage"] == "intro"
    assert pk["proof_step"] == st.node.proof_step       # the engine's step, verbatim
    # the packet never states a board fact — only asks for them
    blob = pp.render_packet_for_prompt(pk)
    import re
    assert not re.search(r"\bP[A-K]\d", blob) and "MHz" not in blob and "USART1" not in blob


def test_packet_attaches_reduced_community_report_fields():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = _community_report(_community_case())
    pk = pp.build_packet(st, "Board", community_report=report)

    assert pk["community_confirm_cases"]
    assert pk["community_compare_cases"] == []
    assert pk["best_external_verification_step"]
    assert pk["source_links"] == ["https://electronics.stackexchange.com/q/100"]
    assert pk["community_summary_reason"]
    assert pk["has_actionable_external_experience"] is True


def test_community_report_does_not_change_local_proof_path_fields():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    local = pp.build_packet(st, "Board")
    report = _community_report(_community_case(verification_step="External check that must not replace local step"))
    with_community = pp.build_packet(st, "Board", community_report=report)

    for key in ("proof_step", "stage", "candidates", "rules_out", "observed",
                "board_name", "user_reported_evidence"):
        assert with_community.get(key) == local.get(key)


def test_empty_community_report_has_empty_fields_and_no_prompt_section():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = rank_community_cases([])
    pk = pp.build_packet(st, "Board", community_report=report)

    assert pk["community_confirm_cases"] == []
    assert pk["community_compare_cases"] == []
    assert pk["best_external_verification_step"] == ""
    assert pk["source_links"] == []
    assert pk["community_summary_reason"] == ""
    assert pk["has_actionable_external_experience"] is False
    assert "External field experience" not in pp.render_packet_for_prompt(pk)


def test_speculated_community_case_is_compare_only_and_never_best_step():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = _community_report(_community_case(
        evidence_quality="speculated",
        confidence=0.8,
        verification_step="Maybe configure PA9 as USART1 TX",
        reference_link="https://stackoverflow.com/q/200",
    ))
    pk = pp.build_packet(st, "Board", community_report=report)

    assert pk["community_confirm_cases"] == []
    assert pk["community_compare_cases"]
    assert pk["best_external_verification_step"] == ""
    assert pk["has_actionable_external_experience"] is False


def test_community_prompt_labels_unverified_field_experience_and_provenance():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    pk = pp.build_packet(st, "Board", community_report=_community_report(_community_case()))
    prompt = pp.render_packet_for_prompt(pk)

    assert "External field experience — unverified, source-backed, for CONFIRM/COMPARE only" in prompt
    assert "these cases are not verified board facts" in prompt
    assert "local proof step remains primary" in prompt
    assert "source links are provenance, not the answer" in prompt
    assert "speculated cases cannot drive HELP" in prompt
    assert "source links (provenance only, not the answer)" in prompt
    assert "https://electronics.stackexchange.com/q/100" in prompt


def test_community_packet_does_not_create_verified_board_facts_or_verifier_allowlist():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    pk = pp.build_packet(st, "Board", community_report=_community_report(_community_case()))
    prompt = pp.render_packet_for_prompt(pk)

    assert all("verified" not in key and "board_fact" not in key for key in pk)
    assert "PA9" not in prompt and "GPIOA->AFR" not in prompt and "AF1" not in prompt

    safe, violations = pp.verify_voiced(
        "A community case says configure PA9 and write GPIOA->AFR.", pk)
    assert not safe
    assert any("PA9" in v for v in violations)


def test_voice_proof_path_accepts_community_report_in_prompt():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = _community_report(_community_case())
    voice = ("You reported no activity on the TX pin. Next, drive that exact pin as a plain GPIO "
             "output and tell me if it moves.")
    gw = _CaptureGw(voice)

    out = _voice_proof_path(st, "Board", use_llm=True, gateway=gw, community_report=report)

    assert out == voice
    assert "External field experience — unverified, source-backed, for CONFIRM/COMPARE only" in gw.prompt
    assert "CONFIRM support cases:" in gw.prompt
    assert "https://electronics.stackexchange.com/q/100" in gw.prompt


def test_voice_proof_path_keeps_local_step_primary_and_external_step_secondary():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = _community_report(_community_case(verification_step="External scope check"))
    gw = _CaptureGw("Run the local proof step first: drive that exact pin as a plain GPIO output.")

    _voice_proof_path(st, "Board", use_llm=True, gateway=gw, community_report=report)

    local_line = f"next proof step (keep the ACTION intact): {st.node.proof_step}"
    assert local_line in gw.prompt
    assert "local proof step remains primary" in gw.prompt
    assert "best external verification step (secondary CONFIRM support only):" in gw.prompt
    assert gw.prompt.index(local_line) < gw.prompt.index("best external verification step")


def test_voice_proof_path_speculated_compare_cases_cannot_drive_help():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    report = _community_report(_community_case(
        evidence_quality="speculated",
        confidence=0.8,
        verification_step="Maybe configure PA9 as USART1 TX",
        reference_link="https://stackoverflow.com/q/200",
    ))
    gw = _CaptureGw("Run the local proof step and report what the pin does.")

    _voice_proof_path(st, "Board", use_llm=True, gateway=gw, community_report=report)

    assert "COMPARE support cases:" in gw.prompt
    assert "evidence speculated" in gw.prompt
    assert "speculated cases cannot drive HELP" in gw.prompt
    assert "best external verification step" not in gw.prompt
    assert "source links (provenance only, not the answer):" in gw.prompt
    assert "https://stackoverflow.com/q/200" in gw.prompt


def test_proof_voice_system_marks_community_as_unverified_support():
    st = pp.resolve(_convo("my UART is not working", "TX is silent"))
    gw = _CaptureGw("Run the local proof step and report the result.")

    _voice_proof_path(st, "Board", use_llm=True, gateway=gw,
                      community_report=_community_report(_community_case()))

    assert "external field experience" in gw.system
    assert "UNVERIFIED" in gw.system
    assert "local proof step remains primary" in gw.system
    assert "community verification steps are secondary CONFIRM support only" in gw.system
    assert "compare/speculated cases cannot drive HELP" in gw.system
    assert "source links are provenance, not the answer" in gw.system
    assert "do not turn community details into verified board facts" in gw.system
    assert "do not turn community pins/registers/clocks into instructions" in gw.system
    assert "do not choose or alter confidence" in gw.system


def test_proof_path_voice_modules_add_no_web_or_rag_imports():
    import eaedk.mentor_llm as ml
    import re
    source = open(ml.__file__).read()
    web_imports = re.findall(r"^\s*(?:import|from)\s+(?:http|requests|urllib|aiohttp|httpx)",
                             source, re.MULTILINE)
    assert not web_imports
    assert not re.findall(r"^\s*(?:import|from)\s+.*rag", source, re.MULTILINE | re.I)


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


# --- Verifier generalization: peripheral- and vendor-agnostic (no SPI pattern needed) ------------

import pytest


@pytest.mark.parametrize("voiced, needle, category", [
    ("Use SPI2 on PB13/PB14/PB15.",        "SPI2",        "peripheral instance"),
    ("Set I2C1 on PB6/PB7.",               "I2C1",        "peripheral instance"),
    ("On ESP32 just use GPIO21.",          "GPIO21",      "pin"),
    ("Set the SPI clock to 10MHz.",        "10MHz",       "clock frequency"),
    ("Write register SPI_CR1 to enable.",  "SPI_CR1",     "register"),
    ("Use address 0x40013000 for the peripheral.", "0x40013000", "address"),
    ("Configure PORTB and DDRB on the AVR.",       "PORTB",      "register"),
    ("Wire it to Arduino pin D13.",        "D13",         "pin"),
])
def test_verifier_blocks_invented_non_uart_claims(voiced, needle, category):
    """A UART packet is enough — the verifier is pattern-independent. These prove it protects future
    SPI/I2C/ESP32/AVR/Arduino patterns with no peripheral-specific code and no SPI pattern added."""
    pk = pp.build_packet(pp.resolve(_convo("my UART is not working")), "Board")
    safe, violations = pp.verify_voiced(voiced, pk)
    assert not safe
    assert any(needle.lower() in v.lower() for v in violations)
    assert any(category in v for v in violations)


def test_verifier_allows_generic_peripheral_talk_without_specifics():
    """Naming a peripheral family WITHOUT an instance number / pin / register is fine — the mentor may
    say 'check your SPI bus', just not invent 'SPI2 on PB13'."""
    pk = pp.build_packet(pp.resolve(_convo("my UART is not working")), "Board")
    safe, violations = pp.verify_voiced(
        "Check your SPI bus and the I2C lines, then watch the timer output.", pk)
    assert safe and violations == []


def test_pattern_can_declare_extra_sensitive_terms():
    """A ProblemPattern may declare its own forbidden claims (e.g. a vendor API). build_packet carries
    them; verify_voiced enforces them — the per-pattern extension point, no engine change."""
    assert pp.build_packet(pp.resolve(_convo("my UART is not working")), "B")["sensitive_terms"] == []
    pk = pp.build_packet(pp.resolve(_convo("my UART is not working")), "B")
    pk["sensitive_terms"] = [r"\bvTaskDelay\b", r"\besp_wifi_init\b"]
    safe, viol = pp.verify_voiced("Just call vTaskDelay() and esp_wifi_init().", pk)
    assert not safe and any("vTaskDelay" in v for v in viol)


# --- User-reported evidence provenance (docs/31 extension) ---------------------------------------
#
# User-reported hardware tokens (pins, instances, clocks) are allowed ONLY when phrased as quotes:
# "you said PA9", "you reported SPI2". The same tokens are BLOCKED when phrased as instructions.

def _urevidence_convo(*user_msgs):
    """Build a transcript: first turn matches the UART pattern, subsequent turns carry user-reported
    hardware tokens. Returns the transcript."""
    msgs = []
    for i, t in enumerate(("my UART is not working",) + user_msgs):
        if i:
            msgs.append({"role": "assistant", "content": "..."})
        msgs.append({"role": "user", "content": t})
    return msgs


# Test 1: User reports PA9 → LLM says "You said TX is on PA9." → ALLOWED
def test_user_reported_pin_quoted_is_allowed():
    msgs = _urevidence_convo("I use PA9 for TX")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("You said TX is on PA9.", pk)
    assert safe, f"Expected ALLOWED, got violations: {violations}"


# Test 2: User reports PA9 → LLM says "Configure PA9 as USART1 TX." → BLOCKED
def test_user_reported_pin_asserted_is_blocked():
    msgs = _urevidence_convo("I use PA9 for TX")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("Configure PA9 as USART1 TX.", pk)
    assert not safe, "Expected BLOCKED, got no violations"
    assert any("PA9" in v for v in violations)


# Test 3: User reports SPI2/PB13/PB14/PB15 → LLM says "You reported…" → ALLOWED
def test_user_reported_spi_quoted_is_allowed():
    msgs = _urevidence_convo("SPI2 on PB13/PB14/PB15")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("You reported SPI2 on PB13/PB14/PB15.", pk)
    assert safe, f"Expected ALLOWED, got violations: {violations}"


# Test 4: User did NOT report SPI2 → LLM says "Use SPI2…" → BLOCKED
def test_unreported_peripheral_asserted_is_blocked():
    msgs = _urevidence_convo("TX is silent")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("Use SPI2 on PB13/PB14/PB15.", pk)
    assert not safe, "Expected BLOCKED, got no violations"
    assert any("SPI2" in v for v in violations)


# Test 5: User reports 10MHz clock → LLM says "You reported a 10MHz clock." → ALLOWED
def test_user_reported_clock_quoted_is_allowed():
    msgs = _urevidence_convo("the clock is 10MHz")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("You reported a 10MHz clock.", pk)
    assert safe, f"Expected ALLOWED, got violations: {violations}"


# Test 6: User did NOT report a clock → LLM says "Set the clock to 10MHz." → BLOCKED
def test_unreported_clock_asserted_is_blocked():
    msgs = _urevidence_convo("TX is silent")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    safe, violations = pp.verify_voiced("Set the clock to 10MHz.", pk)
    assert not safe, "Expected BLOCKED, got no violations"
    assert any("10MHz" in v for v in violations)


def test_user_reported_evidence_in_packet():
    """The packet carries user_reported_evidence from the transcript."""
    msgs = _urevidence_convo("I use PA9 for TX")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    assert "user_reported_evidence" in pk
    assert "PA9" in pk["user_reported_evidence"]


def test_user_reported_evidence_empty_when_none_given():
    """No user hardware tokens → user_reported_evidence is empty."""
    msgs = _urevidence_convo("I'm not sure what I see")
    st = pp.resolve(msgs)
    pk = pp.build_packet(st, "Board")
    assert pk.get("user_reported_evidence", []) == []
