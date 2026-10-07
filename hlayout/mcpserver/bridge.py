"""GUI-side half of the MCP connection: a loopback-only socket through
which the MCP server (server.py, a separate process started by the MCP
client) runs operations inside the running HLayout window.

Off unless asked for (``hlayout_app.py --mcp`` / ``HLAYOUT_MCP=1`` ->
App.start_mcp_bridge()), since it lets a local process run Python in
the app. It listens on 127.0.0.1 only, on a random port, and every
request must carry a random per-run token; port and token are written
to ~/.hlayout/mcp_bridge.json for the MCP server to find.

Tk isn't thread-safe, so socket threads never touch the app: they queue
each request, and a Tk ``after()`` poll on the main thread runs it and
hands the reply back.

Wire format: one JSON object per line.
    -> {"token": "...", "op": "python", "args": {...}}
    <- {"ok": true, "result": "..."}  |  {"ok": false, "error": "..."}
"""

import hmac
import json
import os
import queue
import secrets
import socket
import threading

from . import ops

#: Read at call time (not import time) so tests can monkeypatch it.
BRIDGE_INFO_PATH = os.path.join(os.path.expanduser("~"), ".hlayout", "mcp_bridge.json")


def read_bridge_info(path: str = None):
    """{"port", "token", "pid"} of the running GUI's bridge, or None."""
    try:
        with open(path or BRIDGE_INFO_PATH, "r", encoding="utf-8") as f:
            info = json.load(f)
        int(info["port"]), str(info["token"])
        return info
    except Exception:
        return None


def sync_app(app):
    """Bring the GUI's widgets back in line with app.session after an
    operation that may have swapped the whole document (open) or the
    current cell, then repaint."""
    session = app.session
    canvas = getattr(app, "canvas", None)
    if canvas is None:
        return
    lib = session.library
    if canvas.library is not lib:
        app._close_library_bound_tool_windows()
        canvas.library = lib
        canvas.set_cell(session.current_cell_name)
        app.layer_panel.set_library(lib)
        app._reload_route_settings_from_library()
        app.refresh_all()
        canvas.fit_view()
    else:
        if canvas.cell_name != session.current_cell_name:
            canvas.set_cell(session.current_cell_name)
        app.refresh_all()
    app._update_undo_redo_menu_state()


class McpBridge:
    def __init__(self, app, poll_ms: int = 40, info_path: str = None):
        self.app = app
        self.poll_ms = poll_ms
        self.info_path = info_path or BRIDGE_INFO_PATH
        self.token = secrets.token_hex(16)
        self.port = None
        self._requests = queue.Queue()
        self._stopped = threading.Event()
        self._sock = None
        self.ctx = ops.Context(
            app.session, app.console, getattr(app, "interp", None), mode="live",
            get_path=lambda: app.current_path,
            set_path=lambda p: setattr(app, "current_path", p),
        )

    # --- lifecycle -------------------------------------------------------
    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        os.makedirs(os.path.dirname(self.info_path), exist_ok=True)
        with open(self.info_path, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
        threading.Thread(target=self._accept_loop, name="hlayout-mcp-accept",
                         daemon=True).start()
        self.app.after(self.poll_ms, self._poll)
        return self

    def stop(self):
        if self._stopped.is_set():
            return
        self._stopped.set()
        # close() alone doesn't wake a thread blocked in accept() on
        # every platform; shutdown() does on Linux/macOS, close() on Windows.
        for step in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                step()
            except OSError:
                pass
        info = read_bridge_info(self.info_path)
        if info and info.get("token") == self.token:  # not another instance's
            try:
                os.remove(self.info_path)
            except OSError:
                pass

    # --- socket threads (never touch Tk) ---------------------------------
    def _accept_loop(self):
        while not self._stopped.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            if self._stopped.is_set():
                conn.close()
                return
            threading.Thread(target=self._serve, args=(conn,),
                             name="hlayout-mcp-conn", daemon=True).start()

    def _serve(self, conn):
        with conn, conn.makefile("rwb") as f:
            for raw in f:
                reply = self._handle_line(raw)
                try:
                    f.write((json.dumps(reply) + "\n").encode("utf-8"))
                    f.flush()
                except OSError:
                    return

    def _handle_line(self, raw):
        try:
            req = json.loads(raw.decode("utf-8"))
            token = str(req.get("token", ""))
        except Exception:
            return {"ok": False, "error": "malformed request"}
        if not hmac.compare_digest(token, self.token):
            return {"ok": False, "error": "bad token"}
        reply_q = queue.Queue(1)
        self._requests.put((req.get("op"), req.get("args") or {}, reply_q))
        while True:
            try:
                return reply_q.get(timeout=0.5)
            except queue.Empty:
                if self._stopped.is_set():
                    return {"ok": False, "error": "HLayout is closing"}

    # --- Tk main thread ----------------------------------------------------
    def _poll(self):
        if self._stopped.is_set():
            return
        while True:
            try:
                op, args, reply_q = self._requests.get_nowait()
            except queue.Empty:
                break
            try:
                reply = {"ok": True, "result": ops.call(self.ctx, op, args)}
            except ops.OpError as e:
                reply = {"ok": False, "error": str(e)}
            try:
                sync_app(self.app)
            except Exception as e:  # a repaint problem mustn't lose the reply
                key = "result" if reply["ok"] else "error"
                reply[key] += f"\n(warning: GUI refresh failed: {e})"
            reply_q.put(reply)
        self.app.after(self.poll_ms, self._poll)
