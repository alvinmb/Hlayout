# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal Model Context Protocol (MCP) client for SolidCraft.

SolidCraft (github.com/bherbruck/solidcraft, MIT/Apache-2.0) is a
parametric CAD app with a built-in MCP server: ``solidcraft-cli mcp``
speaks JSON-RPC 2.0 over stdio. Headless it keeps its own design; with
``--connect 127.0.0.1:PORT`` it drives a running SolidCraft window started
with ``solidcraft --control PORT``, so the model builds in front of you.

This module only knows the protocol: start the server, ``initialize``,
then ``tools/call`` (execute / batch / export / screenshot / ...).
hlayout.io.solidcraftio decides what to build.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Optional

from ..core.errors import LayoutError

PROTOCOL_VERSION = "2024-11-05"


def find_solidcraft(hint: Optional[str] = None):
    """(solidcraft.exe, solidcraft-cli.exe) -- from ``hint`` (either exe or
    the folder holding them), PATH, or the copy HLayout installs under
    ~/.hlayout/solidcraft/<version>/. Either may be None."""
    exe = ".exe" if os.name == "nt" else ""
    cands = []
    if hint:
        h = os.path.dirname(hint) if os.path.isfile(hint) else hint
        cands.append(h)
    for name in ("solidcraft-cli", "solidcraft"):
        w = shutil.which(name)
        if w:
            cands.append(os.path.dirname(w))
    root = os.path.join(os.path.expanduser("~"), ".hlayout", "solidcraft")
    if os.path.isdir(root):
        for dirpath, _dirs, files in sorted(os.walk(root), reverse=True):
            if "solidcraft-cli" + exe in files:
                cands.append(dirpath)
    if os.name == "nt":
        for pf in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if pf:
                cands.append(os.path.join(pf, "SolidCraft"))
                cands.append(os.path.join(pf, "Programs", "SolidCraft"))
    for d in cands:
        cli = os.path.join(d, "solidcraft-cli" + exe)
        app = os.path.join(d, "solidcraft" + exe)
        if os.path.isfile(cli):
            return (app if os.path.isfile(app) else None), cli
    return None, None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SolidCraftError(LayoutError):
    pass


class SolidCraftClient:
    """``with SolidCraftClient(cli, connect=port) as sc: sc.batch([...])``.
    ``connect`` = the --control port of a running SolidCraft window (None:
    a headless design inside the server). ``allow`` = folders the server
    may read/write (exports, screenshots)."""

    def __init__(self, cli: str, connect: Optional[int] = None, allow=(), timeout: float = 120.0):
        self.cli = cli
        self.connect = connect
        self.allow = [a for a in allow if a]
        self.timeout = timeout
        self.proc = None
        self._id = 0
        self._lock = threading.Lock()
        self._lines = []
        self._cv = threading.Condition()
        self._stderr = []

    # -- process ---------------------------------------------------------
    def start(self):
        cmd = [self.cli, "mcp"]
        if self.connect:
            cmd += ["--connect", f"127.0.0.1:{int(self.connect)}"]
        for a in self.allow:
            cmd += ["--allow-path", a]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1, creationflags=flags)
        threading.Thread(target=self._pump, args=(self.proc.stdout, True), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, False), daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                    "clientInfo": {"name": "HLayout", "version": "1"}})
        self.notify("notifications/initialized")
        return self

    def _pump(self, stream, is_out):
        for line in stream:
            line = line.strip()
            if not line:
                continue
            if is_out:
                with self._cv:
                    self._lines.append(line)
                    self._cv.notify_all()
            else:
                self._stderr.append(line)
                del self._stderr[:-50]

    def close(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.stdin.close()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- JSON-RPC --------------------------------------------------------
    def _send(self, msg):
        if self.proc is None or self.proc.poll() is not None:
            raise SolidCraftError("the SolidCraft MCP server is not running"
                                  + (": " + self._stderr[-1] if self._stderr else ""))
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method, params=None, timeout=None):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            end = time.monotonic() + (timeout or self.timeout)
            with self._cv:
                while True:
                    for i, line in enumerate(self._lines):
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        if msg.get("id") == rid:
                            del self._lines[i]
                            if "error" in msg:
                                raise SolidCraftError(f"{method}: {msg['error'].get('message', msg['error'])}")
                            return msg.get("result")
                    left = end - time.monotonic()
                    if left <= 0:
                        raise SolidCraftError(f"{method}: no reply from SolidCraft in "
                                              f"{timeout or self.timeout:.0f} s")
                    if self.proc is None or self.proc.poll() is not None:
                        raise SolidCraftError("the SolidCraft MCP server stopped"
                                              + (": " + self._stderr[-1] if self._stderr else ""))
                    self._cv.wait(min(left, 0.5))

    def call(self, tool, timeout=None, **arguments):
        """tools/call -> the tool's result: parsed JSON when its text
        content is JSON, else the text. A tool-level error raises."""
        res = self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
        texts = [c.get("text", "") for c in (res or {}).get("content", []) if c.get("type") == "text"]
        body = "\n".join(texts)
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = body
        if (res or {}).get("isError"):
            raise SolidCraftError(f"{tool}: {body[:500]}")
        return data

    # -- conveniences ----------------------------------------------------
    def execute(self, command, **params):
        return self.call("execute", command=command, params=params)

    def batch(self, commands, rollback=False, timeout=None):
        """commands = [{"command": id, "params": {...}}, ...]."""
        return self.call("batch", commands=commands, rollback=rollback, timeout=timeout)


def launch_window(app: str, port: int, wait: float = 30.0):
    """Start the SolidCraft window listening for MCP control on ``port``;
    wait until the port accepts. Returns the Popen."""
    proc = subprocess.Popen([app, "--control", str(int(port))])
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SolidCraftError(f"SolidCraft exited (code {proc.returncode}) before opening "
                                  f"its control port")
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.3)
    raise SolidCraftError(f"SolidCraft did not open control port {port} within {wait:.0f} s")
