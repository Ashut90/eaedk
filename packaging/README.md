# Packaging EAEDK for Ubuntu / Debian

EAEDK is pure Python with one hard dependency (PyYAML), so it packages cleanly as an
architecture-independent `.deb`. No compilation, no bundled interpreter.

## Build the `.deb`

```bash
packaging/build-deb.sh
# -> dist/eaedk_<version>_all.deb
```

The version comes from `pyproject.toml`. The script needs `dpkg-deb` and `fakeroot`
(both in the `dpkg` / `fakeroot` packages, present on any Ubuntu). An icon is generated
if ImageMagick (`convert`) is available; it is skipped harmlessly otherwise.

## Install

```bash
sudo apt install ./dist/eaedk_0.1.0_all.deb      # resolves dependencies
# or
sudo dpkg -i ./dist/eaedk_0.1.0_all.deb && sudo apt -f install
```

Then, once per user:

```bash
eaedk db init      # create ~/.eaedk/eaedk.db
eaedk db seed      # load the 14 boards, 8 templates, signatures, eval cases
eaedk board list   # confirm it works
```

Uninstall with `sudo apt remove eaedk`. Your project database in `~/.eaedk/` is per-user
and is never touched by install or remove.

## What the package contains and where it goes

| Path | Contents |
|---|---|
| `/usr/bin/eaedk` | launcher — sets `EAEDK_DATA_DIR` + `PYTHONPATH`, runs the CLI |
| `/usr/lib/eaedk/eaedk/` | the Python package (engines, migrations, web UI assets) |
| `/usr/share/eaedk/packages/` | the YAML seed data (boards, templates, rules, signatures) |
| `/usr/share/applications/eaedk.desktop` | app-menu entry that launches the browser UI |
| `/usr/share/doc/eaedk/` | the README and the Complete Technical Guide (offline) |

The launcher relies on EAEDK's built-in `EAEDK_DATA_DIR` override to find the relocated
seed data, so no source change is needed to make the app installable system-wide.

## Dependencies

- **Depends:** `python3 (>= 3.11)`, `python3-yaml` — the full CLI works with just these, offline.
- **Recommends:** `python3-fastapi`, `python3-uvicorn` — the browser UI (`eaedk web`).
- **Suggests:** `python3-fitz` (datasheet ingestion), `gcc-arm-none-eabi`, `openocd`, `ollama`.

Optional features are lazily imported and degrade with a clear message if their package is
absent, so a minimal install is fully usable.

## Other formats

- **pip / pipx (any OS, for developers):** `pip install -e .` from a clone, or
  `pipx install .` for an isolated `eaedk` command. This is the development path; the `.deb`
  is the "install like a normal app" path for Ubuntu.
- **AppImage:** possible but heavier — an AppImage must bundle its own Python interpreter and
  PyYAML, since AppImages do not declare distro dependencies. It is the right choice only if
  you need a single portable file that runs on a machine where you cannot install packages.
  Building one needs `appimagetool` (not required for the `.deb`). The `.deb` is recommended
  for Ubuntu/Debian because it is smaller, integrates with `apt`, and reuses the distro's
  `python3` and `python3-yaml`.
