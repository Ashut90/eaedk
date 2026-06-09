# EAEDK — MVP Specification

**Status:** Spec for sign-off. No app code until approved (per [00-architecture-review.md](00-architecture-review.md) §13).
**Builds on:** Architecture Review §§6, 10, 12, 13. Locked decisions D1–D4 apply.
**Date:** 2026-06-10

---

## 0. MVP Definition (what "done" means)

The MVP is a **CLI tool that is valuable with the LLM turned off** (D1 offline-only, D2 CLI-first).
An engineer can:

1. Create a project for a board (from the seed DB or by manual entry).
2. Get the right template auto-selected and track checklist progress.
3. Enter hardware/software facts (each carrying a citation + confidence).
4. Run the **Validation Engine** → deterministic `PASS / FAIL / UNKNOWN` with reasons.
5. Run the **Risk Engine** → severity-tagged, cited risks.
6. Receive every answer in the fixed schema with `facts[] / assumptions[] / unknowns[]`.
7. (Optional) Ask the LLM to *explain* validated results — never to assert facts.
8. Record ADR-style decisions; reopen the project later with full state intact.

**Explicitly out of MVP** (→ V1+): datasheet auto-ingestion, log analysis, toolchain/errata
matrix beyond a static seed, repo analysis, desktop/web UI, project discovery mode.

The five deliverables below (§§1–5) are exactly the §13 next-deliverable list.

---

## 1. Data Models (field-level) + Migration Plan

SQLite, single file at `~/.eaedk/eaedk.db` (override via `--db` / `EAEDK_DB`). Local-first,
single-writer (D4). All timestamps are ISO-8601 UTC text. All ids are `INTEGER PRIMARY KEY`
unless noted. Booleans stored as `INTEGER` 0/1. JSON columns are validated text.

### 1.1 Provenance (the backbone — every fact points here)

```sql
CREATE TABLE sources (
  id          INTEGER PRIMARY KEY,
  type        TEXT NOT NULL CHECK(type IN
                ('datasheet','trm','sdk_doc','errata','manual','web','seed','user')),
  title       TEXT NOT NULL,
  uri         TEXT,                 -- file path or URL; NULL for inline user input
  hash        TEXT,                 -- sha256 of source content when applicable
  created_at  TEXT NOT NULL
);

CREATE TABLE citations (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  page        INTEGER,              -- nullable (user/seed facts)
  section     TEXT,                 -- e.g. "Table 3, Memory map"
  bbox_json   TEXT,                 -- reserved for V1 datasheet ingestion; NULL in MVP
  snippet     TEXT                  -- short quoted justification
);
```

Confidence is an enum used everywhere: `HIGH | MEDIUM | LOW | UNKNOWN`.

> **MVP rule:** even a manually entered fact gets a `source` (type `user` or `seed`) and a
> `citation`. There is no such thing as an uncited fact in the schema.

### 1.2 Board knowledge

```sql
CREATE TABLE socs (
  id      INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,     -- "STM32F411RE"
  vendor  TEXT,                     -- "STMicroelectronics"
  arch    TEXT NOT NULL,            -- "arm-cortex-m4", "arm-cortex-a53", "riscv32"
  notes   TEXT
);

CREATE TABLE boards (
  id              INTEGER PRIMARY KEY,
  soc_id          INTEGER NOT NULL REFERENCES socs(id),
  name            TEXT NOT NULL UNIQUE,  -- "Nucleo-F411RE"
  flash_base      INTEGER,               -- e.g. 0x08000000
  flash_bytes     INTEGER,               -- e.g. 524288
  ram_base        INTEGER,               -- e.g. 0x20000000
  ram_bytes       INTEGER,               -- e.g. 131072
  ddr_type        TEXT,                  -- NULL for MCUs; "LPDDR4" etc.
  ddr_bytes       INTEGER,
  primary_storage TEXT,                  -- "internal_flash","emmc","nand","nor","sd"
  boot_modes_json TEXT,                  -- ["system_memory","flash","sram"]
  source_id       INTEGER REFERENCES sources(id),
  confidence      TEXT NOT NULL DEFAULT 'HIGH'
);

CREATE TABLE board_capabilities (
  id           INTEGER PRIMARY KEY,
  board_id     INTEGER NOT NULL REFERENCES boards(id),
  capability   TEXT NOT NULL,            -- "uart","usb_dfu","ethernet","i2c"
  details_json TEXT
);
```

