-- EAEDK MVP schema (spec §1). Forward-only. Deferred-to-V1 tables are intentionally absent.

-- 1.1 Provenance ------------------------------------------------------------
CREATE TABLE sources (
  id          INTEGER PRIMARY KEY,
  type        TEXT NOT NULL CHECK(type IN
                ('datasheet','trm','sdk_doc','errata','manual','web','seed','user')),
  title       TEXT NOT NULL,
  uri         TEXT,
  hash        TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE citations (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  page        INTEGER,
  section     TEXT,
  bbox_json   TEXT,
  snippet     TEXT
);

-- 1.2 Board knowledge -------------------------------------------------------
CREATE TABLE socs (
  id      INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,
  vendor  TEXT,
  arch    TEXT NOT NULL,
  notes   TEXT
);

CREATE TABLE boards (
  id              INTEGER PRIMARY KEY,
  soc_id          INTEGER NOT NULL REFERENCES socs(id),
  name            TEXT NOT NULL UNIQUE,
  flash_base      INTEGER,
  flash_bytes     INTEGER,
  ram_base        INTEGER,
  ram_bytes       INTEGER,
  ddr_type        TEXT,
  ddr_bytes       INTEGER,
  primary_storage TEXT,
  boot_modes_json TEXT,
  source_id       INTEGER REFERENCES sources(id),
  confidence      TEXT NOT NULL DEFAULT 'HIGH'
);

CREATE TABLE board_capabilities (
  id           INTEGER PRIMARY KEY,
  board_id     INTEGER NOT NULL REFERENCES boards(id),
  capability   TEXT NOT NULL,
  details_json TEXT
);

-- 1.3 Facts ----------------------------------------------------------------
CREATE TABLE facts (
  id                INTEGER PRIMARY KEY,
  board_id          INTEGER NOT NULL REFERENCES boards(id),
  kind              TEXT NOT NULL CHECK(kind IN
                      ('register','memmap','clock','pinmux','timing','partition')),
  key               TEXT NOT NULL,
  value             TEXT NOT NULL,
  citation_id       INTEGER REFERENCES citations(id),
  confidence        TEXT NOT NULL,
  verified_by_human INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL
);

-- 1.4 Templates ------------------------------------------------------------
CREATE TABLE templates (
  id        INTEGER PRIMARY KEY,
  key       TEXT NOT NULL,
  name      TEXT NOT NULL,
  version   INTEGER NOT NULL,
  goal_type TEXT NOT NULL,
  active    INTEGER NOT NULL DEFAULT 1,
  UNIQUE(key, version)
);

CREATE TABLE template_items (
  id                        INTEGER PRIMARY KEY,
  template_id               INTEGER NOT NULL REFERENCES templates(id),
  item_key                  TEXT NOT NULL,
  text                      TEXT NOT NULL,
  category                  TEXT NOT NULL,
  required_inputs_json      TEXT NOT NULL,
  validation_rule_keys_json TEXT NOT NULL,
  ordinal                   INTEGER NOT NULL,
  UNIQUE(template_id, item_key)
);

-- 1.5 Projects -------------------------------------------------------------
CREATE TABLE projects (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  board_id   INTEGER REFERENCES boards(id),
  goal_type  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active',
  template_id INTEGER REFERENCES templates(id),  -- pinned version (spec §3.3)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE project_inputs (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  source      TEXT NOT NULL CHECK(source IN ('user','extracted','seed')),
  citation_id INTEGER REFERENCES citations(id),
  confidence  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE(project_id, key)
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

CREATE TABLE decisions (
  id                INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id),
  title             TEXT NOT NULL,
  rationale         TEXT,
  alternatives_json TEXT,
  made_at           TEXT NOT NULL
);

CREATE TABLE risks (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  rule_key    TEXT NOT NULL,
  severity    TEXT NOT NULL CHECK(severity IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
  explanation TEXT NOT NULL,
  mitigation  TEXT,
  citation_id INTEGER REFERENCES citations(id),
  status      TEXT NOT NULL DEFAULT 'open',
  created_at  TEXT NOT NULL
);

-- 1.6 Risk rules (data-driven) ---------------------------------------------
CREATE TABLE risk_rules (
  id               INTEGER PRIMARY KEY,
  key              TEXT NOT NULL UNIQUE,
  goal_type        TEXT,
  condition_dsl    TEXT NOT NULL,
  severity         TEXT NOT NULL,
  explanation_tmpl TEXT NOT NULL,
  mitigation_tmpl  TEXT
);

-- 1.7 Eval -----------------------------------------------------------------
CREATE TABLE eval_cases (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  goal_type     TEXT NOT NULL,
  inputs_json   TEXT NOT NULL,
  expected_json TEXT NOT NULL
);

CREATE TABLE eval_runs (
  id        INTEGER PRIMARY KEY,
  case_id   INTEGER NOT NULL REFERENCES eval_cases(id),
  passed    INTEGER NOT NULL,
  diff_json TEXT,
  run_at    TEXT NOT NULL
);
