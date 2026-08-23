#!/usr/bin/env python3
"""Launch the HLayout GUI.

Usage:
    python hlayout_app.py             # start with a blank library
    python hlayout_app.py mydesign.gds  # open an existing GDSII file
                                         # (its .hlaux sidecar, if any,
                                         # is restored too -- see
                                         # hlayout/io/sidecar.py)
"""
import sys

from hlayout.core.errors import LayoutError
from hlayout.gui.app import App
from hlayout.gui.splash import show_splash
from hlayout.io.sidecar import load_design


def _set_windows_dpi_awareness():
    """Mark this process "per-monitor DPI aware" on Windows, before any Tk
    window is created.

    Without this, an unaware process is bitmap-scaled by Windows: top-level
    widgets still work because Tk positions them (and the OS repositions
    the whole window) in the same scaled space, but *child* windows that
    Tk creates via low-level Win32 calls -- notably the real child HWNDs a
    tk.Canvas creates for widgets embedded with create_window(), which is
    exactly how the scrollable Layers panel embeds its checkboxes -- are
    placed by Tk using logical coordinates that no longer line up with the
    physical mouse coordinates Windows reports once the OS is doing the
    scaling. The visible symptom is precisely what was reported: clicks
    that land squarely on a layer's visibility checkbox (or the All On/
    All Off buttons, also canvas-embedded) appear to do nothing, while
    ordinary top-level buttons and other widgets keep working normally.
    Declaring DPI awareness up front tells Windows not to scale the
    process at all, so Tk's own coordinates and the OS's physical mouse
    coordinates agree everywhere, including for canvas-embedded windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            # Windows 10 1703+: per-monitor v2, the most accurate option.
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
            return
        except (AttributeError, OSError):
            pass
        try:
            # Windows 8.1+: per-monitor v1.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except (AttributeError, OSError):
            pass
        try:
            # Vista+: system DPI aware (better than nothing).
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except Exception:
        # Never let a DPI-awareness quirk on some odd Windows configuration
        # stop the app from starting at all.
        pass


def main():
    _set_windows_dpi_awareness()
    show_splash()  # ~5s branded splash, shown once before the main window
    library = None
    attached_libraries = None
    attached_paths = None
    if len(sys.argv) > 1:
        try:
            loaded = load_design(sys.argv[1])
        except LayoutError as e:
            # A clean, one-line message instead of a raw traceback (and
            # gdstk's own "[GDSTK] Unable to open GDSII file..." stderr
            # noise) when the path is wrong or the file doesn't exist.
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        library = loaded.library
        attached_libraries = loaded.attached_libraries
        attached_paths = loaded.attached_paths
        for msg in loaded.attach_errors:
            print(f"warning: attached library failed to restore: {msg}", file=sys.stderr)
    app = App(library, attached_libraries=attached_libraries, attached_paths=attached_paths)
    if len(sys.argv) > 1:
        app.current_path = sys.argv[1]
    app.mainloop()


if __name__ == "__main__":
    main()