### 1.3 Facts (confirmed/extracted hardware facts, per board)

```sql
CREATE TABLE facts (
  id              INTEGER PRIMARY KEY,
  board_id        INTEGER NOT NULL REFERENCES boards(id),
  kind            TEXT NOT NULL CHECK(kind IN
                    ('register','memmap','clock','pinmux','timing','partition')),
  key             TEXT NOT NULL,         -- "ddr.cas_latency", "vector_table.addr"
  value           TEXT NOT NULL,         -- stored as text; typed on read
  citation_id     INTEGER REFERENCES citations(id),
  confidence      TEXT NOT NULL,
  verified_by_human INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);
```

### 1.4 Templates (versioned data — see §3 for the file schema)

```sql
CREATE TABLE templates (
  id        INTEGER PRIMARY KEY,
  key       TEXT NOT NULL,               -- "bare_metal_bootloader"
  name      TEXT NOT NULL,
  version   INTEGER NOT NULL,
  goal_type TEXT NOT NULL,               -- matches projects.goal_type
  active    INTEGER NOT NULL DEFAULT 1,
  UNIQUE(key, version)
);

CREATE TABLE template_items (
  id                   INTEGER PRIMARY KEY,
  template_id          INTEGER NOT NULL REFERENCES templates(id),
  item_key             TEXT NOT NULL,    -- "vector_table_placement"
  text                 TEXT NOT NULL,
  category             TEXT NOT NULL,    -- "memory_layout","integrity","recovery"...
  required_inputs_json TEXT NOT NULL,    -- ["vector_table_addr","flash_base"]
  validation_rule_keys_json TEXT NOT NULL, -- ["VECTOR_TABLE_PLACEMENT"]
  ordinal              INTEGER NOT NULL
);
```

### 1.5 Projects (the missing state pillar — A3)

```sql
CREATE TABLE projects (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  board_id   INTEGER REFERENCES boards(id),   -- nullable until a board is set
  goal_type  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active',  -- active|done|archived
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE project_inputs (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  key         TEXT NOT NULL,             -- "estimated_image_size","kernel_load_addr"
  value       TEXT NOT NULL,
  source      TEXT NOT NULL CHECK(source IN ('user','extracted','seed')),
  citation_id INTEGER REFERENCES citations(id),
  confidence  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE(project_id, key)               -- latest value per key; history via decisions
);

CREATE TABLE project_checklist (
  id               INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES projects(id),
  template_item_id INTEGER NOT NULL REFERENCES template_items(id),
  status           TEXT NOT NULL DEFAULT 'todo'
                     CHECK(status IN ('todo','done','na','blocked')),
  note             TEXT,
  UNIQUE(project_id, template_item_id)
);

CREATE TABLE project_facts (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  fact_id    INTEGER NOT NULL REFERENCES facts(id),
  status     TEXT NOT NULL CHECK(status IN ('confirmed','assumed','unknown'))
);

CREATE TABLE decisions (                 -- ADR-style log; also the audit trail
  id               INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES projects(id),
  title            TEXT NOT NULL,
  rationale        TEXT,
  alternatives_json TEXT,
  made_at          TEXT NOT NULL
);

CREATE TABLE risks (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  rule_key    TEXT NOT NULL,
  severity    TEXT NOT NULL CHECK(severity IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
  explanation TEXT NOT NULL,
  mitigation  TEXT,
  citation_id INTEGER REFERENCES citations(id),
  status      TEXT NOT NULL DEFAULT 'open', -- open|accepted|resolved
  created_at  TEXT NOT NULL
);
```

### 1.6 Rules (data-driven; see §2)

```sql
CREATE TABLE risk_rules (
  id              INTEGER PRIMARY KEY,
  key             TEXT NOT NULL UNIQUE,
  goal_type       TEXT,                  -- NULL = applies to all goals
  condition_dsl   TEXT NOT NULL,         -- see §2.4 mini-DSL
  severity        TEXT NOT NULL,
  explanation_tmpl TEXT NOT NULL,
  mitigation_tmpl TEXT
);
```

