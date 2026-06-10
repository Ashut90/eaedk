-- Toolchain Engine: make the build environment a first-class validated entity.
-- `toolchain_components` = what `toolchain detect` found on this host (replaced each detect).
-- `board_toolchain_reqs` = the per-board required toolchain profile (seeded from board YAML).

CREATE TABLE toolchain_components (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,          -- compiler | debugger | flash_tool | build_system | sdk
  name          TEXT NOT NULL,          -- e.g. arm-none-eabi-gcc, openocd, cmake
  version       TEXT,                   -- detected version string (NULL if unparsable)
  target_triple TEXT,                   -- compilers only: e.g. arm-none-eabi (from -dumpmachine)
  path          TEXT,                   -- resolved binary path
  raw           TEXT,                   -- snippet of --version output
  detected_at   TEXT NOT NULL
);

CREATE TABLE board_toolchain_reqs (
  id            INTEGER PRIMARY KEY,
  board_id      INTEGER NOT NULL REFERENCES boards(id),
  kind          TEXT NOT NULL,          -- compiler | debugger | flash_tool | build_system | sdk
  name          TEXT NOT NULL,          -- required tool name
  target_triple TEXT,                   -- compiler: expected triple (ISA must match board arch)
  min_version   TEXT,                   -- optional minimum version
  severity      TEXT NOT NULL CHECK(severity IN ('HIGH','MEDIUM','LOW')),
  why           TEXT                    -- teach-layer: why it matters / what to do
);
