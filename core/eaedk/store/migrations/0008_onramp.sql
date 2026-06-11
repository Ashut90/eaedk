-- On-ramp (v1.6.0): standard geometry per SoC, so a board with a recognized SoC but missing
-- geometry can be filled without a datasheet.

CREATE TABLE soc_defaults (
  id          INTEGER PRIMARY KEY,
  soc_name    TEXT NOT NULL UNIQUE,
  flash_base  INTEGER,
  flash_bytes INTEGER,
  ram_base    INTEGER,
  ram_bytes   INTEGER
);