Validation rules are **code, not data** in the MVP (pure functions, §2.2) — their *catalog*
(§2.3) is the spec. Risk rules are data because engineers will tune thresholds.

### 1.7 Eval

```sql
CREATE TABLE eval_cases (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  goal_type   TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  expected_json TEXT NOT NULL
);

CREATE TABLE eval_runs (
  id        INTEGER PRIMARY KEY,
  case_id   INTEGER NOT NULL REFERENCES eval_cases(id),
  passed    INTEGER NOT NULL,
  diff_json TEXT,
  run_at    TEXT NOT NULL
);
```

### 1.8 Deferred to V1 (tables present in §6 but NOT in MVP)

`documents`, `doc_chunks`, vector store, `toolchains`, `sdks`, `compatibility`, `errata`,
`log_files`, `log_signatures`, `log_analyses`, `feedback`. They stay in the §6 master schema;
MVP migrations simply don't create them yet.

### 1.9 Migration Plan

- **Mechanism:** numbered, append-only SQL files in `core/eaedk/store/migrations/`
  (`0001_init.sql`, `0002_*.sql`, …). A tiny runner applies any file whose number exceeds
  `PRAGMA user_version`, inside a transaction, then bumps `user_version`. No Alembic — keeps
  the offline single-binary story simple and inspectable.
- **Seed data** is *not* a migration. It loads from `packages/knowledge-seed/` (§3.5) via
  `eaedk db seed`, so seed updates don't require schema bumps and are diffable as YAML.
- **Forward-only** in MVP (no down-migrations); a corrupt local DB is recreated + reseeded.
- `PRAGMA foreign_keys = ON;` and `PRAGMA journal_mode = WAL;` set on every connection.

---

## 2. Validation Rule Catalog (the trust core)

### 2.1 Contract

Every validation rule is a pure function:

```
validate(inputs: dict) -> ValidationResult
ValidationResult = { check, status, reason, inputs_used[], severity_on_fail, citations[] }
status ∈ { PASS, FAIL, UNKNOWN }
```

- **UNKNOWN** when any required input is missing or its confidence is `LOW`/`UNKNOWN`. UNKNOWN
  is *not* a soft pass — it forces a "Missing Information" entry and blocks sign-off of the
  related checklist item.
- **FAIL** halts the workflow for that goal (orchestrator refuses to emit a "feasible" verdict).
- Rules **never** call the LLM and **never** invent a missing input.

### 2.2 Why code, not DSL (for validation specifically)

Capacity/overlap/address math needs integer arithmetic, range checks, and list operations.
Encoding that in a string DSL is more error-prone than a tested pure function. Risk *thresholds*
(§2.4) are data; validation *logic* is code. This split is deliberate.

### 2.3 The catalog

Inputs are resolved from `project_inputs` first, then `boards`/`facts`. Margin defaults are
constants (overridable per project input).

