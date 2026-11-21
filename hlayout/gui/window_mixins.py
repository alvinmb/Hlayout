# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behaviour shared by more than one tool window."""

from tkinter import filedialog

from . import dialogs


class JobListMixin:
    """Add / Edit / Delete for a window that keeps a list of export jobs
    (DXF and Gerber batch export).

    The window provides: self.jobs, self.job_table (a Treeview whose iids
    are the job indices), _job_dialog(job=None) returning a modal dialog
    with a .result, _persist() and _refresh_jobs()."""

    def _current_job_row(self):
        sel = self.job_table.selection()
        return int(sel[0]) if sel else -1

    def _add_job(self):
        dlg = self._job_dialog()
        self.wait_window(dlg)
        if dlg.result is not None:
            self.jobs.append(dlg.result)
            self._persist()
            self._refresh_jobs()

    def _edit_job(self):
        row = self._current_job_row()
        if row < 0:
            dialogs.show_info("Edit Job", "Select a job first.", parent=self)
            return
        dlg = self._job_dialog(job=self.jobs[row])
        self.wait_window(dlg)
        if dlg.result is not None:
            self.jobs[row] = dlg.result
            self._persist()
            self._refresh_jobs()

    def _delete_job(self):
        row = self._current_job_row()
        if row < 0:
            dialogs.show_info("Delete Job", "Select a job first.", parent=self)
            return
        del self.jobs[row]
        self._persist()
        self._refresh_jobs()


class NetNamesImportMixin:
    """"Import net names..." for the router windows. The window provides
    self.session, _say(text) and reload()."""

    def _import_names(self):
        p = filedialog.askopenfilename(parent=self, title="Net names CSV",
                                       filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if not p:
            return
        try:
            n = self.session.import_net_names(p)
            self._say(f"{n} named pad(s) imported from {p}")
        except Exception as e:
            self._say(f"Net names: {e}")
        self.reload()
