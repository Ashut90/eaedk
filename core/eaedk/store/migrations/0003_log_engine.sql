-- V1 Log Analysis Engine (spec §4.8). Deterministic signature DB first, LLM triage fallback.

CREATE TABLE log_signatures (
  id            INTEGER PRIMARY KEY,
  format        TEXT NOT NULL,            -- 'uboot' | 'dmesg' | 'any'
  pattern_regex TEXT NOT NULL,
  cause         TEXT NOT NULL,
  fix           TEXT NOT NULL,
  severity      TEXT NOT NULL CHECK(severity IN ('HIGH','MEDIUM','LOW')),
  source_id     INTEGER REFERENCES sources(id),
  created_at    TEXT NOT NULL
);

CREATE TABLE log_files (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),  -- nullable: a log need not belong to a project
  format     TEXT,
  uri        TEXT,
  hash       TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE log_analyses (
  id             INTEGER PRIMARY KEY,
  log_file_id    INTEGER NOT NULL REFERENCES log_files(id),
  signature_id   INTEGER REFERENCES log_signatures(id),   -- NULL for LLM-triage rows
  llm_hypothesis TEXT,
  confidence     TEXT NOT NULL CHECK(confidence IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
  created_at     TEXT NOT NULL
);
