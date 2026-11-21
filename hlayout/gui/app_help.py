# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Help menu: About, the user manual and the command reference.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import os
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import dialogs
from .. import __version__
from .commands import COMMAND_REFERENCE


def open_with_default_app(path):
    """Open ``path`` with whatever the OS has registered for it (a PDF
    viewer, Word, a browser, ...): os.startfile on Windows, ``open`` on
    macOS, ``xdg-open`` elsewhere. Raises on failure."""
    import subprocess
    import sys
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    else:
        subprocess.Popen(["xdg-open", path])


class HelpMixin:
    def show_about(self):
        dialogs.show_info(
            "About HLayout",
            "HLayout\n"
            "Thick-Film Hybrid Layout CAD Tool\n\n"
            "A layout editor for thick-film hybrid microelectronics: "
            "64 layers, GDSII I/O, mouse and command-bar driven drawing/editing, "
            "attached reference libraries, and a full Python scripting API.\n\n"
            f"Version {__version__}\n\n"
            "Copyright (C) Alvin Brown\n"
            "Free and open-source software, licensed under the GNU General "
            "Public License v3.0 or later. HLayout comes with ABSOLUTELY NO "
            "WARRANTY; you may redistribute it under the terms of that licence.\n\n"
            "Source code and licence: https://github.com/alvinmb/Hlayout",
            parent=self,
        )

    def show_user_manual(self):
        """Help > User Manual: open the user manual PDF
        (docs/HLayout_User_Manual.pdf) -- a walkthrough of every menu,
        dialog and built-in tool, organized by what you're trying to do
        -- in the system's PDF viewer. Falls back to the Word original
        (docs/HLayout_User_Manual.docx) when the PDF isn't there (e.g. a
        source checkout where it hasn't been exported yet).
        Complements Help > Command Reference / docs/help.html, the
        exhaustive command-bar/Python API reference. Shows an info
        dialog if neither file can be found, and an error dialog if the
        OS can't open it."""
        manual_path = self.user_manual_path()
        if manual_path is None:
            dialogs.show_info(
                "User Manual",
                "Couldn't find the user manual (HLayout_User_Manual.pdf) -- "
                "it may be missing from this copy of the app.",
                parent=self,
            )
            return
        try:
            open_with_default_app(manual_path)
            self.status.set(f"Opened {os.path.basename(manual_path)}")
        except Exception as e:
            dialogs.show_error(
                "User Manual",
                f"Couldn't open {manual_path} automatically ({e}). "
                "Open it directly with a PDF viewer.",
                parent=self,
            )

    @staticmethod
    def user_manual_path():
        """The user manual to open: the PDF if present, else the .docx,
        else None. Looks in docs/ next to the hlayout package -- the
        repo root in a source checkout, the bundle's _internal/ folder
        in an installed (PyInstaller) build."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for name in ("HLayout_User_Manual.pdf", "HLayout_User_Manual.docx"):
            path = os.path.join(root, "docs", name)
            if os.path.isfile(path):
                return path
        return None

    def show_command_reference(self):
        """Open the full HTML command bar + Python API reference
        (docs/help.html — every command and every Session method, each
        with a runnable example, in a searchable page) in the system's
        default web browser. Falls back to the old scrollable
        plain-text dialog (_show_command_reference_fallback) if that
        file can't be found -- e.g. a copy of the app distributed
        without its docs/ folder -- so Help > Command Reference always
        shows *something* even then."""
        # hlayout/gui/app.py -> hlayout/gui -> hlayout -> repo root -> docs/help.html
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        help_path = os.path.join(repo_root, "docs", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open(f"file://{help_path}")
            return
        self._show_command_reference_fallback()

    def _show_command_reference_fallback(self):
        """The old scrollable plain-text command reference dialog --
        kept only as a fallback for show_command_reference() above when
        docs/help.html isn't present alongside the app."""
        win = tk.Toplevel(self)
        win.title("Command Reference")
        win.geometry("760x520")

        text_frame = ttk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=8, pady=8)
        hbar = ttk.Scrollbar(text_frame, orient="horizontal")
        text = tk.Text(text_frame, wrap="none", bg="#111", fg="#ddd", insertbackground="#ddd",
                        font=("TkFixedFont",), xscrollcommand=hbar.set)
        hbar.config(command=text.xview)
        hbar.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        text.insert("1.0", COMMAND_REFERENCE)
        text.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))