| Key | Goal(s) | Inputs | PASS when | FAIL when | UNKNOWN when | Sev |
|---|---|---|---|---|---|---|
| `FLASH_CAPACITY` | all | `estimated_image_size`, `board.flash_bytes`, `flash_margin`(=0.10) | `size ≤ flash*(1-margin)` | `size > flash` | size or flash missing | HIGH |
| `RAM_BUDGET` | all | `stack_size`,`heap_size`,`static_size`,`board.ram_bytes` | `Σ ≤ ram*(1-0.10)` | `Σ > ram` | any term missing | HIGH |
| `VECTOR_TABLE_PLACEMENT` | bootloader | `vector_table_addr`,`board.flash_base`,`board.flash_bytes` | addr within `[flash_base, flash_base+flash_bytes)` and 512-byte aligned | outside region / misaligned | addr missing | HIGH |
| `BOOTLOADER_APP_NO_OVERLAP` | bootloader,ota | `bl_region`,`app_region` (`{base,size}`) | regions disjoint AND both within flash | overlap or out-of-flash | either region missing | HIGH |
| `PARTITION_LAYOUT_FITS` | ota,linux | `partitions[]`(`{name,base,size}`),`primary_storage_bytes` | `Σ size ≤ storage` AND each within storage | exceeds storage | storage size or partitions missing | HIGH |
| `PARTITION_NO_OVERLAP` | ota,linux | `partitions[]` | no two ranges overlap | any overlap | partitions missing | HIGH |
| `PARTITION_AB_SYMMETRY` | ota | `partitions[]` with roles `slot_a`,`slot_b` | `size(slot_a)==size(slot_b)` | sizes differ | a/b roles missing | MEDIUM |
| `RECOVERY_PRESENT` | ota | `partitions[]` | a `recovery` (or `slot_b`) role exists | none exists | partitions missing | HIGH |
| `LOAD_ADDR_CONFLICT` | uboot,linux | `kernel_load_addr`,`dtb_load_addr`,`ddr_base`,`ddr_bytes`,`ddr_init_region`(opt) | load addrs within DDR, not in `ddr_init_region`, kernel≠dtb region | addr outside DDR or inside init region or kernel/dtb overlap | any addr or ddr geometry missing | HIGH |
| `DDR_TIMING_VERIFIED` | uboot | `facts[kind=timing]` for DDR with `verified_by_human=1` | required timing facts present & human-verified | a required timing fact present but `verified_by_human=0` | timing facts absent | HIGH |
| `BOOT_FLOW_CONSISTENCY` | uboot,linux | `bootloader_load_addr`,`kernel_load_addr`,`init_addr/entry` | addresses form a non-overlapping, ordered chain | any overlap/contradiction | any stage addr missing | HIGH |
| `CONSOLE_UART_DEFINED` | uboot,linux | `console_uart` (and `stdout_path` for linux) | present & non-empty | — | missing | MEDIUM |
| `TOOLCHAIN_ARCH_MATCH` | all | `toolchain_arch`,`soc.arch` | arch families match (e.g. `arm-*`↔`arm-cortex-*`) | mismatch | toolchain_arch missing | HIGH |
| `SDK_HOST_OS_MATCH` | all | `host_os`,`sdk_required_host_os`(opt) | match or no SDK requirement | declared mismatch | host_os missing | MEDIUM |
| `DRIVER_COMPATIBLE_STRING` | driver | `dtb_compatible`,`peripheral` | non-empty compatible string present | — | missing | MEDIUM |
| `REGISTER_MAP_PRESENT` | driver | `facts[kind=register]` for peripheral | ≥1 register fact, human-verified | register fact present but unverified | none present | HIGH |
| `PINMUX_CONFLICT` | all | `pin_assignments[]`(`{pin,signal}`) | no pin claimed by >1 signal | a pin mapped to 2+ distinct signals | partitions/pins missing | HIGH |
| `POWER_SEQUENCE` | all | `power_rails[]`(`{name,order,depends_on?}`) | each `depends_on` has strictly smaller order; order indices distinct | dependency powers up at/after dependent, or duplicate order, or unknown dep | rails missing | HIGH |

Each row maps 1:1 to template checklist items (§3) via `validation_rule_keys_json`. A checklist
item cannot be marked `done` while its rule is `FAIL` or `UNKNOWN` (enforced by the orchestrator).

### 2.4 Risk-rule mini-DSL (data-driven, §1.6)

Risk rules express tunable thresholds over the same resolved inputs. Grammar (MVP subset):

```
condition := comparison (("and"|"or") comparison)*
comparison := ident op number | ident op ident
op := ">" | ">=" | "<" | "<=" | "==" | "!="
ident := dotted.path resolved from inputs/board (e.g. estimated_image_size, board.flash_bytes)
```

Example seed risk rules:

```yaml
- key: FLASH_TIGHT
  goal_type: null
  condition_dsl: "estimated_image_size > board.flash_bytes * 0.9"
  severity: HIGH
  explanation_tmpl: "Estimated image ({estimated_image_size} B) exceeds 90% of flash ({board.flash_bytes} B)."
  mitigation_tmpl: "Reduce image size, enable LTO/-Os, or move assets to external storage."
- key: WATCHDOG_UNCONFIRMED
  goal_type: bootloader
  condition_dsl: "watchdog_enabled == 0"
  severity: MEDIUM
  explanation_tmpl: "Watchdog not confirmed enabled from first instruction."
  mitigation_tmpl: "Enable IWDG/WWDG before main application handoff."
- key: DDR_GUESSED
  goal_type: uboot
  condition_dsl: "ddr_timing_verified == 0"
  severity: HIGH
  explanation_tmpl: "DDR timing not verified from datasheet."
  mitigation_tmpl: "Confirm CL/tRCD/tRP from the TRM before first boot."
```

