# 16 — Web UI (v2.0.0-web-ui)

A second interface beside the CLI. **The CLI and every engine stay exactly as they are.** The
Web UI is a thin skin: its routes call the same Python functions the CLI calls — no business
logic, no duplicated rules, no new schema/engine. If the web layer were deleted, nothing else
would change.

**Preservation contract:** baseline is `pytest` 181 passing, `eval 14/14` — confirmed before and
after every page. The web code lives entirely under `core/eaedk/web/` plus one additive CLI verb;
no existing CLI verb, schema, seed, validation rule, template, or test behaviour changes.

## Architecture

- **Backend:** FastAPI in `core/eaedk/web/server.py`. Each route is a thin wrapper that opens a
  DB connection (`store.db.connect(paths.default_db_path())`, honouring `EAEDK_DB`), calls one
  engine function, and serialises the result to JSON. The exact functions:
  - Boards: `repo.list_boards`, `repo.load_board`, `mentor.capability_map`,
    `mentor.learning_path_for`, `mentor.dropped_steps_for`.
  - Setup: `repo.create_project`, `orchestrator.assess_project`; goals from
    `project_init.GOAL_LABELS`.
  - Validate: `orchestrator.assess_project` → `AssessResponse.to_dict()`.
  - Export: `engines.output.export_project`, then read the written files; zip on download.
  - Logs: `engines.logs.analyze_log` → `LogAnalysisResult.to_dict()`.
  - Mentor: the same structured functions + `mentor_llm.mentor_ask`.
- **Frontend:** plain HTML + CSS + vanilla JS under `core/eaedk/web/static/`. No React, no npm,
  no build step, no CDN, no external fonts (system font stacks only) — works fully offline. One
  HTML file per page; a shared `style.css` and `app.js` served from localhost.
- **Launch:** `eaedk web` (new additive verb) → `http://localhost:8080`. Lazy-imports FastAPI +
  uvicorn with a plain-English message if the optional `[web]` extra isn't installed.

### The one repo addition (justified)
The project dropdowns need the list of projects, and the rule **"no raw SQL outside repo
helpers"** forbids querying it from the web layer. There is no existing `list_projects`, so a
single **read-only accessor** `repo.list_projects(conn)` is added — mirroring `repo.list_boards`,
strictly additive, zero change to any existing function or behaviour. This is the architecturally
correct home for the query (repo.py is *the* SQL layer) and keeps the web routes SQL-free. No
engine logic is added.

## Pages (built one at a time; pytest + eval + a live route check after each)

1. **Board Explorer** — all boards (name, SoC, arch, flash, RAM, confidence) with a traffic-light
   (GREEN HIGH / YELLOW MEDIUM / RED LOW-or-no-geometry); click → capability cards + learning path.
2. **Project Setup** — form (name, board dropdown, goal dropdown with plain-English labels) →
   create + assess → feasibility light (GREEN feasible / YELLOW incomplete / RED blocked) + risks.
3. **Validate** — project dropdown → assessment table (rule → light), teach shown inline under
   every UNKNOWN/FAIL, gating (real) split from advisory, raw keys translated to plain English.
4. **Export** — feasible project → file tree, per-file View in a monospace panel, START_HERE
   rendered, full-bundle zip download.
5. **Log Analyzer** — paste or drop a log + optional project → matched signatures with severity
   badges, plain-English cause/fix, written-back risks as a notification.
6. **Mentor** — board selector → capability cards + numbered learning path with expandable
   "before you write code"; an ask box. A **command-preview footer** on every page shows the exact
   `eaedk …` command behind what you just did, teaching the CLI by association.

## Design rules (enforced)
Monospace for code/logs/registers/file contents; sans-serif for forms/labels. Dark high-contrast
theme (`#1a1a2e` background, light text). Traffic lights GREEN `#00c853` / YELLOW `#ffd600` /
RED `#ff1744` used consistently. No animations, gradients, marketing copy, or hero sections.
Every error states what to do next — never a raw traceback or bare HTTP code. Desktop-only.

## The governing rule (adopted permanently in v1.9.2, applied here)
Before shipping a page, read every label/button/error/status and ask: *would a person with 6
months of electronics and zero CS degree understand this and know what to do next?* If not,
rewrite it in plain English.

## Testing
`core/tests/test_web.py` uses FastAPI's `TestClient` against an `EAEDK_DB`-injected seeded DB:
each page's API returns the right engine data, create→assess→validate→export→logs round-trips,
and errors return a plain-English message (never a traceback). Existing 181 tests stay green.
