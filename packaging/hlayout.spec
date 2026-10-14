# PyInstaller spec for HLayout, built per-OS in CI (see
# .github/workflows/build-installers.yml) with:
#   pyinstaller packaging/hlayout.spec
#
# Produces a onedir build at dist/hlayout/ on every platform, plus (on
# macOS only) a dist/HLayout.app bundle. The Windows/Linux packaging
# steps package dist/hlayout/ directly; the macOS step packages
# dist/HLayout.app into a .dmg.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# Set by CI from the git tag (see .github/workflows/build-installers.yml);
# defaults to a placeholder for local/manual builds.
APP_VERSION = os.environ.get("HLAYOUT_VERSION", "0.0.0")

block_cipher = None
# SPECPATH is injected into the exec() namespace by PyInstaller itself
# (this file has no reliable __file__ once PyInstaller execs it).
ROOT = Path(SPECPATH).resolve().parent

datas = [
    # show_splash() (hlayout/gui/splash.py) looks for its image next to
    # itself, at hlayout/gui/assets/splash.png relative to the module's
    # own __file__ -- inside a frozen build that resolves under
    # _internal/hlayout/gui/assets/, so the bundled file has to land at
    # that same package-relative path, not the top-level
    # hlayout_splash.png (an unrelated, unused file at the repo root).
    # A missing splash image fails silently by design, which is exactly
    # why this was easy to ship wrong without any error to catch it.
    (str(ROOT / "hlayout" / "gui" / "assets" / "splash.png"), "hlayout/gui/assets"),
    # Help > User Manual opens docs/HLayout_User_Manual.pdf found next to
    # the hlayout package (App.user_manual_path()) -- in a frozen build
    # that's _internal/, so the PDF lands at _internal/docs/.
    (str(ROOT / "docs" / "HLayout_User_Manual.pdf"), "docs"),
]
binaries = []
hiddenimports = []

# gdstk and shapely both ship compiled extensions / bundled shared libs
# that PyInstaller's default import scan can miss -- pull in everything
# their own hooks know about explicitly rather than debugging a
# works-on-my-machine "DLL not found" at run time on a clean box.
def _not_tests(path_pair):
    # path_pair is (source_path, dest_dir); drop anything whose dest
    # sits under a "tests" package directory.
    return "tests" not in Path(path_pair[1]).parts


for pkg in ("gdstk", "shapely", "scipy"):
    d, b, h = collect_all(pkg)
    # collect_all() pulls in every submodule/data file it can find,
    # including each package's own bundled unit-test suite (at least
    # shapely.tests.* runs to hundreds of modules) -- harmless to
    # import but pure bloat in a shipped build, so drop "tests".
    # shapely.plotting is dropped too: HLayout does its own Tkinter
    # canvas rendering and never imports it, but it does a top-level
    # `import matplotlib.pyplot`, and matplotlib's own PyInstaller hook
    # then bundles *every* GUI backend importable in the build
    # environment -- if PyQt5/PySide happens to be installed on that
    # machine for some unrelated project, that's a multi-hundred-MB Qt5
    # binary drop into an app that never touches Qt or matplotlib.
    #
    # scipy is here explicitly (not left to PyInstaller's own bundled
    # scipy hook) because that hook doesn't yet know about every
    # submodule scipy's newer array-API compatibility shim touches --
    # trim_sim.py's `from scipy.sparse.linalg import spsolve` reaches
    # scipy._lib._array_api -> scipy._external.array_api_compat, and
    # the hook missed scipy._external.array_api_compat.numpy.fft
    # specifically, which crashed the Trim Simulator (and anything
    # else importing scipy.sparse.linalg) with a plain
    # ModuleNotFoundError at runtime, on a clean machine only -- it
    # can't show up in a dev environment where scipy is installed
    # normally, only in the frozen build. collect_all() bundles every
    # submodule it can find by scanning the installed package on disk,
    # not just what static analysis can trace through import
    # statements, so it isn't blind to this the way the hook is.
    datas += [p for p in d if _not_tests(p)]
    binaries += b
    hiddenimports += [m for m in h if "tests" not in m.split(".") and m != "shapely.plotting"]

a = Analysis(
    [str(ROOT / "hlayout_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    # Belt-and-suspenders alongside dropping shapely.plotting above:
    # explicitly refuse matplotlib and every Qt binding it might drag
    # in, so a future stray import of any of these fails the build
    # loudly instead of silently ballooning it again.
    excludes=["matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == "win32":
    icon_file = str(ROOT / "packaging" / "icons" / "hlayout.ico")
elif sys.platform == "darwin":
    icon_file = str(ROOT / "packaging" / "icons" / "hlayout.icns")
else:
    icon_file = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hlayout",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="hlayout",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="HLayout.app",
        icon=icon_file,
        bundle_identifier="com.alvinmb.hlayout",
        info_plist={
            "CFBundleName": "HLayout",
            "CFBundleDisplayName": "HLayout",
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
