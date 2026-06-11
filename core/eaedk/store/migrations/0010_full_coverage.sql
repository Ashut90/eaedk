-- Full coverage (v1.8.0): beginner first-mistakes catalogue (per chip family) and the
-- between-projects "what's next" cross-link. Both seeded from diffable YAML; additive only.

CREATE TABLE first_mistakes (
  id       INTEGER PRIMARY KEY,
  family   TEXT NOT NULL,          -- chip family key: stm32 | rp2040 | esp32 | avr
  mistake  TEXT NOT NULL,
  fix      TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'HIGH'
);
CREATE INDEX idx_first_mistakes_family ON first_mistakes(family);

CREATE TABLE learning_step_intro (
  id         INTEGER PRIMARY KEY,
  step_key   TEXT NOT NULL UNIQUE,  -- references a learning_steps.key (soft link, seed-managed)
  introduces TEXT NOT NULL,         -- the new thing this project teaches
  concept    TEXT                   -- a concepts.name to cross-link for the deeper explanation
);
