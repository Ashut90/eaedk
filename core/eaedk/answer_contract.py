"""Answer-shape routing + deterministic Answer Contracts (docs/35 pivot — step #1+#2).

The mentor DETECTS what shape of answer a question demands, builds a CONTRACT for that shape, lets the
LLM Actor generate against it, then VERIFIES the answer against the contract DETERMINISTICALLY. On a
contract miss the Actor REGENERATES (capped) with the failure reasons injected — a critic never
*rewrites* the answer, because a weak local model rewriting a good answer is exactly the corruption
this design removes (a correct folder tree got rewritten into prose; see docs/35).

This is general, NOT a stored-template system: the validators check the SHAPE of whatever the model
generated (does it contain a real directory tree? numbered steps? a closing question?) — never a
canned answer. Topic refinements (bootloader / Yocto / Linux-driver) add extra SHAPE requirements
(must separate boot vs app; must show a layer + recipe) that the generated answer has to satisfy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import arbiter   # reuse the fault + open-decision detectors (no import cycle: arbiter never
                        # imports this module)

# ── Shapes ──────────────────────────────────────────────────────────────────────────────────────
CONCRETE_STRUCTURE = "concrete_structure"   # "folder structure for X" → a real tree/layout
OPEN_DECISION = "open_decision"             # "X vs Y / should I A or B" → Socratic trade-off
CONCEPT = "concept"                         # "what is / explain X" → short explanation + example
DEBUG_PROOF_PATH = "debug_proof_path"       # a fault report → deterministic proof-path (navigator)
TEST_PLAN = "test_plan"                     # "how do I test / test strategy" → numbered checkable plan
LEARNING_PATH = "learning_path"             # "where do I start / roadmap" → sequenced roadmap
FACT_BOUND = "fact_bound"                   # "how much flash / does this board have CAN" → grounded fact
DEFAULT = ""                                # uncovered → open teaching answer (keeps existing critics)

MAX_REGEN = 2                               # Actor regeneration attempts on a contract miss (cap)


# ── Shape detection ─────────────────────────────────────────────────────────────────────────────
_STRUCTURE_SIGNALS = (
    "folder structure", "directory structure", "project structure", "file structure",
    "repo structure", "repository structure", "folder layout", "project layout", "directory layout",
    "code structure", "module layout", "source layout", "how should i organize", "how do i organize",
    "how to organize", "how to structure", "how should i structure", "how do i structure",
    "where do i put", "where should i put", "project organization", "organize my project",
    "organize the project", "lay out my", "layout for", "structure for", "structure of my project",
    "scaffold", "boilerplate", "skeleton for", "project skeleton",
    # reversed word-order variants ("what is the structure of folder/project/…")
    "structure of folder", "structure of the folder", "structure of a folder",
    "structure of project", "structure of the project", "structure of a project",
    "structure of directory", "structure of the directory",
    "what is the folder", "what is the directory", "what does the folder")

_TEST_SIGNALS = (
    "test plan", "test strategy", "testing strategy", "test-plan", "how do i test", "how to test",
    "how should i test", "unit test", "integration test", "test coverage", "test harness",
    "validation plan", "verification plan", "how do i validate", "test framework", "regression test")

_CONCEPT_SIGNALS = ("what is ", "what's ", "what are ", "explain ", "what does ", "how does ",
                    "difference between", "meaning of", "define ")

_LEARNING_SIGNALS = ("where do i start", "where to start", "how do i get started", "getting started",
                     "roadmap", "learning path", "curriculum", "how do i learn", "how to learn",
                     "study plan", "syllabus", "from scratch", "where should i begin")

_FACT_SIGNALS = ("how much flash", "how much ram", "how many", "does this board", "does the board",
                 "what clock", "what is the clock", "which pins", "how big is", "what frequency")


def detect_answer_shape(text: str) -> str:
    """Classify the SHAPE of answer the question demands. Precedence is deliberate: a fault report
    wins over everything (debug physically), a structure/test request wins over the generic 'what is'
    in the same sentence, and an open decision wins over a plain concept lookup."""
    low = " " + (text or "").lower().strip() + " "
    if arbiter._is_fault(text):
        return DEBUG_PROOF_PATH
    if any(s in low for s in _STRUCTURE_SIGNALS):
        return CONCRETE_STRUCTURE
    if any(s in low for s in _TEST_SIGNALS):
        return TEST_PLAN
    if arbiter._is_open_decision(text):
        return OPEN_DECISION
    if any(s in low for s in _LEARNING_SIGNALS):
        return LEARNING_PATH
    if any(s in low for s in _FACT_SIGNALS):
        return FACT_BOUND
    if any(s in low for s in _CONCEPT_SIGNALS):
        return CONCEPT
    return DEFAULT


# ── Topic refinements (extra shape requirements, not templates) ──────────────────────────────────
def _is_bootloader(low: str) -> bool:
    # tolerate the common typo 'bootlaoder' and the spaced 'boot loader'
    return bool(re.search(r"boot ?l(oa|ao)der|bootload", low))


def _is_yocto(low: str) -> bool:
    return any(t in low for t in ("yocto", "bitbake", "openembedded", "poky", "meta-layer",
                                  "bsp layer", "bitbake recipe"))


def _is_linux_driver(low: str) -> bool:
    return any(t in low for t in ("linux driver", "kernel driver", "kernel module", "device driver",
                                  "char driver", "platform driver", "loadable module"))


# ── Deterministic validators (return a failure REASON, or None when satisfied) ───────────────────
Validator = Callable[[str], "str | None"]

_FENCE = re.compile(r"```[\w+-]*\n(.*?)```", re.S)


def _fenced(answer: str) -> str:
    blocks = _FENCE.findall(answer or "")
    return "\n".join(blocks)


def _require_tree(answer: str) -> str | None:
    """A real directory tree / module layout, not prose: a fenced block containing path-ish lines."""
    body = _fenced(answer)
    if not body:
        return "no code block — put the directory structure inside a ``` fenced block"
    has_paths = bool(re.search(r"[\w.\-]+/", body)) or "├" in body or "└" in body or "|--" in body
    if not has_paths:
        return "the block has no directory layout — list real folders (e.g. src/, include/) or a tree"
    return None


def _require_boot_app_separation(answer: str) -> str | None:
    low = answer.lower()
    missing = []
    if not (_is_bootloader(low) or "boot/" in low or "/boot" in low):
        missing.append("a bootloader area")
    if not re.search(r"\bapp\w*/|/app|application", low):
        missing.append("a separate application (app/) area")
    if not any(t in low for t in ("platform", "port", "shared", "common", "hal", "bsp")):
        missing.append("a shared/platform abstraction (platform/ports, shared, or bsp)")
    if missing:
        return "a bootloader project must separate " + ", ".join(missing)
    return None


def _require_yocto_layout(answer: str) -> str | None:
    low = answer.lower()
    missing = []
    if not any(t in low for t in ("meta-", "layer", "bblayers")):
        missing.append("a Yocto layer (meta-<name>/)")
    if not any(t in low for t in (".bb", "recipe", "recipes-")):
        missing.append("at least one recipe (.bb under recipes-*)")
    if not any(t in low for t in ("src", "app", "application", "source")):
        missing.append("the application source structure")
    if missing:
        return "a Yocto project must show " + " and ".join(missing)
    return None


def _require_driver_layout(answer: str) -> str | None:
    low = answer.lower()
    missing = []
    if not ("makefile" in low or "kbuild" in low):
        missing.append("a Makefile/Kbuild (kernel modules build via kbuild)")
    if not (".c" in low or "src/" in low or "driver" in low):
        missing.append("the driver source (.c)")
    if missing:
        return "a Linux driver project must include " + " and ".join(missing)
    return None


def _require_numbered_steps(answer: str) -> str | None:
    if len(re.findall(r"(?m)^\s*\d+[.)]\s+\S", answer or "")) < 2:
        return "give a numbered, checkable plan (1. 2. 3. …), each step with a pass/fail check"
    return None


def _require_question(answer: str) -> str | None:
    a = (answer or "").rstrip().rstrip("*_# ")
    if a.endswith("?") or "question:" in (answer or "").lower():
        return None
    return "an open design question must end by asking the user a deciding question"


# ── Contract ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class AnswerContract:
    """What a given answer shape MUST and MUST NOT look like, plus its prompt steer and the contextual
    Try-this / follow-up. `soft_critics` is False for the structured/deliverable shapes — those are
    governed deterministically and must NOT be run through an LLM rewrite."""
    shape: str
    validators: list[Validator] = field(default_factory=list)
    forbid: list[tuple[str, str]] = field(default_factory=list)   # (regex on lowercased answer, reason)
    soft_critics: bool = True
    prompt_addendum: str = ""
    try_this: str | None = None            # None → keep the domain-selected experiment
    followup: str | None = None            # None → keep the default follow-up question

    def validate(self, answer: str) -> list[str]:
        """Deterministic check. Returns the list of unmet requirements (empty == the answer conforms)."""
        fails: list[str] = []
        for v in self.validators:
            reason = v(answer or "")
            if reason:
                fails.append(reason)
        low = (answer or "").lower()
        for pat, reason in self.forbid:
            if re.search(pat, low):
                fails.append(reason)
        return fails


_STRUCTURE_ADDENDUM = (
    "ANSWER SHAPE REQUIRED: output the actual directory TREE inside a ``` fenced block (folders end "
    "in /). Match the domain: Yocto/embedded-Linux → use Yocto-native layer structure (bblayers.conf, "
    "local.conf, meta-<name>/, recipes-*/); bare-metal/MCU firmware → put chip-specific files behind "
    "platform/ports/<chip>/ with a shared/ contract. After the tree, explain EACH top-level folder: "
    "what it contains and why it exists. Be thorough — do not cut the explanation short. "
    "Do NOT write source code, and do NOT suggest a flash/UART/GPIO experiment — the deliverable is "
    "the structure itself.")
_STRUCTURE_TRY_THIS = (
    "create the folders from the tree above and drop a one-line README in each, then move one real file "
    "into its place — if it still builds from the new layout, the structure is right")
_STRUCTURE_FOLLOWUP = (
    "Question: want me to expand one part of the tree — say the platform/ports layer or the build "
    "files — into actual files?")

_TEST_ADDENDUM = (
    "ANSWER SHAPE REQUIRED: give a concrete TEST PLAN as numbered, checkable steps (1. 2. 3. …) — what "
    "to test, at which layer (unit on host / on-target / HIL), and the pass/fail check for each step. "
    "No long theory.")
_TEST_TRY_THIS = (
    "write the FIRST test from the plan and run it red before you implement — confirm it fails for the "
    "reason you expect")
_TEST_FOLLOWUP = "Question: which layer do you want to write the first test for?"

_OPEN_FORBID = (r"^\s*(use |go with |i'?d recommend|i recommend|just use|choose |pick )",
                "do not OPEN an open design question with a blunt recommendation — teach the trade-off "
                "and end by asking the user a deciding question")
_STRUCTURE_FORBID = (r"^\s*(here'?s how|let'?s break|let me explain|sure[,!]|great question)",
                     "start with the structure itself, not a preamble")


def build_contract(shape: str, text: str) -> AnswerContract:
    """Build the contract for a detected shape, layering topic-specific shape requirements on top."""
    low = (text or "").lower()

    if shape == CONCRETE_STRUCTURE:
        validators: list[Validator] = [_require_tree]
        if _is_bootloader(low):
            validators.append(_require_boot_app_separation)
        if _is_yocto(low):
            validators.append(_require_yocto_layout)
        if _is_linux_driver(low):
            validators.append(_require_driver_layout)
        return AnswerContract(shape, validators=validators, forbid=[_STRUCTURE_FORBID],
                              soft_critics=False, prompt_addendum=_STRUCTURE_ADDENDUM,
                              try_this=_STRUCTURE_TRY_THIS, followup=_STRUCTURE_FOLLOWUP)

    if shape == TEST_PLAN:
        return AnswerContract(shape, validators=[_require_numbered_steps], soft_critics=False,
                              prompt_addendum=_TEST_ADDENDUM, try_this=_TEST_TRY_THIS,
                              followup=_TEST_FOLLOWUP)

    if shape == OPEN_DECISION:
        # Keep the existing soft critics (answer_check already leaves open decisions Socratic), and
        # additionally enforce the Socratic shape deterministically: no blunt opener, ends with a question.
        return AnswerContract(shape, validators=[_require_question], forbid=[_OPEN_FORBID],
                              soft_critics=True)

    # CONCEPT / FACT_BOUND / LEARNING_PATH / DEBUG_PROOF_PATH / DEFAULT — permissive contract; the
    # existing LLM critic chain (and, for debug, the navigator's proof-path upstream) still applies.
    return AnswerContract(shape, soft_critics=True)


def regen_instruction(fails: list[str]) -> str:
    """The block appended to the prompt to make the Actor (not a critic) fix a contract miss."""
    return ("\n\nYOUR PREVIOUS ANSWER DID NOT MEET THE REQUIRED ANSWER SHAPE. Fix ALL of these and "
            "answer again — output ONLY the corrected answer:\n"
            + "\n".join(f"- {f}" for f in fails) + "\n\nReanswer now:")
