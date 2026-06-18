"""Conceptual guards — deterministic checks for conceptually WRONG advice.

The literal verifiers (``problem_patterns.flag_invented_claims`` /
``verify_voiced``) catch invented *values* — a pin, clock, or register the
source never stated.  They do **not** catch statements that are literal-clean
but conceptually false, e.g. *"add pull-up resistors to your SPI bus"* (no
fabricated number, yet wrong).  That gap is the one crack in EAEDK's
proof/assurance promise: a confident conceptual error is worse than a decline,
because the learner has no idea they were misled.

These guards close it the same way the rest of EAEDK works: **a deterministic
rule is the arbiter; the LLM only explains.**  Design rules:

* **Precision over recall.**  We would rather miss a wrong statement than flag a
  correct one.  A false positive blocks a good answer and erodes trust, which is
  the exact thing we are protecting.  Each guard fires only on a clear,
  affirmative *assertion* of the wrong concept, evaluated one sentence at a time,
  with negation / contrast suppression.
* **Curated, not general.**  This is a hand-written registry of the specific
  traps that actually burn embedded beginners.  It grows by adding ``GUARDS``
  entries, never by trying to "understand" concepts.
* **Isolated.**  Pure text in, list of violation messages out.  No DB, no LLM,
  no conversation state.  The caller decides what to do with a violation
  (EAEDK falls back to its grounded deterministic answer).

Usage::

    from eaedk.conceptual_guards import flag_conceptual_errors

    bad  = flag_conceptual_errors("Add 4.7k pull-up resistors to your SPI bus.")
    # -> ["SPI is push-pull with chip-select; it does not use I2C-style ..."]
    good = flag_conceptual_errors("Unlike I2C, SPI does not need pull-ups.")
    # -> []
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

__all__ = ["flag_conceptual_errors", "Guard", "GUARDS"]


# ── Sentence splitting ────────────────────────────────────────────────────────────────
# Guards evaluate ONE sentence at a time so that cross-sentence co-occurrence
# ("SPI is fast. I2C needs pull-ups.") can never trigger a false positive.

# Split on sentence punctuation, but NOT on a '.' between digits (e.g. "4.7k",
# "10.0 MHz") — a decimal point must never fragment a sentence.
_SENT_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|[!?;\n]+")


def _sentences(text: str) -> list[str]:
    return [s.strip().lower() for s in _SENT_SPLIT.split(text or "") if s.strip()]


# Negation / contrast markers: their presence in a sentence means the writer is
# *denying* or *contrasting*, so an affirmative-assertion guard must stand down.
_NEGATORS = (
    " not ", "n't", " no ", " never ", " without ", " unlike ",
    "rather than", "instead of", " unnecessary", " optional",
    " don't", " dont ", " doesn't", " doesnt ", " isn't", " aren't",
)


def _has(sent: str, needles) -> bool:
    return any(n in sent for n in needles)


def _negated(sent: str) -> bool:
    return _has(" " + sent + " ", _NEGATORS)


# Affirmative recommend / assertion verbs shared by several guards.
_VERBS = (
    "add", "need", "require", "use", "put", "place", "connect", "recommend",
    "should", "must", "want", "include", "fit", "attach", "has ", "have ",
    "wire", "install",
)


# ── Guard struct ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Guard:
    """One curated conceptual check.

    ``check`` receives a single lowercased sentence and returns the violation
    message if that sentence affirmatively asserts the wrong concept, else None.
    """
    name: str
    check: Callable[[str], str | None]


# ── The curated guards ────────────────────────────────────────────────────────────────

_PULLUP = ("pull-up", "pull up", "pullup", "pull-ups", "pullups", "with pull")
_I2C    = ("i2c", "i²c", "iic")


def _g_spi_pullups(sent: str) -> str | None:
    """SPI is push-pull with chip-select — it does NOT use I2C-style pull-ups.

    Fires on an affirmative recommendation of pull-ups *for SPI*.  Suppressed by
    negation/contrast, by mentioning I2C in the same sentence (a comparison), or
    by 'open-drain' (an advanced, correct nuance).
    """
    if "spi" not in sent or not _has(sent, _PULLUP):
        return None
    if _negated(sent) or _has(sent, _I2C) or "open-drain" in sent or "open drain" in sent:
        return None
    if not (_has(sent, _VERBS) or "with pull" in sent):
        return None
    return ("SPI is push-pull with chip-select; it does not use I2C-style pull-up "
            "resistors on its data/clock lines. Pull-ups belong to open-drain buses "
            "like I2C.")


_I2C_DENY = (
    "don't need", "dont need", "do not need", "doesn't need", "doesnt need",
    "not need", "no need", "without", "not require", "don't require",
    "no pull-up", "no pullup", "unnecessary", "optional", "aren't needed",
    "not needed", "skip the pull", "skip pull",
)


def _g_i2c_pullups_denied(sent: str) -> str | None:
    """I2C is open-drain — it REQUIRES pull-ups on SDA/SCL.  Flag any denial.

    This guard intentionally fires *on* a negation, because the denial is the
    error, so it does not use the shared negation suppression.
    """
    if not _has(sent, _I2C) or not _has(sent, _PULLUP):
        return None
    # A sentence that also names SPI (or contrasts with "unlike") is a comparison —
    # the denial almost certainly applies to the other bus, so stand down.
    if "spi" in sent or "unlike" in sent:
        return None
    if not _has(sent, _I2C_DENY):
        return None
    return ("I2C is open-drain — it requires pull-up resistors on SDA/SCL to pull the "
            "lines high; they are not optional. Without them the bus stays low and no "
            "device can communicate.")


_CLOCK_LINE = (
    "clock line", "clock wire", "clock signal", "shared clock", "sclk",
    "clock pin", "common clock",
)


def _g_uart_clock_line(sent: str) -> str | None:
    """UART is asynchronous — there is NO shared clock line.  Flag asserting one.

    Suppressed by negation and by 'baud' / 'internal'/'peripheral' clock wording
    (the internal baud clock is real and correct; the *shared external clock
    line* is the misconception).
    """
    if "uart" not in sent or not _has(sent, _CLOCK_LINE):
        return None
    if _negated(sent) or "baud" in sent or "internal" in sent or "peripheral" in sent:
        return None
    if not _has(sent, _VERBS):
        return None
    return ("UART is asynchronous — there is no shared clock line between the two ends. "
            "Both sides simply agree on a baud rate; a shared clock wire is SPI/I2C "
            "territory, not UART.")


GUARDS: tuple[Guard, ...] = (
    Guard("spi_pullups",          _g_spi_pullups),
    Guard("i2c_pullups_denied",   _g_i2c_pullups_denied),
    Guard("uart_clock_line",      _g_uart_clock_line),
)


# ── Public API ────────────────────────────────────────────────────────────────────────

def flag_conceptual_errors(text: str) -> list[str]:
    """Return a list of conceptual-violation messages found in ``text``.

    Empty list means the text is clean (or out of scope of the curated guards).
    Each guard runs against each sentence independently; the same guard never
    reports twice.  Pure and deterministic — same text, same result.
    """
    violations: list[str] = []
    seen: set[str] = set()
    for sent in _sentences(text):
        for guard in GUARDS:
            if guard.name in seen:
                continue
            msg = guard.check(sent)
            if msg:
                violations.append(msg)
                seen.add(guard.name)
    return violations
