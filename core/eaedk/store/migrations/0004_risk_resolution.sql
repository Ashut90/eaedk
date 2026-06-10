-- Risk resolution: let an engineer close a tracked risk with a timestamp + note.
-- Additive. Tracked risks (status='tracked') become status='resolved' when closed.

ALTER TABLE risks ADD COLUMN resolved_at TEXT;
ALTER TABLE risks ADD COLUMN resolution_note TEXT;
