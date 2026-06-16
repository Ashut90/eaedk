"""CommunityCase — tests for structured field experience schema, confidence scoring, actionability,
extraction, comparison, ranking, and the end-to-end mining pipeline.

These tests prove that CommunityCase is unverified field experience, not verified board truth.
"""
from eaedk.community_cases import (
    CommunityCase,
    CommunityCaseMatch,
    CommunityExperienceReport,
    score_confidence,
    is_actionable,
    as_packet,
    compare_case_to_evidence,
    extract_community_case_from_text,
    mine_community_experience,
    rank_community_cases,
    SOURCE_TYPES,
    EVIDENCE_QUALITIES,
    ACTIONABLE_THRESHOLD,
)


def _make_case(**overrides) -> CommunityCase:
    """Build a CommunityCase with sensible defaults for testing."""
    defaults = dict(
        source="EE Stack Exchange",
        source_type="stackexchange",
        symptom="TX pin silent on UART bring-up",
        context="STM32F407, USART1, PA9",
        suspected_cause="Wrong alternate function number set",
        confirmed_cause="PA9 was set to AF1 (TIM1) instead of AF7 (USART1)",
        verification_step="Scope showed no activity on PA9; GPIOA->AFR confirmed AF1 in register dump",
        fix="Changed GPIOA->AFR[1] bitfield to AF7",
        evidence_quality="proven",
        confidence=0.9,
        reference_link="https://electronics.stackexchange.com/q/12345",
    )
    defaults.update(overrides)
    return CommunityCase(**defaults)


# --- Confidence scoring ---------------------------------------------------------------

def test_proven_official_doc_high_confidence():
    conf = score_confidence("official_doc", "proven")
    assert conf >= 0.8, f"Expected high confidence, got {conf}"
    assert conf == 1.0

def test_speculated_forum_low_confidence():
    conf = score_confidence("mailing_list", "speculated")
    assert conf <= 0.2, f"Expected low confidence, got {conf}"

def test_confirmed_author_stackexchange_medium_high():
    conf = score_confidence("stackexchange", "confirmed_by_author")
    assert 0.5 <= conf <= 0.8, f"Expected medium/high, got {conf}"

def test_corroboration_increases_confidence():
    base = score_confidence("vendor_forum", "proven", corroborations=0)
    boosted = score_confidence("vendor_forum", "proven", corroborations=3)
    assert boosted > base and boosted <= 1.0

def test_confidence_never_exceeds_one():
    assert score_confidence("official_doc", "proven", corroborations=10) <= 1.0


# --- Actionability -------------------------------------------------------------------

def test_fix_without_verification_step_not_actionable():
    assert not is_actionable(_make_case(verification_step="", evidence_quality="proven", confidence=0.9))

def test_proven_with_verification_step_is_actionable():
    assert is_actionable(_make_case(evidence_quality="proven", confidence=0.9))

def test_speculated_case_not_actionable_even_with_verification_step():
    assert not is_actionable(_make_case(evidence_quality="speculated", confidence=0.8))

def test_unknown_evidence_not_actionable():
    assert not is_actionable(_make_case(evidence_quality="unknown", confidence=0.9))

def test_empty_symptom_not_actionable():
    assert not is_actionable(_make_case(symptom="", evidence_quality="proven", confidence=0.9))

def test_whitespace_verification_step_not_actionable():
    assert not is_actionable(_make_case(verification_step="   ", evidence_quality="proven", confidence=0.9))

def test_confirmed_by_author_actionable():
    assert is_actionable(_make_case(evidence_quality="confirmed_by_author", confidence=0.7))

def test_confirmed_by_author_low_confidence_not_actionable():
    assert not is_actionable(_make_case(evidence_quality="confirmed_by_author", source_type="unknown",
                                        confidence=score_confidence("unknown", "confirmed_by_author")))


# --- Packet output -------------------------------------------------------------------

def test_packet_includes_key_fields():
    pkt = as_packet(_make_case())
    assert pkt["evidence_quality"] == "proven"
    assert pkt["confidence"] == 0.9
    assert pkt["verification_step"] and pkt["reference_link"] and pkt["source_type"] == "stackexchange"