The DSL evaluator is a small, sandboxed expression parser (no Python `eval`). Unknown idents →
the rule yields `UNKNOWN` severity and is surfaced as missing info, never silently skipped.

---

## 3. Template Schema (versioned YAML)

### 3.1 File shape

Templates live in `packages/templates/<key>.v<version>.yaml`, loaded into the `templates` /
`template_items` tables by `eaedk db seed`. Schema:

```yaml
key: bare_metal_bootloader
name: Bare-Metal Bootloader
version: 1
goal_type: bootloader
items:
  - key: vector_table_placement
    text: "Vector table placement confirmed"
    category: memory_layout
    required_inputs: [vector_table_addr]
    validation_rules: [VECTOR_TABLE_PLACEMENT]
  # ...
```

`required_inputs` drives the "Missing Information" section; `validation_rules` ties each item to
the §2 catalog. An item with empty `validation_rules` is a *manual* checklist item (engineer
judgment, no deterministic check).

### 3.2 Mapping of the 5 existing templates

All five from the system prompt are encoded. Item → rule mapping summary:

**`bare_metal_bootloader` (goal_type: bootloader)**
| item_key | rules |
|---|---|
| vector_table_placement | `VECTOR_TABLE_PLACEMENT` |
| flash_layout_defined | `BOOTLOADER_APP_NO_OVERLAP`, `FLASH_CAPACITY` |
| ram_layout_defined | `RAM_BUDGET` |
| power_sequencing | `POWER_SEQUENCE` |
| clock_init_sequence | *(manual)* |
| watchdog_strategy | *(manual; feeds `WATCHDOG_UNCONFIRMED` risk)* |
| integrity_verification | *(manual)* |
| fallback_rollback | *(manual)* |
| recovery_entry_condition | *(manual)* |
| update_interface | *(manual)* |
| flash_write_protection | *(manual)* |

**`uboot_bringup` (goal_type: uboot)**
| item_key | rules |
|---|---|
| ddr_init | `DDR_TIMING_VERIFIED` |
| clock_tree | *(manual)* |
| console_uart | `CONSOLE_UART_DEFINED` |
| storage_interface | *(manual)* |
| env_storage | *(manual)* |
| bootargs_reviewed | *(manual)* |
| dtb_loading | *(manual)* |
| kernel_load_addr | `LOAD_ADDR_CONFLICT`, `BOOT_FLOW_CONSISTENCY` |
| netboot_fallback | *(manual)* |
| uboot_errata | *(manual; V1 errata matrix)* |

**`linux_bringup` (goal_type: linux)**
| item_key | rules |
|---|---|
| kernel_xcompile | `TOOLCHAIN_ARCH_MATCH` |
| device_tree | *(manual)* |
| console_in_dtb | `CONSOLE_UART_DEFINED` |
| rootfs_type | *(manual)* |
| critical_drivers | *(manual)* |
| kernel_config | *(manual)* |
| boot_flow_documented | `BOOT_FLOW_CONSISTENCY`, `LOAD_ADDR_CONFLICT` |
| first_boot_milestone | *(manual)* |
| logging_strategy | *(manual)* |

**`failsafe_ota` (goal_type: ota)**
| item_key | rules |
|---|---|
| partition_layout | `PARTITION_LAYOUT_FITS`, `PARTITION_NO_OVERLAP`, `PARTITION_AB_SYMMETRY` |
| update_state_machine | *(manual)* |
| rollback_trigger | *(manual)* |
| watchdog_integration | *(manual)* |
| signature_verification | *(manual)* |
| update_source | *(manual)* |
| power_failure_recovery | *(manual)* |
| progress_persistence | *(manual)* |
| downgrade_protection | *(manual)* |
| factory_recovery_path | `RECOVERY_PRESENT` |

