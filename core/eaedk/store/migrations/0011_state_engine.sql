-- Engineering State Engine (v2.2.0): progress derived from evidence, never a stored number,
-- never set by the LLM. One row per (project, checklist item) recording how it was proven.
-- Additive; this is project data, not seed data (never cleared by `db seed`).

CREATE TABLE project_progress (
  id               INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES projects(id),
  template_item_id INTEGER NOT NULL REFERENCES template_items(id),
  status           TEXT NOT NULL DEFAULT 'NOT_STARTED'
                     CHECK(status IN ('NOT_STARTED','IN_PROGRESS','COMPLETE')),
  evidence         TEXT,                       -- what proved it (human-readable)
  verified_by      TEXT                        -- VALIDATION_ENGINE | LOG_TRIAGE | USER
                     CHECK(verified_by IN ('VALIDATION_ENGINE','LOG_TRIAGE','USER') OR verified_by IS NULL),
  updated_at       TEXT NOT NULL,
  UNIQUE(project_id, template_item_id)
);
CREATE INDEX idx_project_progress_project ON project_progress(project_id);
