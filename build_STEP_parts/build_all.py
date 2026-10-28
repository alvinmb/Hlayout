"""Build every HLayout STEP model in one go and put them all in one folder.

Runs every FreeCAD macro in this folder (build_*.FCMacro) with FreeCAD's
command-line FreeCADCmd, and every CadQuery script (build_*.py, except
this one) with Python. Each one writes its STEP files into its own
sub-folder of the output folder (default C:\\step_models):

    C:\\step_models\\ceramic_chip_caps\\..., chip_wire\\..., kovar_dip\\..., lcc\\...
    C:\\step_models\\*.step        <- build_component_library.FCMacro

The macros leave a STEP file alone when it already exists; --clean
deletes the old STEP files first so everything is rebuilt.

Usage:
    python build_STEP_parts\\build_all.py
    python build_STEP_parts\\build_all.py --out D:\\models --clean
    python build_STEP_parts\\build_all.py --only lcc kovar_dip
    python build_STEP_parts\\build_all.py --freecad "C:\\Program Files\\FreeCAD 1.0\\bin\\FreeCADCmd.exe"

Needs FreeCAD for the macros and the cadquery package (pip install
cadquery) for the CadQuery scripts; a step that can't run is reported
and the rest still run.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = r"C:\step_models" if os.name == "nt" else os.path.expanduser("~/step_models")


def find_freecadcmd(given=None):
    """The FreeCADCmd executable: --freecad, then PATH, then the usual
    Windows install folders (newest first)."""
    if given:
        return given if os.path.isfile(given) else None
    for name in ("FreeCADCmd", "freecadcmd", "FreeCADCmd.exe"):
        p = shutil.which(name)
        if p:
            return p
    pats = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        pats.append(os.path.join(base, "FreeCAD*", "bin", "FreeCADCmd.exe"))
        pats.append(os.path.join(base, "FreeCAD*", "bin", "freecadcmd.exe"))
    hits = sorted({h for p in pats for h in glob.glob(p)}, reverse=True)
    return hits[0] if hits else None


def steps(only=None):
    """[(name, kind, path)] -- kind "macro" or "cadquery", in name order."""
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "build_*.FCMacro"))):
        out.append((os.path.basename(p)[6:-8], "macro", p))
    for p in sorted(glob.glob(os.path.join(HERE, "build_*.py"))):
        if os.path.abspath(p) != os.path.abspath(__file__):
            out.append((os.path.basename(p)[6:-3], "cadquery", p))
    if only:
        out = [s for s in out if s[0] in only]
    return out


def step_count(folder):
    return len(glob.glob(os.path.join(folder, "**", "*.step"), recursive=True))


def run(name, kind, path, out, freecad, timeout):
    env = dict(os.environ, HLAYOUT_STEP_DIR=out)
    if kind == "macro":
        if not freecad:
            return False, "FreeCADCmd not found -- install FreeCAD or pass --freecad"
        cmd = [freecad, path]
    else:
        try:
            import cadquery  # noqa: F401
        except ImportError:
            return False, "cadquery not installed -- pip install cadquery"
        cmd = [sys.executable, path, os.path.join(out, name)]
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s"
    text = (r.stdout or "") + (r.stderr or "")
    # FreeCADCmd reports a failing macro but still exits 0
    bad = r.returncode != 0 or "Traceback (most recent call last)" in text \
        or "Exception while processing file" in text
    tail = "\n".join(l for l in text.strip().splitlines()[-6:])
    return not bad, tail


def main():
    ap = argparse.ArgumentParser(description="Build every HLayout STEP model into one folder.")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    ap.add_argument("--clean", action="store_true",
                    help="delete the STEP files already in the output folder first")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="just these, e.g. lcc kovar_dip (names as in build_<name>.*)")
    ap.add_argument("--freecad", help="path to FreeCADCmd(.exe)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per script (default 900)")
    ap.add_argument("--list", action="store_true", help="list the scripts and stop")
    a = ap.parse_args()

    todo = steps(a.only)
    if a.list or not todo:
        for name, kind, path in todo:
            print(f"{name:20s} {kind:9s} {os.path.relpath(path, ROOT)}")
        if not todo:
            print("nothing to build" + (f" matching {a.only}" if a.only else ""))
        return 0 if todo else 1

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    if a.clean:
        old = glob.glob(os.path.join(out, "**", "*.step"), recursive=True)
        for p in old:
            os.remove(p)
        print(f"removed {len(old)} old STEP files from {out}")
    freecad = find_freecadcmd(a.freecad)
    print(f"output   {out}")
    print(f"FreeCAD  {freecad or 'not found'}")
    print()

    failed = []
    for name, kind, path in todo:
        before = step_count(out)
        t0 = time.time()
        print(f"{name} ({kind}) ...", flush=True)
        ok, tail = run(name, kind, path, out, freecad, a.timeout)
        made = step_count(out) - before
        print(f"  {'ok' if ok else 'FAILED'}  {made} new STEP files, {time.time() - t0:.1f} s")
        if not ok:
            failed.append(name)
            for line in tail.splitlines():
                print(f"    {line}")
    print()
    print(f"{step_count(out)} STEP files in {out}")
    if failed:
        print(f"failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