**`linux_driver` (goal_type: driver)**
| item_key | rules |
|---|---|
| peripheral_confirmed | *(manual)* |
| dtb_node | `DRIVER_COMPATIBLE_STRING` |
| pin_mux | `PINMUX_CONFLICT` |
| driver_type | *(manual)* |
| register_map | `REGISTER_MAP_PRESENT` |
| interrupt_handling | *(manual)* |
| dma_evaluated | *(manual)* |
| power_mgmt_hooks | *(manual)* |
| driver_test_strategy | *(manual)* |
| upstream_driver_check | *(manual)* |

### 3.3 Versioning

`(key, version)` is unique; a project pins the template version it started with so later template
edits don't silently change a live checklist. Bumping a template = new file `*.v2.yaml`.

### 3.4 Seed boards (D3 — ~5–10)

`packages/knowledge-seed/boards/*.yaml`, each with a `seed` source + citation. MVP target set:
STM32F411RE (Nucleo), ESP32-DevKitC, Raspberry Pi 4 (BCM2711), BeagleBone Black (AM335x),
i.MX8M Mini EVK, STM32MP157 as a DDR/U-Boot exemplar, and **WIZnet W5500-EVB-Pico** (RP2040)
as a no-internal-flash + Ethernet-over-SPI exemplar. Every numeric field carries a citation to
the public datasheet/RM section.

**`wiznet_w5500_evb_pico` seed values:**
| field | value | note |
|---|---|---|
| soc.name / arch | RP2040 / `arm-cortex-m0plus` | dual Cortex-M0+, ≤133 MHz |
| flash_base / flash_bytes | 0x10000000 / 2097152 | **external** QSPI NOR (2 MB); XIP window |
| ram_base / ram_bytes | 0x20000000 / 270336 | 264 KB on-chip SRAM (6 banks) |
| ddr_type / ddr_bytes | NULL / NULL | MCU, no DRAM |
| primary_storage | `nor` | external QSPI flash |
| boot_modes_json | `["bootrom_usb","bootrom_flash"]` | BOOTSEL → USB MSC; else 2nd-stage from flash |
| capabilities | `spi, ethernet(W5500), usb, uart, i2c` | W5500 = hardwired TCP/IP over SPI |

> **Why it stresses the engine well:** RP2040 has *no on-die program flash* — it boots from
> external QSPI via the bootrom into a 256-byte second-stage bootloader, then XIP. So
> `VECTOR_TABLE_PLACEMENT` and `BOOTLOADER_APP_NO_OVERLAP` must reason about the `0x10000000`
> XIP window (with the 2nd-stage at `0x10000000`), not an on-die flash base. The W5500 also
> makes it the natural seed board for the `linux_driver` template (SPI Ethernet, `compatible =
> "wiznet,w5500"`, register map from the W5500 datasheet) and for a bootloader doing network
> update over Ethernet.

### 3.5 Seed layout

```
packages/
├─ templates/            # *.v1.yaml  (the 5 above)
└─ knowledge-seed/
   ├─ boards/            # one yaml per board
   ├─ risk_rules.yaml    # §2.4 seed rules
   └─ eval_cases.yaml    # §5 golden cases
```

---

## 4. CLI Command Surface (verbs → engines)

Typer app, `eaedk`. Global flags: `--db PATH`, `--json` (machine output), `--no-llm`
(default on; LLM is opt-in via `--llm`). Output renders the fixed response schema (§4.2).

### 4.1 Commands

```
eaedk db init                          # apply migrations
eaedk db seed [--force]                # load templates + seed boards/rules/cases

eaedk board list [--query Q]           # → Board Knowledge Engine
eaedk board show NAME
eaedk board add                        # interactive manual entry (creates user source+citation)

eaedk project new NAME --board B --goal GOAL_TYPE   # creates project, selects template, inits checklist
eaedk project list
eaedk project show NAME                 # full state: checklist, facts, risks, decisions
eaedk project archive NAME

eaedk input set NAME KEY VALUE [--confidence C] [--cite "section"]  # → project_inputs
eaedk input list NAME

eaedk checklist show NAME               # template items + status + linked rule outcomes
eaedk checklist set NAME ITEM_KEY STATUS [--note ...]   # blocked if rule FAIL/UNKNOWN

eaedk validate NAME [--rule KEY]        # → Validation Engine: PASS/FAIL/UNKNOWN table
eaedk risk NAME                         # → Risk Engine: severity-tagged, cited risks

eaedk decision add NAME --title T [--rationale R] [--alt JSON]   # ADR log

eaedk ask NAME "question" --llm         # ONLY LLM-facing verb; refuses uncited factual claims
eaedk explain NAME --rule KEY [--llm]   # LLM prose over an already-validated result

eaedk eval run [--case NAME]            # → Eval harness, compares to expected_json
```

