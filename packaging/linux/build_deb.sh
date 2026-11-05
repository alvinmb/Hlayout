#!/usr/bin/env bash
# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

# Package the PyInstaller onedir build (dist/hlayout/) produced on Linux
# into a .deb. Run from the repo root:
#   packaging/linux/build_deb.sh <version>
# Expects dist/hlayout/ (from `pyinstaller packaging/hlayout.spec`) to
# already exist.
set -euo pipefail

VERSION="${1:?usage: build_deb.sh <version, e.g. 0.1.0>}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG="hlayout_${VERSION}_${ARCH}"
STAGE="${ROOT}/build/deb/${PKG}"

rm -rf "${STAGE}"
mkdir -p "${STAGE}/DEBIAN" \
         "${STAGE}/usr/lib/hlayout" \
         "${STAGE}/usr/bin" \
         "${STAGE}/usr/share/applications" \
         "${STAGE}/usr/share/icons/hicolor/256x256/apps"

# App payload: the whole PyInstaller onedir output.
cp -a "${ROOT}/dist/hlayout/." "${STAGE}/usr/lib/hlayout/"

# Launcher on PATH.
ln -s "/usr/lib/hlayout/hlayout" "${STAGE}/usr/bin/hlayout"

# Desktop entry + icon so it shows up in application menus.
cp "${ROOT}/packaging/linux/hlayout.desktop" "${STAGE}/usr/share/applications/hlayout.desktop"
cp "${ROOT}/packaging/icons/hlayout.png" "${STAGE}/usr/share/icons/hicolor/256x256/apps/hlayout.png"

INSTALLED_SIZE="$(du -sk "${STAGE}/usr" | cut -f1)"

cat > "${STAGE}/DEBIAN/control" <<EOF
Package: hlayout
Version: ${VERSION}
Section: electronics
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Maintainer: Alvin <alvinmb@gmail.com>
Description: IC / PCB layout editor
 HLayout is a GDSII/Gerber-capable IC and PCB layout editor with
 scripting, DRC, and a Tkinter-based GUI.
EOF

dpkg-deb --build --root-owner-group "${STAGE}" "${ROOT}/build/deb/${PKG}.deb"
echo "built: build/deb/${PKG}.deb"
