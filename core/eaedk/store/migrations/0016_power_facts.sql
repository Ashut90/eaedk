-- 0016: power/electrical board facts (v3.0 P3). Additive; NULL = UNCONFIRMED.
-- Typical active current, deep-sleep current, and minimum operating voltage from the datasheet —
-- the inputs the POWER_BUDGET hazard rules check a project's power intent against.

ALTER TABLE boards ADD COLUMN active_current_ma INTEGER;   -- typical active/run current (mA)
ALTER TABLE boards ADD COLUMN sleep_current_ua  INTEGER;   -- deep-sleep / stop current (µA)
ALTER TABLE boards ADD COLUMN min_voltage_v     REAL;      -- minimum operating voltage (V)
