# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""MCP (Model Context Protocol) server for HLayout.

Lets an MCP client such as Claude drive HLayout as a set of tools:

    python -m hlayout.mcpserver            # stdio MCP server

Two ways it can reach a design:

* **Live** -- HLayout's GUI is running with its bridge on
  (``python hlayout_app.py --mcp``, or env ``HLAYOUT_MCP=1``). Every
  tool call is forwarded to that window and run on its Tk thread, so
  edits appear on the canvas and land in its normal undo history.
* **Headless** -- no GUI reachable: the server keeps its own
  ``hlayout.scripting.Session`` in memory and works on files directly.

See ops.py (the operations, shared by both modes), bridge.py (the
GUI-side socket) and server.py (the JSON-RPC/stdio side).
"""
