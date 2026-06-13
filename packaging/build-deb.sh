#!/usr/bin/env bash
# Build a native Ubuntu/Debian package (.deb) for EAEDK.
#
# EAEDK is pure Python with one hard dependency (PyYAML = the distro's python3-yaml), so the
# package is Architecture: all and needs no compilation. The app code is installed privately
# under /usr/lib/eaedk, the seed data under /usr/share/eaedk, and a launcher at /usr/bin/eaedk
# points the code at the data via EAEDK_DATA_DIR (the override hook the app already supports).
#
# Usage:   packaging/build-deb.sh           # -> dist/eaedk_<version>_all.deb
# Install: sudo apt install ./dist/eaedk_<version>_all.deb     (or: sudo dpkg -i ...)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Version from pyproject.toml (single source of truth).
VERSION="$(grep -m1 '^version' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
PKG="eaedk"
ARCH="all"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Building ${PKG} ${VERSION} (${ARCH})"

# --- filesystem layout -----------------------------------------------------
install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/lib/eaedk"
install -d "$STAGE/usr/share/eaedk"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/icons/hicolor/256x256/apps"
install -d "$STAGE/usr/share/doc/eaedk"

# App package (exclude bytecode). Migrations and web/static ship inside the package already.
cp -r core/eaedk "$STAGE/usr/lib/eaedk/eaedk"
find "$STAGE/usr/lib/eaedk" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE/usr/lib/eaedk" -name '*.pyc' -delete

# Seed/template data (the only repo-root-relative data the app reads).
cp -r packages "$STAGE/usr/share/eaedk/packages"

# Launcher: relocate data + put the private app dir on the path, then run the CLI.
cat > "$STAGE/usr/bin/eaedk" <<'LAUNCH'
#!/bin/sh
# EAEDK launcher (installed by the .deb). The app code lives under /usr/lib/eaedk and the
# seed data under /usr/share/eaedk; EAEDK_DATA_DIR points the code at the latter.
export EAEDK_DATA_DIR="${EAEDK_DATA_DIR:-/usr/share/eaedk/packages}"
export PYTHONPATH="/usr/lib/eaedk${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m eaedk.cli "$@"
LAUNCH
chmod 0755 "$STAGE/usr/bin/eaedk"

# Desktop entry (launches the browser UI in a terminal; degrades with a clear hint).
cat > "$STAGE/usr/share/applications/eaedk.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=EAEDK Web UI
GenericName=Embedded AI Engineering Development Kit
Comment=Validate embedded bring-up locally; the engines hold the truth
Exec=sh -c "eaedk web || { echo; echo 'The Web UI needs FastAPI + uvicorn:'; echo '  sudo apt install python3-fastapi python3-uvicorn'; echo; echo 'The full CLI works without them: run  eaedk --help'; echo 'Press Enter to close.'; read _; }"
Terminal=true
Categories=Development;Engineering;
Keywords=embedded;firmware;validation;STM32;bringup;
Icon=eaedk
DESK

# Icon (best-effort: a simple branded square if ImageMagick is present; harmless if not).
if command -v convert >/dev/null 2>&1; then
  convert -size 256x256 xc:'#0d2137' \
    -fill '#eaf2fb' -gravity center -font DejaVu-Sans-Bold -pointsize 64 -annotate 0 'EAEDK' \
    "$STAGE/usr/share/icons/hicolor/256x256/apps/eaedk.png" 2>/dev/null || true
fi

# Docs.
cp -f README.md "$STAGE/usr/share/doc/eaedk/README.md" 2>/dev/null || true
cp -f docs/EAEDK-Complete-Guide.pdf "$STAGE/usr/share/doc/eaedk/" 2>/dev/null || true

cat > "$STAGE/usr/share/doc/eaedk/copyright" <<'CR'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: eaedk
Source: https://github.com/Ashut90/eaedk

Files: *
License: MIT
CR

# --- control metadata ------------------------------------------------------
INSTALLED_KB="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CTRL
Package: ${PKG}
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.11), python3-yaml
Recommends: python3-fastapi, python3-uvicorn
Suggests: python3-fitz, gcc-arm-none-eabi, openocd, ollama
Installed-Size: ${INSTALLED_KB}
Maintainer: Ashutosh <lucifer.chen18@gmail.com>
Homepage: https://github.com/Ashut90/eaedk
Description: Embedded AI Engineering Development Kit (local-first, offline)
 EAEDK takes a firmware engineer from "I have a board, or a datasheet" to a
 validated, buildable project, without an AI ever asserting a hardware fact the
 database has not verified. Deterministic engines (22 validation rules, a risk
 DSL, signature-based log triage, a feasibility-gated exporter) hold the truth;
 an optional local language model may only explain, and a post-filter strips any
 uncited hardware value before it reaches you.
 .
 The command-line tool runs on python3 + python3-yaml alone and works offline.
 The optional browser UI (python3-fastapi, python3-uvicorn), datasheet ingestion
 (python3-fitz) and local model (ollama) are pulled in only if you want them.
CTRL

# postinst / postrm: refresh the desktop + icon caches; never touch user data.
cat > "$STAGE/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
  command -v gtk-update-icon-cache  >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
exit 0
POST
chmod 0755 "$STAGE/DEBIAN/postinst"

# --- build -----------------------------------------------------------------
install -d "$ROOT/dist"
DEB="$ROOT/dist/${PKG}_${VERSION}_${ARCH}.deb"
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
echo "wrote $DEB"
dpkg-deb --info "$DEB" | sed -n '1,20p'
