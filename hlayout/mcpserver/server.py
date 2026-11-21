# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stdio MCP server for HLayout (``python -m hlayout.mcpserver``).

Speaks MCP's JSON-RPC 2.0 over stdin/stdout, one message per line,
using only the standard library (no ``mcp`` package needed). Each tool
call is forwarded to the running HLayout GUI when its bridge is
reachable (see bridge.py) and otherwise run on a headless Session held
in this process (see ops.py).

    python -m hlayout.mcpserver [--headless] [--design path.gds]
"""

import argparse
import json
import os
import socket
import sys

from . import bridge, ops
from .. import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "HLayout is a thick-film hybrid / PCB layout CAD tool. Coordinates are "
    "database units: 1 DBU = 1 micron. Call hlayout_status first: its 'mode' "
    "says whether you are driving the user's running HLayout window ('live') "
    "or a private in-memory session ('headless', nothing appears on screen "
    "and nothing is kept unless you hlayout_save). Use hlayout_api to "
    "discover the scripting API, then hlayout_python (session is `s`) or "
    "hlayout_command (command-bar lines such as 'RECT 0 0,0 100,50')."
)


def _tool(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


TOOLS = [
    _tool("hlayout_status",
          "Current state: live/headless mode, open file, library name, current "
          "cell, cell list, named layers, undo state. Call this first."),
    _tool("hlayout_open",
          "Open a GDSII design (and its .hlaux sidecar, if any), replacing the "
          "current document. Unsaved changes to the current document are lost.",
          {"path": {"type": "string", "description": "Path to a .gds file"}},
          ["path"]),
    _tool("hlayout_save",
          "Save the design as GDSII plus its sidecar (technology + attached "
          "libraries). Overwrites the file.",
          {"path": {"type": "string",
                    "description": "Target .gds path; defaults to the open file"}}),
    _tool("hlayout_command",
          "Run one or more HLayout command-bar lines (one per line), e.g. "
          "'CELL TOP', 'RECT 0 0,0 100,50', 'PLACE SOT-23 0,0', 'DRC', 'UNDO'. "
          "Stops at the first line that fails. See hlayout_api name='commands'.",
          {"text": {"type": "string", "description": "Command line(s)"}},
          ["text"]),
    _tool("hlayout_python",
          "Execute Python against the design. `s` (alias `hl`) is the "
          "hlayout.scripting.Session, `library` the Library; Cell, Polygon, "
          "Path, Rectangle, SRef, Text are in scope and variables persist "
          "between calls. A single expression returns its repr; otherwise "
          "print() what you need. Session edits are undoable.",
          {"code": {"type": "string", "description": "Python source"}},
          ["code"]),
    _tool("hlayout_api",
          "Scripting API reference. No name: every Session method with its "
          "signature and summary. name=<method>: its full documentation. "
          "name='commands': the command-bar reference.",
          {"name": {"type": "string"}}),
    _tool("hlayout_describe_cell",
          "Summarise a cell: hierarchical bounding box, size in mm, shape "
          "counts per layer, placed instances (srefs/arefs) and numbered pins.",
          {"name": {"type": "string",
                    "description": "Cell name; defaults to the current cell"},
           "max_items": {"type": "integer",
                         "description": "Cap on listed instances/pins (default 50)"}}),
]

TOOL_OPS = {t["name"]: t["name"][len("hlayout_"):] for t in TOOLS}


class Backend:
    """Routes an operation to the live GUI if its bridge answers,
    otherwise to a headless session owned by this process."""

    def __init__(self, headless=False, design_path=None, timeout=600):
        self.headless = headless
        self.design_path = design_path
        self.timeout = timeout
        self._local = None
        self._last_mode = None

    def _connect(self):
        if self.headless:
            return None, None
        info = bridge.read_bridge_info()
        if not info:
            return None, None
        try:
            return socket.create_connection(("127.0.0.1", int(info["port"])), timeout=3), info
        except OSError:
            return None, None  # stale file: the GUI has gone away

    def _live_call(self, sock, info, op, args):
        with sock:
            sock.settimeout(self.timeout)
            request = {"token": info["token"], "op": op, "args": args}
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            with sock.makefile("rb") as f:
                raw = f.readline()
        if not raw:
            raise ops.OpError("the HLayout window closed the connection before replying")
        reply = json.loads(raw.decode("utf-8"))
        if not reply.get("ok"):
            raise ops.OpError(reply.get("error") or "unknown error")
        return reply.get("result", "")

    def call(self, op, args):
        sock, info = self._connect()
        mode = "live" if sock else "headless"
        note = ""
        if mode != self._last_mode:
            # Say so whenever the target changes: editing a private
            # headless session while believing it's the visible window
            # (or the reverse) is the one mistake worth shouting about.
            if mode == "live":
                note = "[connected to the running HLayout window]\n"
            elif self.headless:
                note = "[headless session]\n"
            else:
                note = ("[no running HLayout window with the MCP bridge on "
                        "(tick Tools > MCP Server (Claude) in HLayout, or start it "
                        "with: python hlayout_app.py --mcp) -- "
                        "using a private headless session"
                        + (", NOT the window used before" if self._last_mode else "")
                        + "]\n")
            self._last_mode = mode
        try:
            if sock:
                return note + self._live_call(sock, info, op, args)
            if self._local is None:
                self._local = ops.local_context(self.design_path)
            return note + ops.call(self._local, op, args)
        except ops.OpError as e:
            raise ops.OpError(note + str(e))


# --- JSON-RPC ------------------------------------------------------------
def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg, backend):
    """One JSON-RPC message in -> response dict, or None for notifications."""
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request")
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if method is None or "id" not in msg:
        return None  # a notification, or a response to something we never sent

    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hlayout", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        op = TOOL_OPS.get(name)
        if op is None:
            return _error(msg_id, -32602, f"unknown tool {name!r}")
        try:
            text, is_error = backend.call(op, params.get("arguments") or {}), False
        except ops.OpError as e:
            text, is_error = str(e), True
        except Exception as e:  # never let one bad call kill the server
            text, is_error = f"{type(e).__name__}: {e}", True
        return _result(msg_id, {"content": [{"type": "text", "text": text}],
                                "isError": is_error})
    return _error(msg_id, -32601, f"method not found: {method}")


def serve(stdin, stdout, backend):
    def send(obj):
        stdout.write((json.dumps(obj) + "\n").encode("utf-8"))
        stdout.flush()

    for raw in iter(stdin.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            send(_error(None, -32700, "parse error"))
            continue
        if isinstance(msg, list):  # JSON-RPC batch
            replies = [r for r in (handle(m, backend) for m in msg) if r is not None]
            if replies:
                send(replies)
            continue
        reply = handle(msg, backend)
        if reply is not None:
            send(reply)


def private_stdio():
    """Take the protocol pipes off the process's standard handles.

    Returns (stdin, stdout) binary files on private, non-inheritable
    copies of the JSON-RPC pipes, and repoints the standard handles: stdin
    at the null device, stdout at stderr. Anything this process launches
    (FreeCAD, Freerouting, a test run from hlayout_python, ...) then never
    gets the protocol pipes:

    - On Windows a child that inherits a pipe handle this process is
      blocked reading (serve()'s readline) hangs at startup until the
      next MCP message arrives -- subprocess.Popen with stdin=None, or
      with only stdout given (it fills the rest from GetStdHandle),
      hands it exactly that.
    - A child writing to an inherited stdout would put stray text into
      the JSON-RPC stream and break the client's parser.
    """
    for f in (sys.stdin, sys.stdout):
        try:
            f.flush()
        except (AttributeError, OSError, ValueError):
            pass
    proto_in = os.dup(0)    # os.dup: non-inheritable (PEP 446)
    proto_out = os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    if sys.platform == "win32":
        # Popen (and CreateProcess) read the Win32 std handles, not fds
        import ctypes
        import msvcrt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetStdHandle(-10, msvcrt.get_osfhandle(0))   # STD_INPUT_HANDLE
        k32.SetStdHandle(-11, msvcrt.get_osfhandle(1))   # STD_OUTPUT_HANDLE
    sys.stdin = open(os.devnull, "r")
    return os.fdopen(proto_in, "rb"), os.fdopen(proto_out, "wb")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m hlayout.mcpserver",
                                     description="HLayout MCP server (stdio)")
    parser.add_argument("--headless", action="store_true",
                        help="never connect to a running HLayout window")
    parser.add_argument("--design", metavar="PATH",
                        help="GDSII file to open in the headless session")
    opts = parser.parse_args(argv)

    # stdout carries the protocol and nothing else: anything a script, a
    # library or a child process prints goes to stderr instead.
    stdin, stdout = private_stdio()
    sys.stdout = sys.stderr
    serve(stdin, stdout, Backend(headless=opts.headless, design_path=opts.design))


if __name__ == "__main__":
    main()
