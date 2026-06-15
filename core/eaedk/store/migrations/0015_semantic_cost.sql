-- 0015: semantic cost estimates (v3.0 P2).
-- A curated, citation-backed lookup mapping high-level protocol/stack/framework names to estimated
-- flash and RAM cost RANGES. This is NOT LLM inference — it is a seeded table the engine reads so a
-- beginner's intent ("I want gRPC") becomes a concrete hardware cost the validation math can use.
CREATE TABLE semantic_cost_estimates (
  term              TEXT PRIMARY KEY,         -- canonical key, e.g. 'grpc', 'tls_mbedtls'
  flash_min_bytes   INTEGER NOT NULL,
  flash_max_bytes   INTEGER NOT NULL,
  ram_min_bytes     INTEGER NOT NULL,
  ram_max_bytes     INTEGER NOT NULL,
  notes             TEXT,                     -- e.g. "mbedTLS minimum config, no cert chain"
  source            TEXT,                     -- datasheet / project / measurement source
  verified_by_human INTEGER NOT NULL DEFAULT 0
);
