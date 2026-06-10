-- Evolve the existing `facts` table into the canonical engineering-fact layer.
-- Additive only: typed board identity stays in `boards`; structured provenance stays in
-- `sources`/`citations`. We add a `domain` dimension and a denormalized `source_type`
-- (kept consistent by the record_fact() write-through), and expose a read VIEW.

ALTER TABLE facts ADD COLUMN domain TEXT;            -- MEMORY | PINMUX | CLOCK | POWER | ...
ALTER TABLE facts ADD COLUMN source_type TEXT
  CHECK(source_type IN ('USER_INPUT','DATASHEET','TRM','SDK_DOC','SCHEMATIC'));

-- Backfill existing rows (partition facts written by the onboarding wizard) so the unified
-- layer is consistent from day one.
UPDATE facts SET domain = 'MEMORY', source_type = 'USER_INPUT'
  WHERE domain IS NULL AND kind IN ('partition','memmap','register','timing');
UPDATE facts SET domain = 'CLOCK', source_type = 'USER_INPUT'
  WHERE domain IS NULL AND kind = 'clock';
UPDATE facts SET domain = 'PINMUX', source_type = 'USER_INPUT'
  WHERE domain IS NULL AND kind = 'pinmux';

-- The unified READ interface. Provenance is joined in (page/section/snippet preserved),
-- never flattened. This is what postfilter and future consumers query.
CREATE VIEW engineering_facts AS
SELECT
  f.id                AS id,
  f.board_id          AS board_id,
  f.domain            AS domain,
  f.source_type       AS source_type,
  f.kind              AS kind,
  f.key               AS fact_key,
  f.value             AS fact_value,
  f.confidence        AS confidence,
  f.verified_by_human AS verified_by_human,
  f.created_at        AS created_at,
  f.citation_id       AS citation_id,
  c.page              AS citation_page,
  c.section           AS citation_detail,
  c.snippet           AS citation_snippet,
  s.type              AS source_doc_type,
  s.title             AS source_title,
  s.uri               AS source_uri
FROM facts f
LEFT JOIN citations c ON c.id = f.citation_id
LEFT JOIN sources   s ON s.id = c.source_id;
