# 19 — Datasheet Intelligence (v2.3.0)

Drop a datasheet PDF on an unknown board and get a cited intelligence report, a "what's missing
and where to find it" list, deterministic risk warnings, the closest known board, an honest
"what EAEDK can/can't do", and a query engine that answers any board question with an explicit
confidence level on every answer. The user doesn't need to know what to look for.

**Preservation contract.** Baseline `pytest` 237, `eval 14/14` — confirmed before and after every
piece. Additive only: no existing CLI verb, schema, seed, rule, template, or test behaviour
changes. **No new migration or table for this whole build** — arch-risks are read from a seed
YAML at runtime; similarity is computed from existing tables; the report and query engine read
existing facts. The deterministic core (orchestrator, validation, risk-engine internals, gateway,
post-filter) is untouched; the LLM only elaborates and is post-filtered.

## Governing rules (applied to every line of output)
1. Every fact, answer, and suggestion shows its **confidence** (HIGH / MEDIUM / LOW / UNKNOWN).
2. UNKNOWN is always a hard warning **with search instructions** (what string, which chapter).
3. The post-filter runs on **every** query response — the LLM elaborates, never asserts a value.
4. Every answer ends with what to **do** next (an implication, a verify-before, or a search-for).
5. Beginner-clear + mid-level-useful.
6. The similarity engine is deterministic scoring; the LLM only explains the result.

## Pieces (built in this order; pytest + eval after each)

### Piece 3 — Architecture-aware risk rules (seed YAML, no schema change)
`packages/knowledge-seed/arch_risks.yaml`: per arch-family (cortex-m4, cortex-m0, cortex-a,
esp32/xtensa, avr) a list of `{title, severity, explanation, requires_fact?}`. Read at runtime by
`engines/ingest/arch_risks.py::risks_for_arch(arch, confirmed_keys)` — a warning is shown when the
board's arch matches and its `requires_fact` (if any) isn't among the confirmed facts. Deterministic,
not LLM. Surfaced in report Section 4.

### Piece 4 — Board Similarity Engine (deterministic, reads existing tables)
`repo.find_similar_boards(conn, arch, flash_bytes, ram_bytes, peripherals)` scores every other
board: +40 same arch, +20 flash within 2×, +20 RAM within 2×, +10 same vendor family, +10 shared
peripherals. Returns the top 3 with score + a confidence band. `engines/ingest/similarity.py`
turns a match into "what probably works the same / what you must verify / suggested template"
(deterministic). The LLM only narrates.

### Piece 1 — Datasheet Intelligence Report (extends ingest)
`engines/ingest/report.py::intelligence_report(conn, board, candidates)` builds the seven sections
deterministically: (1) extracted facts cited with page+confidence; (2) mandatory items not found,
each with *why mandatory* + *where to look*; (3) priority order with FOUND/NOT-FOUND; (4) arch
risks (Piece 3); (5) closest board (Piece 4); (6) what EAEDK can/can't do given the facts present;
(7) the single most important next step. CLI: `eaedk ingest --file <pdf> --board <name> --analyze`
prints it; the Web UI shows it automatically after every ingest.

### Piece 2 — Board Query Engine (answer anything, with confidence)
`engines/ingest/query.py::answer_query(conn, board, question, use_llm, gateway)`. Mode A (board has
datasheet facts) answers from cited SQLite facts at HIGH; Mode B (none) answers from DB/public
knowledge at MEDIUM with a verify-before warning; a mandatory-but-missing item returns UNKNOWN with
search instructions. The post-filter strips any uncited address/size/clock/timing from the LLM
elaboration (→ replaced, never asserted). Every answer ends with an action. CLI:
`eaedk ask --board <name> "<q>"` and `--file <pdf>` (the existing `eaedk ask <project>` path is
untouched — `--board` selects the new engine). Web: a query box on the Ingest and Board-detail
pages.

## Web UI (additive)
New **Ingest** tab: upload/point at a PDF → the 7-section report with confidence badges (GREEN
HIGH / YELLOW MEDIUM / RED LOW / GREY UNKNOWN) + an "ask anything" box. Board-detail page gains an
"ask about this board" box, the top-3 similar boards, and a "what EAEDK can do" summary.

## Testing
`core/tests/test_datasheet_intelligence.py`: arch-risk selection; similarity scoring (Blue Pill →
Nucleo-F103RB); the 7 report sections from synthetic candidates; query HIGH-with-citation,
MEDIUM-with-verify, UNKNOWN-with-search; post-filter strips an invented value from a query answer;
web routes round-trip. Existing 237 stay green.
