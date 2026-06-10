-- Datasheet ingestion staging: extracted facts land here as CANDIDATES, never directly in
-- `facts`. An engineer reviews and confirms; only confirmation writes to the knowledge base.

CREATE TABLE fact_candidates (
  id          INTEGER PRIMARY KEY,
  board_id    INTEGER NOT NULL REFERENCES boards(id),
  source_id   INTEGER REFERENCES sources(id),     -- the datasheet (one per ingest run)
  domain      TEXT NOT NULL,                       -- MEMORY | CLOCK | TIMING | ...
  kind        TEXT,                                -- fine classifier (memmap, clock, ...)
  fact_key    TEXT NOT NULL,
  fact_value  TEXT NOT NULL,
  method      TEXT NOT NULL CHECK(method IN ('table','text','llm')),
  confidence  TEXT NOT NULL CHECK(confidence IN ('HIGH','MEDIUM','LOW')),
  page        INTEGER,
  section     TEXT,
  snippet     TEXT,
  status      TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','confirmed','rejected')),
  created_at  TEXT NOT NULL
);