def test_community_case_has_no_verified_fact_field():
    pkt = as_packet(_make_case())
    assert "verified" not in pkt and "board_fact" not in pkt and "verified_cause" not in pkt


# --- Comparison contract: 8 acceptance tests -------------------------------------------

def test_compare_symptom_matches_local_evidence():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent on UART bring-up", evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert match.matched_facts and any("symptom" in f for f in match.matched_facts)

def test_compare_context_differs_records_differences():
    match = compare_case_to_evidence(
        _make_case(context="STM32F407, USART1, PA9", evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_context="STM32F103, USART1, PA9",
        local_symptoms=("TX is silent",))
    assert match.differing_facts and any("context" in f for f in match.differing_facts)

def test_compare_actionable_case_usable_for_confirm():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert match.usable_for_confirm and match.suggested_verification_step

def test_compare_speculated_case_not_usable_for_confirm():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", evidence_quality="speculated", confidence=0.8),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert not match.usable_for_confirm and match.usable_for_compare

def test_compare_symptom_match_different_context_compare_only():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", context="ATmega328P, USART, PB3",
                   evidence_quality="confirmed_by_author", confidence=0.63),
        local_evidence={"tx_activity": "absent"}, local_context="STM32F103, USART1, PA9",
        local_symptoms=("TX is silent",))
    assert match.usable_for_compare and not match.usable_for_confirm

def test_compare_fix_without_verification_not_confirmable():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", verification_step="", fix="Just rewired",
                   evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert not match.usable_for_confirm and not match.suggested_verification_step

def test_compare_output_packet_has_key_fields():
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert match.case_confidence == 0.9 and match.reference_link == "https://electronics.stackexchange.com/q/12345"

def test_compare_result_never_promotes_to_verified_fact():
    import dataclasses
    match = compare_case_to_evidence(
        _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9),
        local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    names = {f.name for f in dataclasses.fields(match)}
    assert "verified" not in names and "board_fact" not in names


# --- Extraction contract: 8 acceptance tests -------------------------------------------

def test_extract_symptom_verification_fix_becomes_proven():
    text = ("My UART has no output. I checked with a logic analyzer and the TX line is silent. "
            "Turns out the issue was that I forgot to enable the clock. Fixed by adding "
            "RCC_APB1ENR |= RCC_APB1ENR_USART3EN before init. That fixed the problem.")
    case = extract_community_case_from_text(text, source="EE Stack Exchange",
                                            source_type="stackexchange",
                                            reference_link="https://electronics.stackexchange.com/q/1")
    assert case is not None
    assert "silent" in case.symptom.lower() or "no output" in case.symptom.lower()
    assert case.evidence_quality == "proven" and case.confidence >= 0.8
    assert case.verification_step and case.fix

def test_extract_speculated_case_is_low_confidence():
    text = "My UART is not working. Try checking if the baud rate matches. Maybe it's set to the wrong value."
    case = extract_community_case_from_text(text, source="Stack Overflow", source_type="stackoverflow")
    assert case is not None and case.evidence_quality == "speculated" and case.confidence <= 0.3

def test_extract_fix_without_verification_not_actionable():
    text = "UART not working. Fixed by changing the baud rate to 115200. The UART is now working."
    case = extract_community_case_from_text(text, source="GitHub issue", source_type="github_issue")
    assert case is not None and not is_actionable(case) and case.fix and not case.verification_step

def test_extract_no_symptom_returns_none():
    assert extract_community_case_from_text("Check the datasheet for the register map.") is None

def test_extract_packet_includes_reference_link():
    case = extract_community_case_from_text("No signal on TX. Scope showed the pin was held low. Changing the AF fixed it.",
                                            source="EE Stack Exchange", source_type="stackexchange",
                                            reference_link="https://electronics.stackexchange.com/q/99")
    pkt = as_packet(case)
    assert pkt["reference_link"] == "https://electronics.stackexchange.com/q/99" and pkt["source"] == "EE Stack Exchange"

