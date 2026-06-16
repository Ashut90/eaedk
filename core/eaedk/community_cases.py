"""CommunityCase — structured field experience for the Navigator's CONFIRM and COMPARE stages.

This module defines the executable boundary for community-mined engineering cases. It is NOT:
- a web fetcher (no HTTP calls)
- a RAG system (no retrieve-and-summarise)
- a replacement for the Navigator's local routing

It IS:
- a structured case schema for community-mined experience
- deterministic confidence scoring (not LLM-decided)
- a helper to filter actionable cases

A CommunityCase is UNVERIFIED FIELD EXPERIENCE. It can only suggest a candidate cause or a
verification step that the user then PROVES through the local proof path. It never becomes a
"verified board fact." The existing verifier still blocks any board-specific claim not grounded
in verified facts.

Usage: CommunityCase feeds the CONFIRM and COMPARE stages of MATCH → SORT → CONFIRM → ORGANIZE
→ COMPARE → HELP. The Navigator always decides; community cases only augment these two stages.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# --- Source type — where the case came from ---------------------------------------------

SOURCE_TYPES = (
    "stackexchange",
    "stackoverflow",
    "vendor_forum",
    "github_issue",
    "mailing_list",
    "rtos_forum",
    "official_doc",
    "unknown",
)

# Source quality tiers — feed into confidence scoring.
# Official docs and peer-reviewed answers outrank forums and speculation.
SOURCE_QUALITY: dict[str, float] = {
    "official_doc":   1.0,   # datasheet, app note, reference manual
    "stackexchange":  0.9,   # peer-reviewed, voted answers
    "stackoverflow":  0.85,  # peer-reviewed, voted answers
    "vendor_forum":   0.8,   # vendor staff may respond
    "github_issue":   0.7,   # maintainer may confirm
    "rtos_forum":     0.65,  # community expertise
    "mailing_list":   0.6,   # informal, low signal:noise
    "unknown":        0.4,   # no provenance
}


# --- Evidence quality — was the cause actually proven? -----------------------------------

EVIDENCE_QUALITIES = (
    "proven",               # someone measured / scoped / confirmed with proof
    "confirmed_by_author",  # the author confirmed but no measurement shown
    "speculated",           # a guess, not confirmed
    "unknown",              # no evidence claim at all
)

# Evidence quality multipliers — applied on top of source quality.
EVIDENCE_QUALITY_MULT: dict[str, float] = {
    "proven":              1.0,   # someone actually measured
    "confirmed_by_author": 0.7,   # author said so, no measurement
    "speculated":          0.25,  # a guess
    "unknown":             0.1,   # no evidence claim
}


# --- Actionable confidence threshold -------------------------------------------------

ACTIONABLE_THRESHOLD = 0.5


# --- The CommunityCase schema ---------------------------------------------------------

@dataclass(frozen=True)
class CommunityCase:
    """A structured engineering case mined from community text.

    This is UNVERIFIED FIELD EXPERIENCE, not truth. It can only *suggest* a candidate cause or a
    verification step that the user then PROVES through the local proof path. It never becomes a
    verified board fact, and the existing verifier still blocks any board-specific claim it would
    inject that isn't grounded.
    """

    source: str                         # e.g. "EE Stack Exchange", forum/repo name
    source_type: str                    # one of SOURCE_TYPES
    symptom: str                        # the observable, normalised toward EvidenceVar vocabulary
    context: str                        # board/MCU/peripheral/toolchain it occurred on
    suspected_cause: str                # what was hypothesised
    confirmed_cause: str                # the cause actually proven (may be empty)
    verification_step: str              # the proof that confirmed/rejected it
    fix: str                            # what resolved it
    evidence_quality: str               # one of EVIDENCE_QUALITIES
    confidence: float                   # deterministic score, 0.0–1.0
    reference_link: str                 # provenance for the human — never pasted into the answer


# --- Deterministic confidence scoring -------------------------------------------------

def score_confidence(source_type: str, evidence_quality: str,
                     corroborations: int = 0) -> float:
    """Derive a confidence score from source quality, evidence quality, and corroboration.

    Deterministic — not LLM-decided. The formula is:

        score = source_quality × evidence_quality_mult × corroboration_boost

    where corroboration_boost = min(1 + 0.15 × corroborations, 1.5).

    A case with 'proven' evidence from an 'official_doc' scores highest.
    A speculated forum post scores lowest.

    Args:
        source_type: one of SOURCE_TYPES
        evidence_quality: one of EVIDENCE_QUALITIES
        corroborations: number of independent sources that report the same cause (0 = single source)

    Returns:
        A float between 0.0 and 1.0.
    """
    sq = SOURCE_QUALITY.get(source_type, 0.4)
    eq = EVIDENCE_QUALITY_MULT.get(evidence_quality, 0.1)
    corroboration_boost = min(1.0 + 0.15 * max(0, corroborations), 1.5)
    raw = sq * eq * corroboration_boost
    return round(min(raw, 1.0), 2)


# --- Actionability check ---------------------------------------------------------------

def is_actionable(case: CommunityCase,
                  threshold: float = ACTIONABLE_THRESHOLD) -> bool:
    """Is this case actionable enough to reach the user?

    Actionable means:
    - The case has a symptom (it maps to a real problem)
    - It has a verification_step (the most valuable field)
    - Its evidence quality is at least 'confirmed_by_author' (not speculated or unknown)
    - Its confidence passes the threshold

    A case with a fix but no verification_step is NOT actionable — the fix is anecdotal
    without proof.
    """
    if not case.symptom.strip():
        return False
    if not case.verification_step.strip():
        return False
    if case.evidence_quality not in ("proven", "confirmed_by_author"):
        return False
    return case.confidence >= threshold


# --- Packet output for future integration --------------------------------------------

def as_packet(case: CommunityCase) -> dict:
    """Convert a CommunityCase to a dict suitable for attaching to Navigator packets
    (ProblemPattern, LearningMap, DecisionMap). This dict is structured — not a link, not
    a page summary.

    The packet includes evidence_quality, confidence, verification_step, and reference_link
    so downstream consumers can present the case as field experience, never as verified truth.
    """
    return {
        "source": case.source,
        "source_type": case.source_type,
        "symptom": case.symptom,
        "context": case.context,
        "suspected_cause": case.suspected_cause,
        "confirmed_cause": case.confirmed_cause,
        "verification_step": case.verification_step,
        "fix": case.fix,
        "evidence_quality": case.evidence_quality,
        "confidence": case.confidence,
        "reference_link": case.reference_link,
    }


# --- CommunityCase comparison contract ------------------------------------------------
#
# Deterministic comparison of a mined CommunityCase against the user's local context.
# This feeds the CONFIRM and COMPARE stages of the Navigator loop.
# No LLM, no web fetching, no RAG — pure structured comparison.

@dataclass(frozen=True)
class CommunityCaseMatch:
    """Result of comparing a CommunityCase against local evidence/context.

    A CommunityCaseMatch is never verified board truth. It describes the OVERLAP between
    a community-mined case and the user's local state, to help the Navigator decide whether
    this case is useful for CONFIRM (suggested proof step) or COMPARE (alternative cause).
    """

    # What overlaps / diverges
    matched_facts: tuple[str, ...]        # case fields that overlap with local evidence
    differing_facts: tuple[str, ...]      # case fields that conflict with local context
    missing_facts: tuple[str, ...]        # local context details the case does not address

    # Actionability for the two Navigator stages
    usable_for_confirm: bool              # True if case can suggest a proof step
    usable_for_compare: bool              # True if case can be compared even if not confirmable

    # Derived values
    case_confidence: float                # the case's confidence score, carried through
    suggested_verification_step: str      # the verification_step from the case, if actionable

    # Provenance — not the answer
    reference_link: str                   # for human reference, never pasted into output
    reason: str                           # human-readable explanation of the comparison result


def _tokenise(text: str) -> set[str]:
    """Lowercase, split into word tokens for simple comparison. No NLP — just word overlap."""
    import re
    low = (text or "").lower()
    return set(re.findall(r"[a-z0-9]+", low))


# --- Extraction from raw community text ------------------------------------------------
#
# Deterministic, pattern-based extraction. Not NLP. Returns a CommunityCase only when
# enough structure exists. Never summarises a page. Never creates verified board facts.

import re as _re

# Symptom phrases — what to look for in the text
_SYMPTOM_PATTERNS = (
    r"no waveform",
    r"no ack",
    r"not acknowledged",
    r"kernel panic",
    r"does not boot",
    r"won't boot",
    r"wrong baud",
    r"device not detected",
    r"not detected",
    r"no signal",
    r"no output",
    r"silent",
    r"garbled output",
    r"not working",
    r"doesn't work",
    r"no communication",
    r"no data",
    r"fails to initialise",
    r"hangs",
    r"crashes",
    r"reset loop",
    r"no activity",
    r"no response",
    r"tx is silent",
    r"rx is silent",
    r"no clock",
    r"wrong clock",
    r"timed out",
    r"timeout",
)

# Verification step phrases — proof language
_VERIFICATION_PATTERNS = (
    r"measured",
    r"checked with (?:logic )?analyzer",
    r"checked with (?:a )?scope",
    r"scope showed",
    r"logic analyzer showed",
    r"confirmed by",
    r"verified by",
    r"the fix was confirmed",
    r"fix was confirmed",
    r"the problem was confirmed",
    r"fixed (?:the |when )",
    r"proven by",
    r"register dump",
    r"register read",
    r"confirmed (?:that|the )",
    r"verified (?:that|the )",
    r"after (?:checking|measuring|looking)",
    r"turned out to be",
)

# Fix phrases — resolution language
_FIX_PATTERNS = (
    r"fixed by",
    r"resolved by",
    r"the (?:solution|fix) was",
    r"solution is",
    r"changing .{3,30} fixed",
    r"fix was to",
    r"the fix (?:is|was) to",
    r"changed .{3,40} and (?:it|that|the)",
    r"after (?:changing|replacing|removing|adding|setting)",
)

# Speculation phrases — indicates the answer is a guess, not proven
_SPECULATED_PATTERNS = (
    r"\btry (?:this|that|checking|the )",
    r"\btry (?:setting|adding|removing|changing)",
    r"\bmaybe",
    r"\bprobably",
    r"\bcould be",
    r"\bmight be",
    r"\bcheck (?:if |whether )",
    r"\byou might want to",
    r"\bthis might work",
    r"\bshould work if",
)

# Confirmation phrases — author says it solved the issue
_CONFIRMATION_PATTERNS = (
    r"that (?:fixed|solved|resolved|worked|was the issue|was the problem|was the cause)",
    r"this fixed",
    r"that worked",
    r"it worked",
    r"problem solved",
    r"issue resolved",
    r"that was (?:the|it)",
    r"turns out (?:it|that|this) was",
)


def extract_community_case_from_text(
    text: str,
    source: str = "",
    source_type: str = "unknown",
    reference_link: str = "",
) -> CommunityCase | None:
    """Deterministically extract a CommunityCase candidate from provided community text.

    This is NOT full NLP — it uses simple pattern matching for symptom, verification step,
    fix, and evidence quality. Returns None when no clear symptom is found.

    Returns a CommunityCase with deterministic confidence scoring applied. The case is
    UNVERIFIED FIELD EXPERIENCE, never a verified board fact. Source/reference_link are
    attached only as provenance.

    Args:
        text: the raw community text (forum post, issue comment, answer, etc.)
        source: e.g. "EE Stack Exchange"
        source_type: one of SOURCE_TYPES
        reference_link: provenance URL

    Returns:
        CommunityCase or None if no clear symptom is found.
    """
    if not text or not text.strip():
        return None

    low = text.lower()

    # --- Extract symptom ---
    symptom_matches = _find_symptom(low)
    if not symptom_matches:
        return None
    symptom = "; ".join(symptom_matches[:3])  # top 3 symptoms

    # --- Extract verification step ---
    verification = _find_verification(text)

    # --- Extract fix ---
    fix = _find_fix(text)

    # --- Determine evidence quality ---
    evidence_quality = _determine_evidence_quality(low, verification, fix)

    # --- Determine suspected cause ---
    cause = _find_cause(text)

    # --- Compute confidence ---
    confidence = score_confidence(source_type, evidence_quality)

    return CommunityCase(
        source=source,
        source_type=source_type,
        symptom=symptom,
        context="",
        suspected_cause=cause,
        confirmed_cause="",
        verification_step=verification,
        fix=fix,
        evidence_quality=evidence_quality,
        confidence=confidence,
        reference_link=reference_link,
    )


def _find_symptom(low: str) -> list[str]:
    """Extract symptom phrases from text. Returns the matching phrases."""
    found = []
    for pattern in _SYMPTOM_PATTERNS:
        m = _re.search(pattern, low)
        if m:
            phrase = m.group(0)
            if phrase not in found:
                found.append(phrase)
        if len(found) >= 3:
            break
    return found


def _find_verification(text: str) -> str:
    """Extract the sentence containing verification/proof language."""
    for sent in _re.split(r"[.!]", text):
        for pattern in _VERIFICATION_PATTERNS:
            if _re.search(pattern, sent, _re.I):
                return sent.strip()
    return ""


def _find_fix(text: str) -> str:
    """Extract the sentence containing resolution/fix language."""
    for sent in _re.split(r"[.!]", text):
        for pattern in _FIX_PATTERNS:
            if _re.search(pattern, sent, _re.I):
                return sent.strip()
    return ""


def _determine_evidence_quality(low: str, verification: str, fix: str) -> str:
    """Determine evidence quality from text signals."""
    has_verification = bool(verification)
    has_fix = bool(fix)

    # Check for speculation
    is_speculated = any(_re.search(p, low) for p in _SPECULATED_PATTERNS)
    if is_speculated:
        return "speculated"

    # Check for confirmation that it worked
    has_confirmation = any(_re.search(p, low) for p in _CONFIRMATION_PATTERNS)

    # Determine quality
    if has_verification and has_fix and has_confirmation:
        return "proven"
    if has_verification and has_fix:
        return "confirmed_by_author"
    if has_fix and has_confirmation:
        return "confirmed_by_author"
    if has_verification:
        return "confirmed_by_author"
    if has_fix:
        return "confirmed_by_author"
    return "unknown"


def _find_cause(text: str) -> str:
    """Try to extract the suspected cause from text."""
    cause_patterns = (
        r"(?:cause|reason|problem|issue) (?:was|is|turned out to be) (.+?)(?:\n|\.|$)",
        r"the (?:root|underlying) cause (?:was|is) (.+?)(?:\n|\.|$)",
    )
    for pattern in cause_patterns:
        m = _re.search(pattern, text, _re.I)
        if m:
            return m.group(1).strip()
    return ""


# --- CommunityCase aggregation / ranking contract --------------------------------------
#
# Given multiple CommunityCase objects plus local evidence, produces a structured report
# that separates cases by their utility for the Navigator loop (CONFIRM, COMPARE, rejected),
# ranks them by confidence, and selects the best verification step.
# Deterministic, not LLM-decided.

@dataclass(frozen=True)
class CommunityExperienceReport:
    """Structured report from ranking a set of CommunityCases against local evidence.

    The report classifies cases into three buckets (confirm / compare / rejected),
    selects the best verification step from the highest-confidence confirm case,
    and carries provenance. It is NEVER a verified board fact.

    The report is consumed by the Navigator loop:
    - CONFIRM cases suggest a candidate cause + verification step
    - COMPARE cases show alternative causes / different context
    - rejected cases are noise and do not reach the user
    """

    # Buckets
    confirm_cases: tuple[CommunityCaseMatch, ...]    # usable_for_confirm=True
    compare_cases: tuple[CommunityCaseMatch, ...]    # usable_for_compare=True but not confirm
    rejected_cases: tuple[CommunityCaseMatch, ...]   # neither confirm nor compare

    # Best proof path from the community experience
    best_verification_step: str                       # from highest-confidence confirm case
    best_confidence: float                            # confidence of the best confirm case

    # Provenance
    top_reference_links: tuple[str, ...]              # provenance links, never the answer

    # Summary
    summary_reason: str                               # human-readable summary
    has_actionable_external_experience: bool          # True if at least one confirm case exists


def rank_community_cases(
    cases: list[CommunityCase],
    local_evidence: dict[str, str] | None = None,
    local_context: str | None = None,
    local_symptoms: tuple[str, ...] = (),
) -> CommunityExperienceReport:
    """Rank a set of CommunityCases against local evidence, producing a structured report.

    Deterministic. Local Navigator remains primary. Cases never become verified board facts.

    Ranking rules:
    1. Each case is compared via compare_case_to_evidence().
    2. Cases with usable_for_confirm=True go to confirm_cases, sorted by confidence.
    3. Cases with usable_for_compare=True (but not confirm) go to compare_cases, sorted by confidence.
    4. All others go to rejected_cases.
    5. best_verification_step comes from the highest-confidence confirm case.
    6. A speculated case must never outrank a proven/confirmed case.
    7. Different-board/different-MCU cases can be compare-only, not confirm.
    8. Reference links are provenance, never the answer.
    """
    if not cases:
        return CommunityExperienceReport(
            confirm_cases=(),
            compare_cases=(),
            rejected_cases=(),
            best_verification_step="",
            best_confidence=0.0,
            top_reference_links=(),
            summary_reason="No community cases provided.",
            has_actionable_external_experience=False,
        )

    # --- Compare each case against local evidence ---
    matches: list[CommunityCaseMatch] = []
    for case in cases:
        match = compare_case_to_evidence(
            case,
            local_evidence=local_evidence,
            local_context=local_context,
            local_symptoms=local_symptoms,
        )
        matches.append(match)

    # --- Separate into buckets, sorted by confidence (descending) ---
    # Evidence quality is used as a tiebreaker: proven > confirmed_by_author > speculated > unknown
    evidence_order = {"proven": 0, "confirmed_by_author": 1, "speculated": 2, "unknown": 3}

    def _sort_key(m: CommunityCaseMatch) -> tuple[float, int]:
        """Higher confidence first; proven > confirmed_by_author > speculated > unknown first."""
        eq = "unknown"
        for q in ("proven", "confirmed_by_author", "speculated", "unknown"):
            if q in m.reason:
                eq = q
                break
        return (-m.case_confidence, evidence_order.get(eq, 3))

    confirm = tuple(sorted(
        [m for m in matches if m.usable_for_confirm],
        key=_sort_key,
    ))
    compare = tuple(sorted(
        [m for m in matches if m.usable_for_compare and not m.usable_for_confirm],
        key=_sort_key,
    ))
    rejected = tuple(sorted(
        [m for m in matches if not m.usable_for_confirm and not m.usable_for_compare],
        key=_sort_key,
    ))

    # --- Best verification step from highest-confidence confirm case ---
    best_verification = ""
    best_confidence = 0.0
    if confirm:
        best = confirm[0]  # already sorted by confidence descending
        best_verification = best.suggested_verification_step
        best_confidence = best.case_confidence

    # --- Provenance ---
    all_links = [m.reference_link for m in confirm + compare if m.reference_link]
    top_links = tuple(dict.fromkeys(all_links))  # dedupe preserving order, first N

    # --- Summary reason ---
    n_confirm = len(confirm)
    n_compare = len(compare)
    n_rejected = len(rejected)
    if n_confirm:
        summary_reason = (
            f"{n_confirm} case(s) usable for CONFIRM (matching symptom, proven/confirmed evidence, "
            f"matching context). {n_compare} case(s) usable for COMPARE only. "
            f"{n_rejected} case(s) rejected as noise or no overlap."
        )
    elif n_compare:
        summary_reason = (
            f"No cases fully usable for CONFIRM. {n_compare} case(s) usable for COMPARE only — "
            f"symptom overlaps but evidence is weak or context differs. "
            f"{n_rejected} case(s) rejected."
        )
    else:
        summary_reason = (
            f"No usable community experience found. {n_rejected} case(s) rejected."
        )

    return CommunityExperienceReport(
        confirm_cases=confirm,
        compare_cases=compare,
        rejected_cases=rejected,
        best_verification_step=best_verification,
        best_confidence=best_confidence,
        top_reference_links=top_links,
        summary_reason=summary_reason,
        has_actionable_external_experience=bool(confirm),
    )


def mine_community_experience(
    snippets: list[dict[str, str]],
    local_evidence: dict[str, str] | None = None,
    local_context: str | None = None,
    local_symptoms: tuple[str, ...] = (),
) -> CommunityExperienceReport:
    """Deterministic end-to-end pipeline: text snippets → ranked community experience.

    This is the manual boundary for CommunityCase mining. No web fetching, no LLM, no RAG.
    Snippets are provided by the caller (already fetched or pasted text). Each snippet is a
    dict with keys: 'text', 'source', 'source_type', 'reference_link' (last three optional).

    Pipeline:
    1. Extract CommunityCase from each snippet via extract_community_case_from_text().
    2. Drop snippets that do not produce a case.
    3. Score each case (already done by extract_community_case_from_text).
    4. Compare each case against local evidence/context/symptoms.
    5. Rank cases into confirm / compare / rejected.
    6. Return CommunityExperienceReport.

    Rules:
    - Local Navigator remains primary; this starts only after local MATCH/SORT.
    - No case becomes verified board truth.
    - No links are dumped as answers.
    - Empty or weak snippets return a safe empty/low-actionability report.
    """
    cases: list[CommunityCase] = []
    for snippet in snippets:
        text = snippet.get("text", "")
        source = snippet.get("source", "")
        source_type = snippet.get("source_type", "unknown")
        link = snippet.get("reference_link", "")
        case = extract_community_case_from_text(
            text, source=source, source_type=source_type, reference_link=link)
        if case is not None:
            cases.append(case)
    return rank_community_cases(
        cases,
        local_evidence=local_evidence,
        local_context=local_context,
        local_symptoms=local_symptoms,
    )


def compare_case_to_evidence(
    case: CommunityCase,
    local_evidence: dict[str, str] | None = None,
    local_context: str | None = None,
    local_symptoms: tuple[str, ...] = (),
) -> CommunityCaseMatch:
    """Deterministically compare a CommunityCase against local evidence and context.

    This feeds the CONFIRM and COMPARE stages of the Navigator loop:

    - CONFIRM: if the case is actionable (proven/confirmed evidence, verification_step present,
      confidence above threshold) AND its symptom overlaps with local evidence, it is usable
      for CONFIRM — it can suggest a candidate cause + verification step.

    - COMPARE: if the case's symptom overlaps with local context even when not fully actionable,
      or if it differs in context, it is usable for COMPARE — the Navigator can note "community
      experience differs from your situation."

    Args:
        case: the mined CommunityCase
        local_evidence: normalised evidence from the user's ProblemPattern (e.g. {"tx_activity": "absent"})
        local_context: the user's board/MCU/peripheral/toolchain string
        local_symptoms: text strings the user has said (e.g. ["TX is silent", "pin not toggling"])

    Returns:
        CommunityCaseMatch with matched/differing/missing facts, actionability flags, and reason.
    """
    matched: list[str] = []
    differing: list[str] = []
    missing: list[str] = []

    # --- Symptom match ---
    case_tokens = _tokenise(case.symptom + " " + case.suspected_cause)
    evidence_tokens: set[str] = set()
    for v in (local_evidence or {}).values():
        evidence_tokens |= _tokenise(v)
    for s in (local_symptoms or ()):
        evidence_tokens |= _tokenise(s)

    symptom_overlap = case_tokens & evidence_tokens
    if symptom_overlap:
        matched.append("symptom: " + ", ".join(sorted(symptom_overlap)))
    else:
        missing.append("symptom: no overlap between case symptom and local evidence")

    # --- Context comparison ---
    if local_context and case.context:
        ctx_case = _tokenise(case.context)
        ctx_local = _tokenise(local_context)
        context_overlap = ctx_case & ctx_local
        context_diff = ctx_case - ctx_local
        if context_overlap:
            matched.append("context: " + ", ".join(sorted(context_overlap)))
        if context_diff:
            differing.append("context: " + ", ".join(sorted(context_diff)))

    # --- Verification step presence ---
    has_verification = bool(case.verification_step.strip())
    has_fix = bool(case.fix.strip())

    # --- Determine actionability ---
    case_is_actionable = is_actionable(case)
    symptom_present = bool(symptom_overlap)
    context_has_overlap = any("context:" in f for f in matched)
    context_differs = bool(differing)

    # CONFIRM requires: actionable case + symptom overlap + context is NOT fully divergent.
    # If the case's context (MCU/board) differs from the local context, the proof step may
    # not apply — demote to COMPARE only, never CONFIRM. This prevents an ATmega case from
    # being treated as confirmable proof for an STM32 user.
    usable_confirm = case_is_actionable and symptom_present and not context_differs
    usable_compare = symptom_present and (case.confidence >= 0.15)

    # --- Build the suggested verification step ---
    verification = case.verification_step if (usable_confirm and has_verification) else ""

    # --- Build reason ---
    if usable_confirm:
        reason = ("Case symptom matches local evidence and case is actionable "
                  f"(evidence_quality={case.evidence_quality}, confidence={case.confidence}). "
                  "Usable for CONFIRM: suggests a candidate cause + verification step.")
    elif usable_compare and not usable_confirm:
        reason = ("Case symptom overlaps but case is not fully actionable. "
                  f"evidence_quality={case.evidence_quality}, confidence={case.confidence}. "
                  "Usable for COMPARE only: shows alternative causes seen by others.")
    elif usable_compare and has_fix and not has_verification:
        reason = ("Case has a fix but no verification_step. The fix is anecdotal without proof. "
                  "Usable for COMPARE as a weak signal only, not for CONFIRM.")
    else:
        reason = ("Case does not overlap with local evidence enough to be useful. "
                  "Not used for CONFIRM or COMPARE.")

    if differing:
        reason += f" Note: {len(differing)} context difference(s) noted — case and local context diverge."

    return CommunityCaseMatch(
        matched_facts=tuple(matched),
        differing_facts=tuple(differing),
        missing_facts=tuple(missing),
        usable_for_confirm=usable_confirm,
        usable_for_compare=usable_compare,
        case_confidence=case.confidence,
        suggested_verification_step=verification,
        reference_link=case.reference_link,
        reason=reason,
    )