### 4.2 Fixed response schema (every engineering answer)

Mirrors the system prompt's RESPONSE FORMAT, made structural:

```
## Feasibility Assessment      (derived from Validation Engine: any FAIL → not feasible)
## Risk Summary                (Risk Engine output, HIGH→LOW)
## Template Applied            (template name@version + checklist status counts)
## Architecture / Plan         (deterministic where possible; LLM prose only if --llm)
## Facts Confirmed             (facts/inputs with HIGH confidence + citations)
## Assumptions Made            (MEDIUM/LOW confidence inputs)
## Missing Information          (UNKNOWN rules + required_inputs not yet provided)
## Recommended Next Step       (single clearest action; usually "resolve UNKNOWN X")
```

`--json` emits the same content as `{feasibility, risks[], template, facts[], assumptions[],
unknowns[], next_step}` so the contract is machine-checkable (and is what eval compares).

### 4.3 LLM guardrail in the CLI

With `--llm`, the orchestrator passes only validated facts + citations into the prompt and runs a
post-filter: any sentence asserting a hardware fact without a matching citation id is stripped and
replaced with `[uncited claim removed — verify against TRM]`. With the default `--no-llm`, `ask`/
`explain` return the deterministic assembly only. This makes D1's "convenience layer" structural.

---

## 5. First Golden Eval Cases

Stored in `packages/knowledge-seed/eval_cases.yaml`; run by `eaedk eval run`. Each case feeds
`inputs_json` through the deterministic engines and asserts `expected_json` (engines are
deterministic, so these are exact, LLM-free).

```yaml
- name: stm32f411_bootloader_fits
  goal_type: bootloader
  inputs:
    board: "Nucleo-F411RE"          # flash 512KiB, ram 128KiB
    estimated_image_size: 32768
    vector_table_addr: 0x08000000
    bl_region: {base: 0x08000000, size: 0x4000}
    app_region: {base: 0x08004000, size: 0x7C000}
    stack_size: 8192
    heap_size: 16384
    static_size: 16384
  expected:
    feasibility: feasible
    validations:
      FLASH_CAPACITY: PASS
      VECTOR_TABLE_PLACEMENT: PASS
      BOOTLOADER_APP_NO_OVERLAP: PASS
      RAM_BUDGET: PASS
    risks_contains: []

- name: stm32f411_image_overflow
  goal_type: bootloader
  inputs:
    board: "Nucleo-F411RE"
    estimated_image_size: 600000     # > 512KiB
    vector_table_addr: 0x08000000
  expected:
    feasibility: not_feasible
    validations:
      FLASH_CAPACITY: FAIL
    risks_contains: [FLASH_TIGHT]

- name: uboot_ddr_unverified_is_unknown
  goal_type: uboot
  inputs:
    board: "STM32MP157"
    console_uart: "UART4"
    kernel_load_addr: 0xC2000000
    dtb_load_addr: 0xC4000000
    ddr_base: 0xC0000000
    ddr_bytes: 0x20000000
    # no human-verified DDR timing facts provided
  expected:
    feasibility: blocked            # UNKNOWN present
    validations:
      DDR_TIMING_VERIFIED: UNKNOWN
      LOAD_ADDR_CONFLICT: PASS
      CONSOLE_UART_DEFINED: PASS
    risks_contains: [DDR_GUESSED]
    unknowns_contains: ["DDR timing"]

- name: ota_ab_asymmetry_fails
  goal_type: ota
  inputs:
    board: "BeagleBone-Black"
    primary_storage_bytes: 4294967296
    partitions:
      - {name: "slot_a", role: "slot_a", base: 0x0,        size: 0x10000000}
      - {name: "slot_b", role: "slot_b", base: 0x10000000, size: 0x08000000}  # half of A
  expected:
    feasibility: not_feasible
    validations:
      PARTITION_LAYOUT_FITS: PASS
      PARTITION_NO_OVERLAP: PASS
      PARTITION_AB_SYMMETRY: FAIL
      RECOVERY_PRESENT: PASS

- name: linux_kernel_load_in_ddr_init_region_fails
  goal_type: linux
  inputs:
    board: "i.MX8M-Mini-EVK"
    console_uart: "UART2"
    stdout_path: "serial0"
    ddr_base: 0x40000000
    ddr_bytes: 0x80000000
    ddr_init_region: {base: 0x40000000, size: 0x00100000}
    kernel_load_addr: 0x40080000     # inside init region
    dtb_load_addr: 0x43000000
  expected:
    feasibility: not_feasible
    validations:
      LOAD_ADDR_CONFLICT: FAIL
      CONSOLE_UART_DEFINED: PASS

- name: rp2040_w5500_xip_bootloader_fits
  goal_type: bootloader
  inputs:
    board: "WIZnet-W5500-EVB-Pico"   # flash_base 0x10000000, 2MiB external QSPI; ram 264KiB
    estimated_image_size: 262144      # 256 KiB app
    vector_table_addr: 0x10000100     # after 256-byte 2nd-stage at XIP base
    bl_region: {base: 0x10000000, size: 0x1000}     # 2nd-stage stub + boot
    app_region: {base: 0x10001000, size: 0x1FF000}
    stack_size: 8192
    heap_size: 16384
    static_size: 32768
  expected:
    feasibility: feasible
    validations:
      FLASH_CAPACITY: PASS
      VECTOR_TABLE_PLACEMENT: PASS    # addr within [0x10000000, 0x10200000)
      BOOTLOADER_APP_NO_OVERLAP: PASS
      RAM_BUDGET: PASS
    risks_contains: []
```

