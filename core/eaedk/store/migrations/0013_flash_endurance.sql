-- 0013: FLASH endurance facts + rule metadata (v2.7 P2.5)
-- Additive only. Boards gain a per-board flash erase-cycle rating (NULL = UNCONFIRMED).
-- Risk rules gain a requires-gate and an on-unknown severity so a rule can fire a
-- lower-confidence warning when a board fact is unconfirmed instead of silently dropping.

ALTER TABLE boards ADD COLUMN flash_endurance_cycles INTEGER;

ALTER TABLE risk_rules ADD COLUMN requires_json TEXT;
ALTER TABLE risk_rules ADD COLUMN severity_on_unknown TEXT;
