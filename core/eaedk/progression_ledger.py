"""Progression Ledger (Option 1) — the structural state engine for learner growth.

Tracks concept mastery, mistake patterns, and independence signals to dynamically
adjust scaffolding: full explanations for beginners, compressed prompts for
intermediate learners, peer-review framing for experienced engineers.

Storage: local SQLite (``learner_model.db``), isolated from the main EAEDK
database.  The ledger is a pure side-channel — writing telemetry never
affects routing, and reading scaffolding never mutates the ledger.

Tables
------
``concept_mastery``
    Per-entity exposure count, last-seen timestamp, and the route that last
    touched it.  Mastery is *computed*, not stored — it decays with time.

``mistake_ledger``
    Per-fault-pattern mistake and resolution counts.  The resolution ratio
    tells us how well the proof-path engine is guiding the learner.

``independence_signals``
    Append-only log of every turn's route, intent, entities, and the
    scaffolding level that was applied — the raw data for independence
    trajectory analysis.

Usage::

    from eaedk.progression_ledger import (
        ProgressionLedger, log_turn_telemetry,
        get_scaffolding_parameters, apply_scaffolding_to_prompt,
    )

    ledger = ProgressionLedger()                         # creates learner_model.db
    log_turn_telemetry(ledger, "TEACH", "define", ("SPI", "I2C"))
    decision = get_scaffolding_parameters(ledger, ("SPI",))
    prompt = apply_scaffolding_to_prompt(system_prompt, decision)
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence


# ── Scaffolding levels ────────────────────────────────────────────────────────────────

class ScaffoldLevel(str, Enum):
    """The three tiers of instructional scaffolding."""
    FULL       = "FULL_SCAFFOLD"           # score < 0.4  — beginner
    COMPRESSED = "COMPRESSED_SCAFFOLD"     # 0.4 <= score < 0.8  — intermediate
    PEER       = "PEER_REVIEW"             # score >= 0.8  — experienced


# Thresholds
_FULL_UPPER     = 0.4
_COMPRESSED_UPPER = 0.8

# Temporal decay: half-life in days.  A concept last seen 3 weeks ago has
# half the mastery of the same exposure count seen today.
_DECAY_HALF_LIFE_DAYS = 21.0

# Logarithmic normalisation: how many exposures to reach ~1.0 base mastery.
_EXPOSURE_NORM = 20


# ── Control struct ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScaffoldingDecision:
    """The scaffolding configuration for a single teaching turn.

    ``level``               – which tier to apply.
    ``aggregated_mastery``  – 0.0–1.0, averaged across target entities.
    ``entity_scores``       – per-entity mastery (for logging / debugging).
    ``suppressed_sections`` – deterministic-render section headers to strip
                              (e.g. ``("MATCH", "SORT")``).
    ``system_note``         – one-line instruction to append to the LLM system
                              prompt so the model matches the scaffolding tier.
    """
    level: ScaffoldLevel
    aggregated_mastery: float
    entity_scores: dict[str, float]
    suppressed_sections: tuple[str, ...] = ()
    system_note: str = ""


# ── Schema ────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS concept_mastery (
    entity          TEXT    PRIMARY KEY,
    exposure_count  INTEGER NOT NULL DEFAULT 0,
    last_seen_ts    TEXT,
    last_route      TEXT
);

CREATE TABLE IF NOT EXISTS mistake_ledger (
    fault_id         TEXT    PRIMARY KEY,
    mistake_count    INTEGER NOT NULL DEFAULT 0,
    last_mistake_ts  TEXT,
    resolution_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS independence_signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    route      TEXT    NOT NULL,
    intent     TEXT    NOT NULL,
    entities   TEXT,
    scaffold   TEXT
);
"""


# ── Ledger class ──────────────────────────────────────────────────────────────────────

