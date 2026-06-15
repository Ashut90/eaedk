-- 0014: per-project beginner facts on the learning path (v3.0 P1).
-- Additive: each step gains the peripherals it exercises, the failure mode to expect, how to
-- diagnose it, what it proves to an interviewer, and the explicit dependency on the prior step.
-- All NULL/'[]' defaults so the driver path and any un-annotated step keep working unchanged.

ALTER TABLE learning_steps ADD COLUMN peripherals_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE learning_steps ADD COLUMN failure_mode TEXT;
ALTER TABLE learning_steps ADD COLUMN diagnose TEXT;
ALTER TABLE learning_steps ADD COLUMN proves TEXT;
ALTER TABLE learning_steps ADD COLUMN builds_on TEXT;
