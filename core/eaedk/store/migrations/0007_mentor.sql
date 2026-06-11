-- Mentor layer (v1.5.0): plain-language capability descriptions, an ordered learning path,
-- and concept anchors. All seeded from YAML (not hardcoded, not LLM-generated).

CREATE TABLE capabilities (
  id      INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,         -- matches board_capabilities.capability
  summary TEXT NOT NULL                 -- one plain-language sentence
);

CREATE TABLE learning_steps (
  id                    INTEGER PRIMARY KEY,
  step                  INTEGER NOT NULL,         -- order in the path
  key                   TEXT NOT NULL UNIQUE,
  title                 TEXT NOT NULL,
  goal_type             TEXT,                     -- maps to a template where one exists
  requires_json         TEXT NOT NULL DEFAULT '[]',   -- capabilities the board must have
  why                   TEXT NOT NULL,            -- why this comes before the next
  before_you_start_json TEXT NOT NULL DEFAULT '[]'    -- plain checklist
);

CREATE TABLE concepts (
  id     INTEGER PRIMARY KEY,
  name   TEXT NOT NULL UNIQUE,          -- lowercased concept key, e.g. 'hardfault'
  anchor TEXT NOT NULL                  -- one factual sentence (deterministic backbone)
);
