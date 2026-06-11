-- Last-mile (v1.7.0): the real flash command. A small, seedable map from debug probe -> OpenOCD
-- interface cfg, and from SoC -> OpenOCD target cfg + the probe a beginner most likely owns.
-- Lets FLASH.md emit a filled-in command for common setups (Blue Pill + ST-Link) instead of
-- <probe>/<target> placeholders.

CREATE TABLE debug_probes (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  interface_cfg TEXT NOT NULL,        -- OpenOCD interface/*.cfg path
  summary       TEXT
);

CREATE TABLE soc_flash_profiles (
  id             INTEGER PRIMARY KEY,
  soc_name       TEXT NOT NULL UNIQUE,
  openocd_target TEXT NOT NULL,       -- OpenOCD target/*.cfg path
  default_probe  TEXT                 -- name in debug_probes the beginner most likely has
);
