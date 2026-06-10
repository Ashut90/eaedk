# EAEDK — Datasheet Ingestion (v1.2.0)

**Date:** 2026-06-11
**Commands:** `eaedk ingest --file <pdf> --board <name>` · `--review` · `--confirm <id>` · `--reject <id>`

Removes the last manual bottleneck: board facts no longer have to be typed in by hand. Drop a
PDF datasheet and EAEDK extracts memory map / sizes / clock ceiling as **candidate facts with
cited provenance** — which the engineer reviews and confirms before anything enters the
knowledge base.

## Honest capability ceiling (assessed before building)

Per the architecture review, "a wrong extracted value is worse than no value." So this is **not**
a magic PDF→DB pipeline. With PyMuPDF (text only; no table libs available):

| Target | Method | Confidence |
|---|---|---|
| Flash/RAM base in a memory-map line (`Flash … 0x08000000`) | structured line | **HIGH** (`table`) |
| Flash/RAM size ("512 Kbytes of Flash") | prose regex | **MEDIUM** (`text`) |
| System-clock ceiling ("up to 100 MHz") | prose regex (clock-context only) | **MEDIUM** (`text`) |
| Register maps / **DDR AC timing** | out of deterministic scope | **LLM → LOW**, or UNKNOWN |

Uncertain → **no candidate**, never a guess. Most real datasheets will yield a handful of
MEDIUM candidates plus lots of UNKNOWN — the value is *cited candidates + a fast confirm flow*,
not full automation.

## Pipeline (human-in-the-loop is structural)

```
ingest --file X.pdf --board B
   │  PyMuPDF (lazy, optional)              extract.py (PURE regex extractors)
   ▼                                              │
 pages ──────────────────────────────────────────►  candidates  ──►  fact_candidates (PENDING)
                                   (--llm fills only missing keys, method=llm, confidence=LOW)

ingest --review --board B        # list candidates with [confidence/method] + page §section + snippet
ingest --confirm <id>            # the ONLY write path -> record_fact(source_type='DATASHEET', source_id=…)
ingest --reject  <id>            # discard
```

- One `sources(type='datasheet')` row per ingest (filename + sha256); every candidate cites it
  with `page`, nearest `section` heading, and the matched `snippet`.
- **No silent writes.** Ingestion only stages `fact_candidates`. `confirm` is the sole path that
  touches `facts`, via the existing `record_fact` write-through (now accepts a shared
  `source_id`). Confirmation counts as human verification (`verified_by_human=1`).
- **`--llm`** is optional and only attempts keys the deterministic pass left empty; results are
  marked `LOW`/`llm` and still require confirmation. Guarded — no model, no candidates.

## Dependency

PyMuPDF is an **optional** extra (`pip install eaedk[ingest]` / `pip install pymupdf`), imported
lazily. The rest of EAEDK still runs on PyYAML alone; without it, `ingest` prints a clear install
message.

## Verification

- `pytest` → **67 passed** (5 new: method/confidence assignment, no-guess-when-absent, a real
  fitz-generated PDF round-trip that stages without writing facts, confirm-commits-with-DATASHEET-
  provenance, reject-discards).
- `eval run` → **11/11**.
- Live: a synthetic STM32 datasheet PDF → 5 candidates (2 HIGH memory-map bases, 3 MEDIUM
  sizes/clock); confirming `flash_base` committed `0x08000000` with `source_type=DATASHEET`,
  `citation_page=1`, `§3.2 Memory mapping`, `verified_by_human=1`.