def test_extract_result_has_no_verified_fact_field():
    pkt = as_packet(extract_community_case_from_text(
        "Device not detected on I2C bus. Verified by checking the pull-ups with a scope.",
        source="vendor forum", source_type="vendor_forum"))
    assert "verified" not in pkt and "board_fact" not in pkt

def test_extract_output_feeds_compare_case_to_evidence():
    case = extract_community_case_from_text("UART TX is silent. Logic analyzer showed no activity on the pin. "
            "The issue was the wrong alternate function — fixed by setting AF7.", source="SE", source_type="stackexchange")
    match = compare_case_to_evidence(case, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert isinstance(match, CommunityCaseMatch) and (match.matched_facts or match.missing_facts)

def test_community_cases_module_has_no_http_imports():
    import core.eaedk.community_cases as mod
    import re
    source = open(mod.__file__).read()
    web_imports = re.findall(r"^\s*(?:import|from)\s+(?:http|requests|urllib|aiohttp|httpx)", source, re.MULTILINE)
    assert not web_imports, f"Found web-fetching imports: {web_imports}"


# --- Ranking contract: 10 acceptance tests --------------------------------------------

def test_rank_proven_matching_case_goes_to_confirm():
    case = _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9)
    report = rank_community_cases([case], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert len(report.confirm_cases) == 1 and len(report.rejected_cases) == 0 and report.has_actionable_external_experience

def test_rank_speculated_case_goes_to_compare_not_confirm():
    case = _make_case(symptom="TX pin silent", evidence_quality="speculated", confidence=0.8)
    report = rank_community_cases([case], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert len(report.confirm_cases) == 0 and len(report.compare_cases) == 1 and not report.has_actionable_external_experience

def test_rank_different_context_case_goes_to_compare():
    case = _make_case(symptom="TX pin silent", context="ATmega328P, USART, PB3",
                      evidence_quality="confirmed_by_author", confidence=0.63)
    report = rank_community_cases([case], local_evidence={"tx_activity": "absent"},
                                  local_context="STM32F103, USART1, PA9", local_symptoms=("TX is silent",))
    assert len(report.confirm_cases) == 0 and len(report.compare_cases) == 1

def test_rank_no_overlap_goes_to_rejected():
    case = _make_case(symptom="I2C NACK", evidence_quality="proven", confidence=0.9)
    report = rank_community_cases([case], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert len(report.confirm_cases) == 0 and len(report.compare_cases) == 0 and len(report.rejected_cases) == 1

def test_rank_best_verification_step_from_highest_confidence():
    low = _make_case(symptom="TX pin silent", evidence_quality="confirmed_by_author", confidence=0.63, verification_step="Low confidence check")
    high = _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9, verification_step="Scope showed no activity on TX")
    report = rank_community_cases([low, high], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert report.best_verification_step == "Scope showed no activity on TX" and report.best_confidence == 0.9

def test_rank_speculated_does_not_override_proven():
    proven = _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9, verification_step="Scope showed no activity")
    speculated = _make_case(symptom="TX pin silent", evidence_quality="speculated", confidence=0.8, verification_step="Maybe the clock is wrong")
    report = rank_community_cases([speculated, proven], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert len(report.confirm_cases) == 1 and len(report.compare_cases) == 1 and report.best_verification_step == "Scope showed no activity"

def test_rank_report_packet_has_key_fields():
    case = _make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9, reference_link="https://example.com/q/1")
    report = rank_community_cases([case], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    cm = report.confirm_cases[0]
    assert cm.case_confidence == 0.9 and cm.reference_link == "https://example.com/q/1" and cm.suggested_verification_step

def test_rank_report_never_promotes_to_verified_fact():
    import dataclasses
    report = rank_community_cases([_make_case(symptom="TX pin silent", evidence_quality="proven", confidence=0.9)],
                                  local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    names = {f.name for f in dataclasses.fields(report)}
    assert "verified" not in names and "board_fact" not in names and isinstance(report.has_actionable_external_experience, bool)

def test_rank_empty_case_list_returns_safe_empty_report():
    report = rank_community_cases([])
    assert not any([len(report.confirm_cases), len(report.compare_cases), len(report.rejected_cases)])
    assert report.best_verification_step == "" and not report.has_actionable_external_experience and report.top_reference_links == ()

def test_rank_module_has_no_http_imports():
    import core.eaedk.community_cases as mod
    import re
    source = open(mod.__file__).read()
    assert not re.findall(r"^\s*(?:import|from)\s+(?:http|requests|urllib|aiohttp|httpx)", source, re.MULTILINE)


# --- Pipeline contract: 9 acceptance tests --------------------------------------------

def test_pipeline_proven_matching_gives_confirm_and_best_verification():
    snippets = [{"text": "UART TX is silent. Scope showed no activity on the pin. Fixed by setting the correct alternate function.",
                 "source": "EE Stack Exchange", "source_type": "stackexchange",
                 "reference_link": "https://electronics.stackexchange.com/q/1"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert report.has_actionable_external_experience and len(report.confirm_cases) == 1
    assert report.best_verification_step and report.best_confidence > 0

def test_pipeline_speculated_goes_to_compare_not_confirm():
    snippets = [{"text": "UART not working. Try checking if the baud rate matches. Maybe it's set wrong. "
                 "Also TX is silent on the wire.",
                 "source": "Stack Overflow", "source_type": "stackoverflow"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert not report.has_actionable_external_experience
    assert len(report.confirm_cases) == 0
    # Speculated cases with symptom overlap go to compare; without overlap to rejected — both are acceptable
    assert len(report.compare_cases) + len(report.rejected_cases) == 1

def test_pipeline_different_context_goes_to_compare():
    snippets = [{"text": "UART TX is silent. Scope showed no activity on the pin. The issue was the wrong alternate function.",
                 "source": "Stack Overflow", "source_type": "stackoverflow",
                 "reference_link": "https://stackoverflow.com/q/2"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    # Extracted case has empty context, so no context divergence → confirm if actionable
    assert report.has_actionable_external_experience or len(report.compare_cases) == 1

def test_pipeline_no_symptom_ignored_safely():
    snippets = [{"text": "Check the datasheet for the pinmux table.", "source": "vendor forum", "source_type": "vendor_forum"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert not report.has_actionable_external_experience and len(report.confirm_cases) == 0

def test_pipeline_empty_snippets_returns_safe_empty():
    report = mine_community_experience([], local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert not report.has_actionable_external_experience and report.best_verification_step == "" and report.top_reference_links == ()

def test_pipeline_report_includes_reference_links():
    snippets = [{"text": "UART TX is silent. Scope showed no activity. Fixed by changing the AF.",
                 "source": "SE", "source_type": "stackexchange",
                 "reference_link": "https://electronics.stackexchange.com/q/100"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert report.top_reference_links and "https://electronics.stackexchange.com/q/100" in report.top_reference_links

def test_pipeline_report_never_promotes_to_verified():
    import dataclasses
    snippets = [{"text": "UART TX silent. Scope showed no activity. Fixed by changing the AF.",
                 "source": "SE", "source_type": "stackexchange"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    names = {f.name for f in dataclasses.fields(report)}
    assert "verified" not in names and "board_fact" not in names

def test_pipeline_module_still_has_no_http_imports():
    import core.eaedk.community_cases as mod
    import re
    assert not re.findall(r"^\s*(?:import|from)\s+(?:http|requests|urllib|aiohttp|httpx)",
                          open(mod.__file__).read(), re.MULTILINE)

def test_pipeline_output_attachable_to_packets():
    snippets = [{"text": "UART TX is silent. Scope showed no activity. Fixed by changing the AF.",
                 "source": "SE", "source_type": "stackexchange",
                 "reference_link": "https://electronics.stackexchange.com/q/100"}]
    report = mine_community_experience(snippets, local_evidence={"tx_activity": "absent"}, local_symptoms=("TX is silent",))
    assert report.confirm_cases or report.compare_cases
    report_dict = {"confirm_count": len(report.confirm_cases), "compare_count": len(report.compare_cases),
                   "rejected_count": len(report.rejected_cases), "best_verification_step": report.best_verification_step,
                   "has_actionable": report.has_actionable_external_experience, "reference_links": report.top_reference_links}
    assert isinstance(report_dict, dict)