class ProgressionLedger:
    """Isolated SQLite storage for the learner progression model.

    Creates and manages ``learner_model.db`` (path overridable via
    ``EAEDK_PROGRESSION_DB`` env var).  Follows the same connection
    conventions as ``store.db`` (WAL, foreign keys, row_factory).
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path(
                os.environ.get("EAEDK_PROGRESSION_DB",
                               str(Path(__file__).parent / "learner_model.db")))
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    # -- connection management --------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def _ensure_schema(self) -> None:
        """Create tables idempotently."""
        self.conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- convenience queries (used by the read path) ----------------------------------

    def _concept_row(self, entity: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT exposure_count, last_seen_ts FROM concept_mastery WHERE entity = ?",
            (entity,)).fetchone()

    def _fault_row(self, fault_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT mistake_count, resolution_count FROM mistake_ledger WHERE fault_id = ?",
            (fault_id,)).fetchone()


# ── Timestamp helper ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Write Path: Telemetry Updates ─────────────────────────────────────────────────────

def log_turn_telemetry(
    ledger: ProgressionLedger,
    route: str,
    evaluated_intent: str,
    detected_entities: Sequence[str],
    active_fault_id: str | None = None,
) -> None:
    """Record a single turn's telemetry into the progression ledger.

    Parameters
    ----------
    ledger
        The progression ledger instance.
    route
        The route the classifier chose (``PROOF_PATH``, ``TEACH``,
        ``DECISION_MAP``, ``LEARNING_MAP``, ``CLARIFY``, ``DECLINE``).
    evaluated_intent
        The structural router's objective string (``fault``, ``define``,
        ``compare``, ``direction``, ``vague``).
    detected_entities
        Canonical entity names extracted by the structural router.
    active_fault_id
        When ``route == "PROOF_PATH"``, the matched ``ProblemPattern.name``
        (e.g. ``"uart_bringup"``).  Ignored for other routes.
    """
    now = _now_iso()
    conn = ledger.conn

    # ── Mistake ledger: increment on PROOF_PATH ────────────────────────────────
    if route == "PROOF_PATH" and active_fault_id:
        conn.execute(
            """INSERT INTO mistake_ledger
                   (fault_id, mistake_count, last_mistake_ts, resolution_count)
               VALUES (?, 1, ?, 0)
               ON CONFLICT(fault_id) DO UPDATE SET
                   mistake_count    = mistake_count + 1,
                   last_mistake_ts  = excluded.last_mistake_ts""",
            (active_fault_id, now))

    # ── Concept mastery: increment exposure on TEACH ────────────────────────────
    if route == "TEACH":
        for entity in detected_entities:
            conn.execute(
                """INSERT INTO concept_mastery
                       (entity, exposure_count, last_seen_ts, last_route)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(entity) DO UPDATE SET
                       exposure_count = exposure_count + 1,
                       last_seen_ts   = excluded.last_seen_ts,
                       last_route     = excluded.last_route""",
                (entity, now, route))

    # ── Independence signals: log every turn ────────────────────────────────────
    conn.execute(
        """INSERT INTO independence_signals
               (ts, route, intent, entities, scaffold)
           VALUES (?, ?, ?, ?, NULL)""",
        (now, route, evaluated_intent,
         json.dumps(list(detected_entities))))

    conn.commit()


def log_resolution(ledger: ProgressionLedger, fault_id: str) -> None:
    """Record that a fault-pattern proof path was successfully resolved.

    Call this when the learner provides the evidence the proof path asked
    for and the decision tree advances past the terminal node.
    """
    now = _now_iso()
    ledger.conn.execute(
        """INSERT INTO mistake_ledger
               (fault_id, mistake_count, resolution_count, last_mistake_ts)
           VALUES (?, 0, 1, ?)
           ON CONFLICT(fault_id) DO UPDATE SET
               resolution_count = resolution_count + 1,
               last_mistake_ts  = excluded.last_mistake_ts""",
        (fault_id, now))
    ledger.conn.commit()


# ── Read Path: Mastery Scoring with Temporal Decay ────────────────────────────────────

def _temporal_mastery(
    exposure_count: int,
    last_seen_ts: str | None,
    now: float,
) -> float:
    """Compute mastery for a single entity.

    Formula::

        mastery = min(1.0, ln(1 + exposure) / ln(1 + NORM))
                  × exp(-ln(2) × days_since / HALF_LIFE)

    * Base score: logarithmic in exposure count (diminishing returns; 20
      exposures ≈ 1.0, 1 exposure ≈ 0.23).
    * Decay: exponential half-life of 21 days.  A concept last seen 3 weeks
      ago has half the mastery of the same exposure seen today.
    * Zero exposures or no timestamp → 0.0.
    """
    if exposure_count <= 0 or not last_seen_ts:
        return 0.0

    try:
        last_seen = datetime.fromisoformat(last_seen_ts).timestamp()
    except (ValueError, TypeError):
        return 0.0

    days_since = max(0.0, (now - last_seen) / 86400.0)

    base  = min(1.0, math.log(1 + exposure_count) / math.log(1 + _EXPOSURE_NORM))
    decay = math.exp(-math.log(2) * days_since / _DECAY_HALF_LIFE_DAYS)

    return base * decay


# ── Scaffolding Level Selection ───────────────────────────────────────────────────────

_SUPPRESSED_BY_LEVEL: dict[ScaffoldLevel, tuple[str, ...]] = {
    ScaffoldLevel.FULL:       (),
    ScaffoldLevel.COMPRESSED: ("MATCH", "SORT"),
    ScaffoldLevel.PEER:       ("MATCH", "SORT", "CONFIRM", "ORGANIZE"),
}

_SYSTEM_NOTE: dict[ScaffoldLevel, str] = {
    ScaffoldLevel.FULL: "",
    ScaffoldLevel.COMPRESSED: (
        "SCAFFOLDING: the learner has intermediate knowledge of the topic. "
        "Skip basic definitions and introductory framing — go straight to "
        "trade-offs, edge cases, and the next concrete action."),
    ScaffoldLevel.PEER: (
        "SCAFFOLDING: the learner is an experienced engineer. No introductory "
        "material, no definitions, no learning-order scaffolding. Treat as a "
        "peer: straight to the engineering substance, advanced trade-offs, "
        "and real-world failure modes."),
}


def _scaffold_for_score(mastery: float) -> ScaffoldLevel:
    if mastery < _FULL_UPPER:
        return ScaffoldLevel.FULL
    if mastery < _COMPRESSED_UPPER:
        return ScaffoldLevel.COMPRESSED
    return ScaffoldLevel.PEER


# ── Read Path: Scaffolding Parameterisation ──────────────────────────────────────────

def get_scaffolding_parameters(
    ledger: ProgressionLedger,
    target_entities: Sequence[str],
) -> ScaffoldingDecision:
    """Look up target entities in ``concept_mastery`` and compute an
    aggregated mastery score with temporal decay.

    **Pessimistic minimum**: the aggregated mastery is the MINIMUM across
    all target entities, not the average.  This prevents high mastery in a
    known protocol from averaging out a novice protocol — if ANY entity is
    at ``FULL_SCAFFOLD``, the entire decision defaults to ``FULL_SCAFFOLD``.

    Returns a :class:`ScaffoldingDecision` containing the level, per-entity
    scores, which deterministic-render sections to suppress, and the
    one-line system-note to append to the LLM prompt.

    If *target_entities* is empty or no entities have any exposure history,
    the result is ``FULL_SCAFFOLD`` (score 0.0) — maximum hand-holding.
    """
    now = time.time()
    entity_scores: dict[str, float] = {}
    scores: list[float] = []

    for entity in target_entities:
        row = ledger._concept_row(entity)
        if row and row["exposure_count"] > 0:
            score = _temporal_mastery(
                row["exposure_count"], row["last_seen_ts"], now)
        else:
            score = 0.0
        entity_scores[entity] = round(score, 4)
        scores.append(score)

    # Pessimistic minimum: worst-case entity drives the scaffolding level
    aggregated = round(min(scores) if scores else 0.0, 4)
    level = _scaffold_for_score(aggregated)

    return ScaffoldingDecision(
        level=level,
        aggregated_mastery=aggregated,
        entity_scores=entity_scores,
        suppressed_sections=_SUPPRESSED_BY_LEVEL[level],
        system_note=_SYSTEM_NOTE[level],
    )


# ── Pass-2 Integration: Prompt Modifier ──────────────────────────────────────────────

def apply_scaffolding_to_prompt(
    system_prompt: str,
    decision: ScaffoldingDecision,
) -> str:
    """Apply a scaffolding decision to a prompt string.

    Two mechanical transformations:

    1. **Section stripping** — for deterministic renders that contain section
       headers (``MATCH —``, ``SORT —``, ``CONFIRM —``, ``ORGANIZE —``),
       blocks under suppressed headers are removed.  ``COMPARE`` and ``HELP``
       are always preserved (they carry the actionable substance).

    2. **System-note injection** — for LLM system prompts, a one-line
       scaffolding instruction is prepended so the model matches the tier.

    The function is safe to call on any string; if no sections match, only
    the note injection (if any) applies.
    """
    if decision.level == ScaffoldLevel.FULL:
        return system_prompt  # no modifications

    lines = system_prompt.split("\n")
    result: list[str] = []
    skipping = False

    # Build the set of section prefixes we want to strip
    strip_headers = {f"{s} —" for s in decision.suppressed_sections}

    # All known section headers (to detect when a suppressed block ends)
    all_headers = {
        "MATCH —", "SORT —", "CONFIRM —", "ORGANIZE —",
        "COMPARE —", "HELP —", "What ", "Your ", "A ",
    }

    for line in lines:
        stripped = line.strip()

        # Does this line start a section we want to strip?
        if any(stripped.startswith(h) for h in strip_headers):
            skipping = True
            continue

        # Does this line start ANY known section header? → stop skipping
        if skipping and stripped and any(stripped.startswith(h) for h in all_headers):
            skipping = False

        if not skipping:
            result.append(line)

    # Strip trailing blank lines left by removed blocks
    cleaned = "\n".join(result).rstrip("\n")

    # Inject the system note at the top
    if decision.system_note:
        cleaned = f"[{decision.system_note}]\n\n{cleaned}"

    return cleaned


# ── Independence Trajectory ──────────────────────────────────────────────────────────

def independence_score(
    ledger: ProgressionLedger,
    window_turns: int = 50,
) -> float:
    """Compute a 0.0–1.0 score measuring how independently the learner operates.

    Signals of independence (over the last *window_turns* turns):
      * More ``TEACH`` / ``DECISION_MAP`` / ``LEARNING_MAP`` routes
        (self-directed exploration).
      * Fewer ``PROOF_PATH`` routes (fewer faults requiring guided debugging).
      * Fewer ``CLARIFY`` / ``DECLINE`` routes (clearer, more grounded intent).

    ``score = self_directed / (total - clarifies)``, bounded to [0, 1].
    """
    rows = ledger.conn.execute(
        """SELECT route FROM independence_signals
           ORDER BY id DESC LIMIT ?""",
        (window_turns,)).fetchall()

    if not rows:
        return 0.0

    total = len(rows)
    self_directed = sum(
        1 for r in rows
        if r["route"] in ("TEACH", "DECISION_MAP", "LEARNING_MAP"))
    clarifies = sum(
        1 for r in rows
        if r["route"] in ("CLARIFY", "DECLINE"))

    productive = total - clarifies
    if productive <= 0:
        return 0.0

    return round(min(1.0, self_directed / productive), 4)


def fault_resolution_ratio(
    ledger: ProgressionLedger,
    fault_id: str,
) -> float:
    """Return the resolution ratio for a specific fault: resolutions / (mistakes + resolutions).

    Returns 0.0 when no data exists.  A ratio approaching 1.0 means the
    learner is resolving this fault pattern reliably.
    """
    row = ledger._fault_row(fault_id)
    if row is None:
        return 0.0
    m = row["mistake_count"]
    r = row["resolution_count"]
    total = m + r
    return round(r / total, 4) if total > 0 else 0.0