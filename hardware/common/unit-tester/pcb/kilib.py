"""Minimal KiCad 10 symbol-library + schematic-text helpers (stdlib only).

Library symbols are read from the installed KiCad symbol libraries (env KICAD_SHARE overrides the
install location) and embedded in the generated sheets; `extends` symbols are flattened.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
SYMDIR = KICAD_SHARE / "symbols"

_lib_cache: dict[str, str] = {}


def lib_text(lib: str) -> str:
    if lib not in _lib_cache:
        _lib_cache[lib] = (SYMDIR / f"{lib}.kicad_sym").read_text(encoding="utf8")
    return _lib_cache[lib]


def balanced(t: str, i: int) -> str:
    """Text of the parenthesised expression starting at t[i] == '('."""
    depth, j, instr = 0, i, False
    while True:
        c = t[j]
        if instr:
            if c == "\\":
                j += 1
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return t[i:j + 1]
        j += 1


def raw_block(lib: str, name: str) -> str:
    t = lib_text(lib)
    m = re.search(r'\n\t\(symbol "%s"\n' % re.escape(name), t)
    if not m:
        raise SystemExit(f"symbol {lib}:{name} not found")
    return balanced(t, m.start() + 1)


def props(block: str) -> dict[str, str]:
    """top-level (depth 2 tabs) property blocks by name."""
    out = {}
    for m in re.finditer(r'\n\t\t\(property "([^"]+)"', block):
        out[m[1]] = balanced(block, m.start() + 1)
    return out


def flat_block(lib: str, name: str) -> str:
    b = raw_block(lib, name)
    m = re.search(r'\(extends "([^"]+)"\)', b)
    if not m:
        return b
    parent = flat_block(lib, m[1])
    pp, cp = props(parent), props(b)
    for k, v in cp.items():
        if k in pp:
            parent = parent.replace(pp[k], v, 1)
        else:  # add before first unit sub-symbol
            idx = parent.index('\n\t\t(symbol "')
            parent = parent[:idx] + "\n\t\t" + v + parent[idx:]
    parent = parent.replace(f'(symbol "{m[1]}"', f'(symbol "{name}"', 1)
    parent = re.sub(r'\(symbol "%s_(\d+_\d+)"' % re.escape(m[1]), r'(symbol "%s_\1"' % name, parent)
    return parent


OUT = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}  # pin angle -> outward unit (x right, y up)
LABEL_ANGLE = {(-1, 0): 180, (1, 0): 0, (0, 1): 90, (0, -1): 270}


class Sym:
    """A library symbol: embedded text + per-unit pin geometry (symbol coordinates, y up)."""

    def __init__(self, lib: str, name: str):
        self.lib, self.name = lib, name
        self.block = flat_block(lib, name)
        self.lib_id = f"{lib}:{name}"
        self.units: dict[int, dict[str, tuple[float, float, int, str, str]]] = {}
        self.units_n = 1
        for m in re.finditer(r'\n\t\t\(symbol "[^"]+_(\d+)_(\d+)"', self.block):
            u = int(m[1])
            sub = balanced(self.block, m.start() + 1)
            d = self.units.setdefault(u, {})
            for pm in re.finditer(
                    r'\(pin (\w+) \w+\s+\(at ([-\d.]+) ([-\d.]+) (\d+)\).*?\(name "([^"]*)".*?\(number "([^"]*)"',
                    sub, re.S):
                d[pm[6]] = (float(pm[2]), float(pm[3]), int(pm[4]), pm[5], pm[1])
        self.units_n = max([u for u in self.units if u > 0] or [1])
        self.fields = {}
        for m in re.finditer(r'\(property "(Reference|Value)"\s+"[^"]*"\s+\(at ([-\d.]+) ([-\d.]+) ', self.block):
            self.fields[m[1]] = (float(m[2]), float(m[3]))
        m = re.search(r'\(property "Footprint" "([^"]*)"', self.block)
        self.default_fp = m[1] if m else ""
        self.is_power = "(power" in self.block.split("\n\t\t(symbol", 1)[0]

    def pins(self, unit: int) -> dict[str, tuple[float, float, int, str, str]]:
        d = dict(self.units.get(0, {}))
        if self.units_n > 1 or unit in self.units:
            d.update(self.units.get(unit, {}))
        return d

    def embedded(self) -> str:
        b = self.block.replace(f'(symbol "{self.name}"', f'(symbol "{self.lib_id}"', 1)
        return b


def rot(x: float, y: float, r: int) -> tuple[float, float]:
    for _ in range(r // 90 % 4):
        x, y = -y, x
    return x, y
