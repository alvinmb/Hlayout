#!/usr/bin/env bash
# Local Linux build: freezes HLayout with PyInstaller and packages it
# as a .deb.
#
# Run from the repo root (or anywhere -- it cd's to the repo root
# itself):
#   packaging/linux/build.sh
#   packaging/linux/build.sh 0.2.0
#
# Needs python3-tk and dpkg-dev; on Debian/Ubuntu:
#   sudo apt install python3-tk dpkg-dev
#
# (CI runs the same steps on ubuntu-latest for every tagged release --
# see .github/workflows/build-installers.yml -- this script is for
# building/testing a copy locally without pushing a tag.)
set -euo pipefail

VERSION="${1:-0.0.0-local}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "==> Checking for python3-tk"
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "error: tkinter isn't available for this python3 -- install it first:" >&2
    echo "    sudo apt install python3-tk" >&2
    exit 1
fi

echo "==> Installing/upgrading build dependencies"
python3 -m pip install --break-system-packages -r requirements.txt pyinstaller

echo "==> Removing old build/dist"
rm -rf build dist

echo "==> Freezing with PyInstaller (version ${VERSION})"
HLAYOUT_VERSION="${VERSION}" python3 -m PyInstaller packaging/hlayout.spec

echo "==> Onedir build ready: dist/hlayout/hlayout"

# Sanity check: fail loudly (instead of shipping a bloated build) if
# matplotlib or a Qt binding snuck back in -- see packaging/hlayout.spec
# for why that can happen (shapely.plotting pulls matplotlib, whose own
# PyInstaller hook then bundles whatever Qt binding is on this machine).
stray="$(find dist/hlayout -iname '*qt5*' -o -iname '*qt6*' -o -iname '*matplotlib*' 2>/dev/null || true)"
if [[ -n "${stray}" ]]; then
    echo "warning: found unexpected Qt/matplotlib files in the build --" >&2
    echo "packaging/hlayout.spec's excludes list may need updating:" >&2
    echo "${stray}" >&2
fi

if command -v dpkg-deb >/dev/null 2>&1; then
    echo "==> Packaging .deb"
    packaging/linux/build_deb.sh "${VERSION}"
else
    echo "==> dpkg-deb not found -- skipping the .deb step (sudo apt install dpkg-dev)."
    echo "    You can still run dist/hlayout/hlayout directly."
fi
