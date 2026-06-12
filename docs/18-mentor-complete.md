# 18 — Engineering State Engine + finishing touches (v2.2.0)

## Context (important correction)

The originating prompt assumed EAEDK was at v2.0.0 / 196 tests and asked for six pieces under tag
`v2.1.0-mentor-complete`. The repository is already at **v2.1.0-mentor-complete / 221 tests** —
Pieces 3 (driver path), and the bulk of 4 (Wokwi), 5 (Code Studio), and 6 (chat) shipped in that
release. This build adds **only the genuinely-missing work**, additively, and ships as **v2.2.0**
(the v2.1.0 tag is already published and is left untouched). Baseline confirmed before starting:
`pytest` 221, `eval 14/14`. No existing CLI verb, schema, seed, rule, template, or test behaviour
changes; the deterministic core is untouched.

## Net-new work in this release

### Piece 1 — Engineering State Engine (the new centrepiece)
Project progress derived from **evidence**, never stored as a number, never set by the LLM.
- Forward-only migration `0011_state_engine.sql`: new `project_progress(id, project_id,
  template_item_id, status, evidence, verified_by, timestamp)`, `UNIQUE(project_id,
  template_item_id)`. Additive; not a seed table (project data, never cleared by `db seed`).
- `engines/state.py::project_status(conn, project)` derives each checklist item's status from
  evidence, deterministically, in priority order:
  1. **VALIDATION_ENGINE** — the item's owning validation rule(s) all PASS → COMPLETE.
  2. **LOG_TRIAGE** — the item's owning rule appears in a *resolved* tracked risk → COMPLETE.
  3. **USER** — the engineer confirmed it (`eaedk checklist done`) → COMPLETE.
  Otherwise IN_PROGRESS (a rule is engaged but not yet PASS) or NOT_STARTED. It records the
  evidence into `project_progress` (read accessors only; no raw SQL in the engine) and returns
  the items, a derived `complete/total (%)`, a plain-English "why it matters" per incomplete item
  (the rule's `RULE_TEACH`, or a curated supplement for rule-less items), and the next
  recommended task. The LLM is never on this path — it only *explains* the result.
- CLI: `eaedk project status <name>` (additive verb) renders the screen from the spec.
- CLI: `eaedk checklist done <project> <item>` (additive) — the USER completion path.
- Web: `/api/progress/{project}` + a progress bar / checklist widget on the Validate and Code
  Studio pages (GREEN complete / YELLOW in-progress / GREY not-started, each incomplete item with
  its why-it-matters).
- Mentor: "how am I doing" / "what's next" reads the State Engine and explains the next
  incomplete item.
- Golden tests (pytest): COMPLETE on validation PASS; NOT_STARTED when the rule is UNKNOWN; the
  LLM cannot change status (no write path exists). The `eval 14/14` set is unchanged.

### Piece 2 enhancement — `--think` CLI verb + board-specific answers
`eaedk mentor --board <name> --project <name> --think` surfaces the existing
`think_before_code` checklist on the terminal. A new seed `board_blink_facts.yaml` (LED pin +
clock hint for the common boards) lets the checklist show the concrete answer (e.g. *PC13 →
GPIOC → APB2*) **from SQLite**, with a generic prompt where a board isn't seeded — still not
hardcoded in logic.

### Piece 4 enhancement — Wokwi-first dual-path START_HERE
`START_HERE.md` now shows **PATH A — Simulate (no hardware, start here)** before **PATH B —
Physical board (when you have real hardware)** for Wokwi-supported boards (Blue Pill, Pico,
ESP32-DevKitC, Arduino Uno/Mega); unsupported boards keep the single physical path. The Web UI
Export tab gains a "Don't have a physical board yet? → use Wokwi" banner. The Log Analyzer already
accepts Wokwi serial output unchanged (same text as real serial) — verified and noted.

### Piece 5 enhancement — Mark-as-complete in Code Studio
After a review with **no CONFIRMED issues**, Code Studio offers the project's not-yet-complete
checklist items as "Mark complete" buttons, which feed the State Engine via the **USER** path.

### Piece 6 enhancement — progress- and hardware-aware chat
`mentor_chat` now also injects the project's progress (next incomplete item from the State Engine)
and whether the user is on Wokwi or real hardware, and ties the "Try this" to Wokwi when there's
no hardware. "How am I doing / what's next" is answered from the State Engine.

## Governing rules (applied)
Beginner-clear + mid-level-useful on every label/answer; the Actor-Critic explains hardware
consequences, never lints; every feature works for a Wokwi-only user; the LLM never sets progress
or asserts an uncited hardware value.

## Testing
`core/tests/test_state_engine.py` + additions to `test_mentor_complete.py` / `test_web.py`.
Existing 221 stay green; ships as v2.2.0.
