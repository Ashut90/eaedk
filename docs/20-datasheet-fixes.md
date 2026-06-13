# 20 — Datasheet Fixes (v2.3.1)

A real dogfood on an STM32F303RE datasheet (a Cortex-M4 board not in EAEDK's DB) found two
blockers and three smaller issues in the v2.3.0 datasheet intelligence. This release fixes all
five, additively. Baseline `pytest` 252, `eval 14/14` — confirmed before and after every change.
No new migration/table; the deterministic core (orchestrator, validation, risk-engine, gateway,
post-filter) is untouched; no existing CLI verb/schema/seed/rule/template/test behaviour changes.

## Fix 1 (BLOCKER) — unknown-board on-ramp
The Datasheet tab's board dropdown only listed existing boards, so the actual use case — a board
EAEDK doesn't know — was impossible, and ingest returned a dead-end "Board not found".
- **Web:** the board dropdown gains a "➕ New board (not in the list)" option; choosing it reveals
  a name field + an architecture dropdown (Cortex-M0/M0+/M3/M4/M7/A7/A53, Xtensa-LX6, AVR, other).
  `/api/ingest` accepts `new_board` + `arch` and auto-creates a skeleton board (null geometry)
  before analysing.
- **CLI:** `eaedk ingest --file <pdf> --board <name> --arch <arch> --analyze` auto-creates the
  skeleton when the board doesn't exist and `--arch` is given; if it doesn't exist and no `--arch`,
  it prompts for the architecture interactively. Never a "Board not found" dead end.
- A new `repo.create_skeleton_board(name, arch)` helper does the insert (no raw SQL in callers).

## Fix 2 (BLOCKER) — extractor misses facts in prose
The old extractor took the first hex per line and required the memory keyword *after* a size, so
"The Flash memory base address is 0x08000000 … 512 Kbytes … SRAM base address is 0x20000000 with
64 Kbytes of SRAM. … up to 72 MHz" yielded only flash base + flash size. The rewrite is
**sentence-aware**:
- Splits each line into sentences; for every hex it assigns the base to the **nearest** preceding
  `Flash`/`SRAM`/`RAM` keyword (so a flash hex no longer mis-labels a RAM base).
- Sizes are matched with the keyword **before or after** and assigned to the nearest of
  `Flash`/`RAM`; units `KB`/`MB`/`Kbytes`/`Mbytes`/`KiB` all convert (`64 Kbytes`→65536,
  `512 KB`→524288).
- Clock max from "up to N MHz" / "N MHz max" on a clock-context sentence (`clock`/`sysclk`/`HCLK`/
  `HSI`/`HSE`/`frequency`).
- **Confidence rule is unchanged:** a short label+hex line is a table → HIGH; prose is MEDIUM —
  prose is *never* upgraded to HIGH. The only change is coverage, not confidence accuracy.
- The existing fixture (`test_ingest`) still yields exactly 2 HIGH + 3 MEDIUM; the STM32F303RE
  sheet now yields all 5 key facts (flash base/size, RAM base/size, max clock).

## Fix 3 (MED) — honest "not extracted" vs "no datasheet"
The query engine said "once a datasheet is ingested" even after ingest. It now distinguishes:
no datasheet ingested → "run `eaedk ingest … --analyze`"; datasheet ingested but the fact wasn't
extracted → "your datasheet was ingested but I couldn't extract this automatically — search for
'SRAM'/'RAM size', then confirm with `eaedk ingest --board <name> --review`".

## Fix 4 (MED) — similarity uses extracted facts
A freshly-ingested board's stored `flash_bytes`/`ram_bytes` are still null (the facts are pending
confirmation). `similar_with_guidance` now resolves the query board's geometry from stored columns
→ confirmed facts → **pending extracted candidates**, so a just-ingested 512 KB Cortex-M4 scores
~80% against the F411, with the contributing values labelled "(unconfirmed)".

## Fix 5 (LOW) — human-readable fact labels
A shared `FACT_LABELS` map (`flash_base`→"Flash start address", `flash_bytes`→"Flash size",
`ram_base`→"RAM start address", `ram_bytes`→"RAM size", `sysclk_max_hz`→"Maximum system clock",
`vtor_offset`→"Vector table offset") is used everywhere the report and query show a fact name —
never a raw DB key.

## Verification
Ingest the STM32F303RE sheet → ≥4 of 5 key facts (gets 5); add it as a new board from the
Datasheet tab → no "Board not found"; "how much RAM?" → the ingested-but-not-extracted message
when missed; freshly-ingested Cortex-M4 similarity >60%; Section 1 shows readable labels. New
tests in `core/tests/test_datasheet_fixes.py`; existing 252 stay green.
