# EAEDK — Canonical Engineering-Fact Layer

**Date:** 2026-06-10
**Status:** Canonical truth-layer architecture (V1 foundation).
**Decision:** Evolve the existing `facts` table — do **not** fork a parallel `engineering_facts`
table or flatten provenance.

## Why evolve, not fork

A unified, polymorphic fact store already existed (`facts`: `board_id, kind, key, value,
citation_id, confidence, verified_by_human`). Adding a parallel `engineering_facts` table
with a flat `source_type` + `citation_detail` string would have (a) duplicated `sources.type`,
(b) regressed structured provenance (`citations.page/section/snippet`) that the post-filter and
V1 datasheet ingestion depend on, and (c) tempted moving typed board identity into EAV. So we
evolved instead.

## What changed (migration `0002_engineering_facts.sql`, additive)

- `facts` gains two columns:
  - `domain TEXT` — coarse dimension: `MEMORY | PINMUX | CLOCK | POWER | …` (extensible).
  - `source_type TEXT` — `USER_INPUT | DATASHEET | TRM | SDK_DOC | SCHEMATIC` (CHECKed).
- Existing partition facts are **backfilled** (`domain='MEMORY'`, `source_type='USER_INPUT'`).
- New read VIEW **`engineering_facts`** joins `facts → citations → sources`, so the unified
  read surface carries provenance (`citation_page`, `citation_detail`, `citation_snippet`,
  `source_doc_type`, `source_title`) — never flattened.

Typed board identity (`boards.flash_base/flash_bytes/ram_*`) stays in `boards`; deterministic
rules keep their fast typed reads and NOT NULL guarantees.

## Canonical write-through: `repo.record_fact(...)`

The single entry point for writing facts (onboarding now; datasheet/SDK parsers next):

```
record_fact(conn, *, board_id, domain, fact_key, fact_value, source_type, confidence,
            kind=None, citation_section=None, citation_page=None, snippet=None,
            verified_by_human=None) -> fact_id
```

It builds the provenance chain (`source` of the mapped doc-type → `citation`) and inserts the
`facts` row with `domain`/`source_type`. `source_type` and the linked `sources.type` are kept
consistent by this one path (no drift). `verified_by_human` defaults to true for HIGH.

`SCHEMATIC` maps to a `manual` source (no dedicated `sources.type` yet) while the precise
classifier lives on `facts.source_type`.

## Consumers

- **`onboard.py`** writes partition facts via `record_fact(domain='MEMORY',
  source_type='USER_INPUT', kind='partition', …)` instead of a raw INSERT.
- **`postfilter.py`** builds its allowlist by reading `engineering_facts.fact_value` through
  the VIEW (with `citation_id IS NOT NULL`), plus absolute partition addresses
  (`flash_base + offset`). `_collect_ints` now also parses JSON-encoded fact values, so
  offsets/sizes stored as facts are admitted (previously only surfaced via parsed inputs).

## Verification

- `pytest` → **23 passed** (4 new in `test_facts_layer.py`: write-through + structured
  provenance, source_type validation, onboarding→view, postfilter→view).
- `eval run` → **11/11**.
- Real DB migrated v1→v2: the 4 live STM32F407 partition facts backfilled to
  `MEMORY/USER_INPUT`; allowlist resolves abs slot addresses through the VIEW.
