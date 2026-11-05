# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Netlist readers for placement: KiCad (.net), CSV and Tango (.tng).

read_netlist(path) -> Netlist with
  .components  {ref: Component(ref, value, footprint, fields, part, description)}
  .nets        [Net(name, cls, nodes=[Node(ref, pin, pintype, pinfunction)])]

KiCad 6-8 export the netlist as an s-expression:
  (export (version "E")
    (components (comp (ref "U1") (value "...") (footprint "Lib:Name")
                      (fields (field (name "HL_Family") "die") ...)
                      (property (name "HL_Power_mW") (value "120")) ...))
    (nets (net (code "1") (name "GND") (class "Power")
               (node (ref "U1") (pin "7") (pinfunction "GND") (pintype "power_in")))))
Older exports put symbol fields under (fields (field (name X) value)); KiCad 7+
also writes (property (name X) (value Y)). Both are read.

CSV: a header with net + ref + pin columns (net,ref,pin[,...]) or pad + net
("U1.7,GND"); components are then just the refs seen. Tango: [ ] component
blocks (ref, footprint, value) and ( ) net blocks (name, then REF-PIN lines).
"""
import csv
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Component:
    ref: str
    value: str = ""
    footprint: str = ""
    fields: Dict[str, str] = field(default_factory=dict)
    part: str = ""
    description: str = ""


@dataclass
class Node:
    ref: str
    pin: str
    pintype: str = ""
    pinfunction: str = ""


@dataclass
class Net:
    name: str
    cls: str = ""
    nodes: List[Node] = field(default_factory=list)


@dataclass
class Netlist:
    components: Dict[str, Component] = field(default_factory=dict)
    nets: List[Net] = field(default_factory=list)
    source: str = ""

    def pad_nets(self) -> Dict[str, str]:
        """{"RefDes.Pin": net} -- what Session.set_net_names() stores."""
        return {f"{n.ref}.{n.pin}": net.name for net in self.nets for n in net.nodes}


# --- s-expression ------------------------------------------------------------

def _tokens(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            yield ("S", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield ("S", text[i:j])
            i = j


def parse_sexpr(text):
    stack = [[]]
    for t in _tokens(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        else:
            stack[-1].append(t[1])
    while len(stack) > 1:
        done = stack.pop()
        stack[-1].append(done)
    return stack[0]


def _kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _val(node, key, default=""):
    k = _kids(node, key)
    if k and len(k[0]) > 1 and not isinstance(k[0][1], list):
        return str(k[0][1])
    return default


def read_kicad_netlist(text: str) -> Netlist:
    tree = parse_sexpr(text)
    root = next((c for c in tree if isinstance(c, list) and c and c[0] == "export"), None)
    if root is None:
        raise ValueError("not a KiCad netlist: no (export ...) form")
    nl = Netlist()
    for comps in _kids(root, "components"):
        for c in _kids(comps, "comp"):
            ref = _val(c, "ref")
            if not ref:
                continue
            comp = Component(ref=ref, value=_val(c, "value"), footprint=_val(c, "footprint"))
            for ls in _kids(c, "libsource"):
                comp.part = _val(ls, "part")
                comp.description = _val(ls, "description")
            for fl in _kids(c, "fields"):
                for f in _kids(fl, "field"):
                    nm = _val(f, "name")
                    vals = [x for x in f[1:] if not isinstance(x, list)]
                    if nm:
                        comp.fields[nm] = str(vals[-1]) if vals else ""
            for p in _kids(c, "property"):
                nm = _val(p, "name")
                if nm:
                    comp.fields.setdefault(nm, _val(p, "value"))
            nl.components[ref] = comp
    for nets in _kids(root, "nets"):
        for n in _kids(nets, "net"):
            net = Net(name=_val(n, "name"), cls=_val(n, "class"))
            for nd in _kids(n, "node"):
                net.nodes.append(Node(ref=_val(nd, "ref"), pin=_val(nd, "pin"),
                                      pintype=_val(nd, "pintype"),
                                      pinfunction=_val(nd, "pinfunction")))
            if net.name and net.nodes:
                nl.nets.append(net)
    return nl


def read_csv_netlist(path: str) -> Netlist:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path}: no rows")
    cols = {c.lower().strip(): c for c in rows[0].keys() if c}
    net_c = next((cols[k] for k in ("net", "net_name", "netname", "signal") if k in cols), None)
    ref_c = next((cols[k] for k in ("ref", "refdes", "ref_des", "component") if k in cols), None)
    pin_c = next((cols[k] for k in ("pin", "pad", "dip_die_pad") if k in cols and k != "pad"), None)
    pad_c = cols.get("pad")
    if net_c is None:
        raise ValueError(f"{path}: no net column")
    nl = Netlist()
    by = {}
    for r in rows:
        net = (r.get(net_c) or "").strip()
        if ref_c and pin_c:
            ref, pin = (r.get(ref_c) or "").strip(), (r.get(pin_c) or "").strip()
        elif pad_c and "." in (r.get(pad_c) or ""):
            ref, _, pin = r[pad_c].strip().partition(".")
        else:
            continue
        if not (net and ref and pin):
            continue
        by.setdefault(net, Net(name=net)).nodes.append(Node(ref=ref, pin=pin))
        nl.components.setdefault(ref, Component(ref=ref))
    nl.nets = list(by.values())
    return nl


def read_tango_netlist(text: str) -> Netlist:
    nl = Netlist()
    i = 0
    lines = [l.strip() for l in text.splitlines()]
    while i < len(lines):
        l = lines[i]
        if l == "[":
            block = []
            i += 1
            while i < len(lines) and lines[i] != "]":
                block.append(lines[i])
                i += 1
            if block:
                ref = block[0]
                nl.components[ref] = Component(ref=ref, footprint=block[1] if len(block) > 1 else "",
                                               value=block[2] if len(block) > 2 else "")
        elif l == "(":
            block = []
            i += 1
            while i < len(lines) and lines[i] != ")":
                block.append(lines[i])
                i += 1
            if block:
                net = Net(name=block[0])
                for m in block[1:]:
                    ref, sep, pin = m.rpartition("-")
                    if sep and ref:
                        net.nodes.append(Node(ref=ref, pin=pin))
                        nl.components.setdefault(ref, Component(ref=ref))
                if net.nodes:
                    nl.nets.append(net)
        i += 1
    return nl


def read_netlist(path: str) -> Netlist:
    """KiCad .net, CSV or Tango .tng, by extension (KiCad also sniffed)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        nl = read_csv_netlist(path)
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if ext in (".tng", ".tan") or (not text.lstrip().startswith("(") and "[" in text):
            nl = read_tango_netlist(text)
        else:
            nl = read_kicad_netlist(text)
    nl.source = path
    return nl