These six exercise: capacity PASS/FAIL, UNKNOWN-as-blocker (the trust-critical case),
A/B symmetry, load-address-vs-memory-map conflict, and the RP2040 **external-QSPI / XIP-window**
boot model (flash_base ≠ on-die) — the highest-value deterministic checks.

---

## 6. Build Order (once this spec is signed off)

Per the architecture-review gate, app code starts only after sign-off. Suggested order, each
step independently testable and eval-backed:

1. `store/` — migrations runner + `0001_init.sql` + connection pragmas.
2. `db seed` + the 5 template YAMLs + 6 seed boards + risk rules + eval cases (data only).
3. **Validation Engine** (§2.3 pure functions) + unit tests from §5 cases.
4. **Risk Engine** (DSL evaluator + seed rules).
5. Orchestrator + fixed response schema (§4.2) + project state CRUD.
6. Typer CLI surface (§4.1).
7. Eval harness (`eval run`) wired to §5 → green before declaring MVP done.
8. LLM Gateway (offline Ollama adapter) + the §4.3 guardrail — **last**, behind `--llm`, off by default.

---

## 7. Sign-off Decisions (locked 2026-06-10)

| # | Question | Decision |
|---|---|---|
| Q1 | Seed board set | **9 boards**: STM32F411RE, ESP32-DevKitC, RPi4 (BCM2711), BBB (AM335x), i.MX8M Mini EVK, STM32MP157, WIZnet W5500-EVB-Pico (RP2040), STM32H743 (Cortex-M7), RTL8722DM (Ameba-D; memory map left NULL/MEDIUM pending datasheet). |
| Q2 | Margins | **flash 10% / RAM 10%** reserve, as constants overridable per project input. |
| Q3 | UNKNOWN handling | **Strict**: any `UNKNOWN` ⇒ `feasibility: blocked`. An unverified hardware fact never reads as feasible. |
| Q4 | Risk DSL scope | **Comparison + one arithmetic term vs a constant** (e.g. `x > board.flash_bytes * 0.9`). No nested expressions in MVP. |
| Q5 | Ollama model | **Deferred to build step 8** (LLM last, off by default). Default pick: `qwen2.5:7b-instruct` for prose; no embeddings in MVP. |

§§1–5 are **signed off**. Build proceeds at §6 step 1.
