#!/usr/bin/env python3
"""Generate the KiCad 10 projects + hierarchical schematics of the relay COUNTER boards.

Two boards (see README.md for the scheme and `relay.rs` `Counters` for the golden model):

  counter_common  pL (= a4 = l), pM, a3 = l - m : three base-26 relay digits + the rule wiring
  counter_unit    a2, a1 (the offset-dial preset) : two base-26 relay digits + 10 buffer relays
                  that repeat the common a3/a4 code onto this unit's shifter-board CTRL lines

Every digit is the same 11-relay circuit (5 slave + 5 master + 1 wrap-detect relay, G6S-2 DPDT
24 V) with diodes; the generator writes one sheet file per digit instance.

Stdlib only.  Library symbols are copied from the installed KiCad standard libraries
(env KICAD_SHARE overrides the default install location).

    python gen_counter.py            # writes counter_common.* / counter_unit.* + digit sheets
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
SYMDIR = KICAD_SHARE / "symbols"

NS = uuid.UUID("b2f5c0de-7a31-4d6e-9e84-3c1a5f7d2b02")  # fixed namespace -> stable UUIDs
BITS = 5

# ----------------------------------------------------------------------------- parts
FP_RELAY = "Relay_THT:Relay_DPDT_Omron_G6S-2"
FP_DSUB = "Connector_Dsub:DSUB-%d_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm"
FP_DIODE = "Diode_SMD:D_SOD-123"
FP_R = "Resistor_SMD:R_0603_1608Metric"
FP_LED = "LED_SMD:LED_0603_1608Metric"
DS_RELAY = "http://omronfs.omron.com/en_US/ecb/products/pdf/en-g6s.pdf"

# G6S-2 pin functions (see shifter-board/gen_shifter.py: armature of pole 1 rests on pin 3,
# of pole 2 on pin 10 when the coil is off; coil = pin 1 (+), pin 12 (-)).
RELAY_PINS = dict(coil_p="1", coil_n="12", c1="4", nc1="3", no1="5", c2="9", nc2="10", no2="8")

V_SCHOTTKY = "1N5819W"   # feed / OR diodes (Schottky: ~0.3 V drop keeps coil voltage up)
V_SWITCH = "1N4148W"     # coil flyback diodes


def g(v: float) -> float:
    """Snap to the 2.54 mm grid (keeps every pin/wire end on the 1.27 mm connection grid)."""
    return round(round(v / 2.54) * 2.54, 2)


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ----------------------------------------------------------------------------- library access
_lib_cache: dict[str, str] = {}


def lib_text(lib: str) -> str:
    if lib not in _lib_cache:
        _lib_cache[lib] = (SYMDIR / f"{lib}.kicad_sym").read_text(encoding="utf8")
    return _lib_cache[lib]


def sym_block(lib: str, name: str) -> str:
    t = lib_text(lib)
    m = re.search(r'\n\t\(symbol "%s"\n' % re.escape(name), t)
    if not m:
        raise SystemExit(f"symbol {lib}:{name} not found")
    i = m.start() + 1
    depth, j = 0, i
    while True:
        c = t[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    b = t[i:j + 1]
    if "(extends" in b:
        raise SystemExit(f"{lib}:{name} uses extends; pick a base symbol")
    return b


class Sym:
    """A library symbol: embedded text + pin geometry (rotation 0 only)."""

    def __init__(self, lib: str, name: str):
        self.lib, self.name = lib, name
        self.block = sym_block(lib, name)
        self.lib_id = f"{lib}:{name}"
        self.pins: dict[str, tuple[float, float, int]] = {}
        for m in re.finditer(
                r'\(pin \w+ \w+\s+\(at ([-\d.]+) ([-\d.]+) (\d+)\).*?\(number "([^"]*)"', self.block, re.S):
            self.pins[m[4]] = (float(m[1]), float(m[2]), int(m[3]))
        self.fields = {}
        for m in re.finditer(r'\(property "(Reference|Value)"\s+"[^"]*"\s+\(at ([-\d.]+) ([-\d.]+) ', self.block):
            self.fields[m[1]] = (float(m[2]), float(m[3]))

    def embedded(self) -> str:
        return self.block.replace(f'(symbol "{self.name}"', f'(symbol "{self.lib_id}"', 1)


S_RELAY = Sym("Relay", "G6S-2")
S_DIODE = Sym("Device", "D")
S_R = Sym("Device", "R_US")
S_LED = Sym("Device", "LED")
S_DB9 = Sym("Connector", "DE9_Pins")
S_DA15 = Sym("Connector", "DA15_Pins")
S_DB25 = Sym("Connector", "DB25_Pins")

OUT = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}  # pin angle -> outward unit (x right, y up)
LABEL_ANGLE = {(-1, 0): 180, (1, 0): 0, (0, 1): 90, (0, -1): 270}


class Sheet:
    def __init__(self, project: str, file_uuid: str, paper: str, title: str, comments=()):
        self.project, self.uuid, self.paper, self.title = project, file_uuid, paper, title
        self.comments = list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0

    def _u(self, kind: str) -> str:
        self.n += 1
        return uid(self.uuid, kind, self.n)

    def wire(self, x1, y1, x2, y2):
        self.items.append(
            f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self._u("w")}")\n\t)')

    @staticmethod
    def _just(angle):
        return "right bottom" if angle in (180, 270) else "left bottom"

    def label(self, name, x, y, angle):
        self.items.append(
            f'\t(label "{name}"\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t)\n\t\t\t(justify {self._just(angle)})\n\t\t)\n\t\t(uuid "{self._u("l")}")\n\t)')

    def hlabel(self, name, x, y, angle):
        just = "right" if angle == 180 else "left"
        self.items.append(
            f'\t(hierarchical_label "{name}"\n\t\t(shape bidirectional)\n\t\t(at {f(x)} {f(y)} {angle})\n'
            f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
            f'\t\t(uuid "{self._u("h")}")\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def symbol(self, sym: Sym, x, y, ref, value, footprint, datasheet="", desc="", inst_path="", show_value=True):
        self.libs[sym.lib_id] = sym

        def prop(name, val, key=None, hide=False):
            ox, oy = sym.fields.get(key or name, (0.0, 0.0))
            h = "\n\t\t\t(hide yes)" if hide else ""
            pos = f"{f(x + ox)} {f(y - oy)}" if key else f"{f(x)} {f(y)}"
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {pos} 0){h}\n\t\t\t(effects\n\t\t\t\t(font\n'
                    f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')
        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n'
             f'\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", ref, "Reference")
        s += prop("Value", value, "Value", hide=not show_value)
        s += prop("Footprint", footprint, hide=True)
        s += prop("Datasheet", datasheet, hide=True)
        s += prop("Description", desc, hide=True)
        for num in sym.pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{self.project}"\n\t\t\t\t(path "{inst_path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

    def pin_xy(self, sym: Sym, x, y, num):
        px, py, a = sym.pins[num]
        return x + px, y - py, OUT[a]

    def stub_label(self, px, py, out, name, length=2.54):
        dx, dy = out
        ex, ey = px + dx * length, py - dy * length
        self.wire(px, py, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[out])

    def pin_net(self, sym, x, y, num, name, length=2.54):
        px, py, out = self.pin_xy(sym, x, y, num)
        if name is None:
            self.noconnect(px, py)
        else:
            self.stub_label(px, py, out, name, length)

    def render(self) -> str:
        head = (f'(kicad_sch\n\t(version 20250610)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                f'\t(uuid "{self.uuid}")\n\t(paper "{self.paper}")\n\t(title_block\n\t\t(title "{self.title}")\n'
                f'\t\t(date "2026-11-15")\n\t\t(rev "A")\n')
        for i, c in enumerate(self.comments, 1):
            head += f'\t\t(comment {i} "{c}")\n'
        head += "\t)\n\t(lib_symbols\n"
        for s in self.libs.values():
            head += "\n".join("\t" + ln for ln in s.embedded().split("\n")) + "\n"
        head += "\t)\n"
        return head + "\n".join(self.items) + "\n\t(embedded_fonts no)\n)\n"


# ----------------------------------------------------------------------------- placement helpers
def relay(sh: Sheet, path: str, ref: str, x: float, y: float, desc: str, nets: dict):
    """Place a G6S-2 and label every pin.  nets: key of RELAY_PINS -> net name or None (no-connect)."""
    x, y = g(x), g(y)
    sh.symbol(S_RELAY, x, y, ref, "G6S-2 24VDC", FP_RELAY, DS_RELAY, desc, path)
    for key, net in nets.items():
        sh.pin_net(S_RELAY, x, y, RELAY_PINS[key], net)


def diode(sh: Sheet, path: str, ref: str, x: float, y: float, k_net: str, a_net: str, desc: str, value=V_SCHOTTKY):
    """Diode, cathode (pin 1, left) on k_net, anode (pin 2, right) on a_net."""
    x, y = g(x), g(y)
    sh.symbol(S_DIODE, x, y, ref, value, FP_DIODE, "", desc, path, show_value=False)
    sh.pin_net(S_DIODE, x, y, "1", k_net)
    sh.pin_net(S_DIODE, x, y, "2", a_net)


class DiodeCounter:
    """Sequential diode reference numbers D{d}01.. within one sheet."""

    def __init__(self, d: int):
        self.d, self.n = d, 0

    def ref(self) -> str:
        self.n += 1
        return f"D{self.d}{self.n:02d}"


# ----------------------------------------------------------------------------- digit sheet
def a_net(i: int) -> str:
    return "AIN" if i == 0 else "AOUT" if i == BITS else f"A{i}"


def z_net(i: int) -> str:
    return "ZIN" if i == 0 else "ZOUT" if i == BITS else f"Z{i}"


DIGIT_LEFT = ["AIN", "ZIN"] + [f"P{i}" for i in range(BITS)] + ["+24V", "GND"]
DIGIT_RIGHT = [f"Q{i}" for i in range(BITS)] + ["AOUT", "ZOUT", "NOUT"]


def digit_sheet(project: str, root_uuid: str, sheet_uuid: str, d: int, label: str) -> Sheet:
    """One base-26 relay digit (11 relays).  See README.md section 2 for the circuit."""
    sh = Sheet(project, uid(project, "digitfile", d), "A2", f"Base-26 relay digit {label}",
               [f"{label}: 5 slave relays KS (state, drive CTRL), 5 master relays KM, 1 wrap relay KW",
                "AIN/ZIN = advance / hold rail pair (complementary); AOUT/ZOUT = carry (25 -> 0) rail pair",
                "Q0..Q4 = 24 V when the bit is 1 (binary 0..25); P0..P4 = preset code (24 V only while LOAD)",
                "Generated by gen_counter.py - do not edit by hand"])
    path = f"/{root_uuid}/{sheet_uuid}"
    dc = DiodeCounter(d)
    xs = [g(70.0 + i * 65.0) for i in range(BITS)]

    for i in range(BITS):
        x = xs[i]
        mir_to_m = f"MIRG{i}" if i in (1, 2) else f"MIR{i}"   # bits 1,2: through the KW contact
        # slave: pole 1 = output pair Q/NQ from +24 V, pole 2 = rail Z -> (NC: mirror to master, NO: self-hold)
        relay(sh, path, f"K{d}0{i}", x, 60.0, f"Slave (state) relay bit {i}",
              dict(c1="+24V", no1=f"Q{i}", nc1=f"NQ{i}" if i in (0, 3, 4) else None, c2=z_net(i),
                   nc2=f"MIR{i}", no2=f"SH{i}", coil_p=f"SC{i}", coil_n="GND"))
        # master: pole 1 = carry chain A_i -> A_{i+1} (NC) and load/hold feed G_i (NO), pole 2 = Y_i = AIN & M_i
        relay(sh, path, f"K{d}1{i}", x, 135.0, f"Master relay bit {i} (= NOT slave)",
              dict(c1=a_net(i), nc1=a_net(i + 1), no1=f"G{i}", c2="AIN", nc2=None, no2=f"Y{i}",
                   coil_p=f"MC{i}", coil_n="GND"))
        # diodes under the column
        y = 200.0
        items = [("SH%d" % i, f"SC{i}", "slave self-hold (rail Z)"),
                 (f"G{i}", f"SC{i}", "slave load from master"),
                 (f"P{i}", f"SC{i}", "slave preset from dial code"),
                 (mir_to_m, f"MC{i}", "master mirrors NOT slave"),
                 (f"G{i}", f"MC{i}", "master hold")]
        for a, k, desc in items:
            diode(sh, path, dc.ref(), x, y, k, a, desc)
            y += 7.62
        diode(sh, path, dc.ref(), x, y, f"SC{i}", "GND", "slave coil flyback", V_SWITCH)
        y += 7.62
        diode(sh, path, dc.ref(), x, y, f"MC{i}", "GND", "master coil flyback", V_SWITCH)

        # indicator: Q -> R -> LED -> GND
        ry = 352.0
        sh.symbol(S_R, x, g(ry), f"R{d}{i}", "4.7k", FP_R, "", f"Bit {i} indicator resistor", path)
        sh.pin_net(S_R, x, g(ry), "1", f"Q{i}")
        sh.pin_net(S_R, x, g(ry), "2", f"IND{i}")
        sh.symbol(S_LED, x, g(ry + 20.0), f"DL{d}{i}", "LED", FP_LED, "", f"Bit {i} value indicator", path)
        sh.pin_net(S_LED, x, g(ry + 20.0), "2", f"IND{i}")
        sh.pin_net(S_LED, x, g(ry + 20.0), "1", "GND")

    # rail Z_t = ZIN OR Y_0..Y_{t-1}  (parallel diode OR: one diode drop per node)
    for t in range(1, BITS + 1):
        x = xs[t - 1]
        y = 275.0
        for src in ["ZIN"] + [f"Y{j}" for j in range(t)]:
            diode(sh, path, dc.ref(), x, y, z_net(t), src, f"rail {z_net(t)} OR input {src}")
            y += 7.62
    # N = OR of Y_i = AIN and any master set  (= advance and value <> 25)
    for i in range(BITS):
        diode(sh, path, dc.ref(), 410.0, g(200.0 + 7.62 * i), "NOUT", f"Y{i}", "NOUT = OR of Y")
    # wrap relay KW: energised unless value = 25 (bits 4,3,0 all 1); gates the mirror of bits 1, 2
    relay(sh, path, f"K{d}20", 410.0, 60.0, "Wrap relay: energised unless value = 25",
          dict(c1="MIR1", nc1=None, no1="MIRG1", c2="MIR2", nc2=None, no2="MIRG2", coil_p="KC", coil_n="GND"))
    y = 250.0
    for src in ("NQ0", "NQ3", "NQ4"):
        diode(sh, path, dc.ref(), 410.0, y, "KC", src, f"KW coil OR input {src}")
        y += 7.62
    diode(sh, path, dc.ref(), 410.0, y, "KC", "GND", "KW coil flyback", V_SWITCH)

    sh.text("Row 1: slave relays KS (K%d00..K%d04) and wrap relay KW (K%d20). Row 2: master relays KM (K%d10..K%d14).\n"
            "Under each column: slave feed diodes (hold / load / preset), master feed diodes (mirror / hold),\n"
            "coil flyback diodes. Rails Z1..ZOUT: diode OR of ZIN and Y_j (j below the bit)." % ((d,) * 5), 20, 14)
    sh.text("Rail Z_i = NOT A_i.  A_{i+1} = A_i AND (master_i = 0) (NC contact chain, master = NOT slave = 'bit is 1').\n"
            "Slave i toggles (loads the master) only when A_i is up; otherwise rail Z_i holds it and keeps the mirror running.",
            20, 410)

    # ports
    y0 = g(30.0)
    for r, nm in enumerate(DIGIT_LEFT):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(10.0), y, 180)
        sh.wire(g(10.0), y, g(20.0), y)
        sh.label(nm, g(20.0), y, 0)
    for r, nm in enumerate(DIGIT_RIGHT):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(560.0), y, 0)
        sh.wire(g(560.0), y, g(550.0), y)
        sh.label(nm, g(550.0), y, 180)
    return sh


# ----------------------------------------------------------------------------- buffer sheet (unit board)
def buffer_sheet(project: str, root_uuid: str, sheet_uuid: str) -> Sheet:
    sh = Sheet(project, uid(project, "buffile"), "A3", "a3 / a4 buffer relays (one DPDT per CTRL line)",
               ["10 relays K900..K909: coil = common-board code line, both poles in parallel from +24 V to this unit's CTRL line",
                "a4: L0..L4 -> C4_0..C4_4 (shifter board a4, mounted reversed); a3: T0..T4 -> C3_0..C3_4",
                "Generated by gen_counter.py - do not edit by hand"])
    path = f"/{root_uuid}/{sheet_uuid}"
    n = 0
    dn = 0
    for row, (src, dst, nm) in enumerate((("L", "C4_", "a4"), ("T", "C3_", "a3"))):
        for b in range(BITS):
            x, y = g(80.0 + b * 60.0), g(70.0 + row * 80.0)
            relay(sh, path, f"K9{n:02d}", x, y, f"Buffer relay {nm} bit {b}",
                  dict(c1="+24V", nc1=None, no1=f"{dst}{b}", c2="+24V", nc2=None, no2=f"{dst}{b}",
                       coil_p=f"{src}{b}", coil_n="GND"))
            dn += 1
            diode(sh, path, f"D9{dn:02d}", x, y + 26.0, f"{src}{b}", "GND", f"{nm} bit {b} coil flyback", V_SWITCH)
            n += 1
    y0 = g(30.0)
    left = [f"L{b}" for b in range(BITS)] + [f"T{b}" for b in range(BITS)] + ["+24V", "GND"]
    right = [f"C4_{b}" for b in range(BITS)] + [f"C3_{b}" for b in range(BITS)]
    for r, nm in enumerate(left):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(10.0), y, 180)
        sh.wire(g(10.0), y, g(20.0), y)
        sh.label(nm, g(20.0), y, 0)
    for r, nm in enumerate(right):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(400.0), y, 0)
        sh.wire(g(400.0), y, g(390.0), y)
        sh.label(nm, g(390.0), y, 180)
    return sh


# ----------------------------------------------------------------------------- root sheets
def sheet_block(project, sheet_uuid, root_uuid, name, fname, x, y, w, h, left, right, page) -> str:
    """left/right: lists of (pin name, (x, y)).  Pins are bidirectional."""
    s = (f'\t(sheet\n\t\t(at {f(x)} {f(y)})\n\t\t(size {f(w)} {f(h)})\n\t\t(exclude_from_sim no)\n'
         f'\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(stroke\n\t\t\t(width 0.1524)\n'
         f'\t\t\t(type solid)\n\t\t)\n\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n\t\t(uuid "{sheet_uuid}")\n'
         f'\t\t(property "Sheetname" "{name}"\n\t\t\t(at {f(x)} {f(y - 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
         f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t(thickness 0.254)\n\t\t\t\t\t(bold yes)\n\t\t\t\t)\n'
         f'\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
         f'\t\t(property "Sheetfile" "{fname}"\n\t\t\t(at {f(x)} {f(y + h + 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
         f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n')

    def pin(nm, px, py, ang, just, k):
        return (f'\t\t(pin "{nm}" bidirectional\n\t\t\t(at {f(px)} {f(py)} {ang})\n\t\t\t(uuid "{uid(sheet_uuid, "pin", nm, k)}")\n'
                f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify {just})\n\t\t\t)\n\t\t)\n')
    for nm, (px, py) in left:
        s += pin(nm, px, py, 180, "left", "L")
    for nm, (px, py) in right:
        s += pin(nm, px, py, 0, "right", "R")
    s += (f'\t\t(instances\n\t\t\t(project "{project}"\n\t\t\t\t(path "/{root_uuid}"\n\t\t\t\t\t(page "{page}")\n'
          f'\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
    return s


def add_sheet(root: Sheet, project: str, root_uuid: str, key: str, name: str, fname: str, x: float, y: float,
              left: list[str], right: list[str], netmap: dict, page: int) -> str:
    """Add a sheet block to the root, label every pin with its net (None -> no-connect). Returns sheet uuid."""
    sheet_uuid = uid(project, "sheet", key)
    w = 50.8
    rows = max(len(left), len(right))
    h = 2.54 * (rows + 3)
    lp = [(nm, (x, y + 5.08 + 2.54 * r)) for r, nm in enumerate(left)]
    rp = [(nm, (x + w, y + 5.08 + 2.54 * r)) for r, nm in enumerate(right)]
    root.items.append(sheet_block(project, sheet_uuid, root_uuid, name, fname, x, y, w, h, lp, rp, page))
    for nms, out in ((lp, (-1, 0)), (rp, (1, 0))):
        for nm, (px, py) in nms:
            net = netmap.get(nm, nm)
            if net is None:
                root.noconnect(px, py)
            else:
                root.stub_label(px, py, out, net)
    return sheet_uuid


def connector(root: Sheet, path: str, sym: Sym, ref: str, value: str, nsize: int, x: float, y: float,
              pins: dict[int, str | None], desc: str):
    x, y = g(x), g(y)
    root.symbol(sym, x, y, ref, value, FP_DSUB % nsize, "", desc, path)
    for p in range(1, nsize + 1):
        net = pins.get(p)
        if net is None:
            px, py, _ = root.pin_xy(sym, x, y, str(p))
            root.noconnect(px, py)
        else:
            root.pin_net(sym, x, y, str(p), net, 5.08)


# bus connector (identical pinout on both boards)
BUS_PINS = {1: "A2B", 2: "Z2B", 3: "A1B", 4: "Z1B",
            **{5 + b: f"L{b}" for b in range(BITS)}, **{10 + b: f"T{b}" for b in range(BITS)},
            15: "LOAD", 16: "+24V", 17: "+24V", 18: "+24V", 19: "GND", 20: "GND", 21: "GND"}
BUS_DESC = ("D-sub 25 pins: 1 A2B (a2 advance rail), 2 Z2B (a2 hold rail), 3 A1B, 4 Z1B (a1 rails), 5-9 L0..L4 (a4 = l code), "
            "10-14 T0..T4 (a3 code), 15 LOAD (HOME preset level), 16-18 +24 V, 19-21 0 V, 22-25 reserved")


def common_board(outdir: Path):
    project = "counter_common"
    root_uuid = uid(project, "root")
    root = Sheet(project, root_uuid, "A2", "Relay counter board - COMMON (pL = a4, pM, a3)",
                 ["Project Christopher - relay bombe track, counter board (common part)",
                  "Generated by gen_counter.py - do not edit by hand",
                  "Rules (relay.rs Counters): a4=l every clock; a3 every clock except l wraps; m advances when l wraps; "
                  "rails for a2 (m advances, m not wrapping) and a1 (m wraps) go out on the bus"])
    path0 = f"/{root_uuid}"
    sheets = []
    connector(root, path0, S_DB9, "J1", "CLOCK", 9, 40.0, 60.0,
              {1: "CK", 2: "NCK", 3: "LOAD", 4: "+24V", 5: "+24V", 6: "GND", 7: "GND"},
              "D-sub 9 pins: 1 CK (+24 V during the commit phase), 2 NCK (+24 V otherwise; both 0 V = LOAD/clear state), "
              "3 LOAD, 4-5 +24 V, 6-7 0 V, 8-9 reserved")
    connector(root, path0, S_DB25, "J2", "UNIT_BUS", 25, 40.0, 180.0, BUS_PINS, BUS_DESC)

    specs = [("PL", "pL (a4 = l)", "common_pl.kicad_sch", 1,
              dict(AIN="CK", ZIN="NCK", AOUT="LC", ZOUT="ZL", NOUT="NL", **{f"Q{i}": f"L{i}" for i in range(BITS)})),
             ("PM", "pM", "common_pm.kicad_sch", 2,
              dict(AIN="LC", ZIN="ZL", AOUT="A1B", ZOUT="Z1B", NOUT="A2B", **{f"Q{i}": None for i in range(BITS)})),
             ("A3", "a3 = l - m", "common_a3.kicad_sch", 3,
              dict(AIN="NL", ZIN="Z3I", AOUT=None, ZOUT=None, NOUT=None, **{f"Q{i}": f"T{i}" for i in range(BITS)}))]
    for idx, (key, label, fname, d, nets) in enumerate(specs):
        netmap = dict(nets, **{f"P{i}": "GND" for i in range(BITS)})
        x = 150.0 + idx * 95.0
        su = add_sheet(root, project, root_uuid, key, f"DIGIT_{key}", fname, g(x), g(40.0),
                       DIGIT_LEFT, DIGIT_RIGHT, netmap, idx + 2)
        sheets.append((digit_sheet(project, root_uuid, su, d, label), fname))

    # rule diodes on the root: a3 hold rail = NCK OR (l wraps); a2 hold rail = ZL OR (m wraps)
    dx = 175.0
    diode(root, path0, "D801", dx, 160.0, "Z3I", "NCK", "a3 hold rail OR: NCK")
    diode(root, path0, "D802", dx, 168.0, "Z3I", "LC", "a3 hold rail OR: l wraps")
    diode(root, path0, "D803", dx + 95.0, 160.0, "Z2B", "ZL", "a2 hold rail OR: not (l wraps)")
    diode(root, path0, "D804", dx + 95.0, 168.0, "Z2B", "A1B", "a2 hold rail OR: m wraps")
    root.text("Inter-digit rules (all on rails; masters are stable during CK so no ripple races):\n"
              "  pL: AIN = CK, ZIN = NCK (advances every clock).  LC / ZL = pL carry rail pair (CK and l = 25 / its complement).\n"
              "  pM: AIN = LC (advances when l wraps).  A1B / Z1B = pM carry rails = m wraps -> a1 advance / hold.\n"
              "  pM NOUT = A2B = CK and l wraps and m <> 25 -> a2 advance.  Z2B = ZL OR A1B = its complement -> a2 hold.\n"
              "  a3: AIN = NL = CK and l <> 25 (NOUT of pL); ZIN = Z3I = NCK OR LC.  a3/pL/pM presets tie P0..P4 to 0 V = clear.\n"
              "Bus J2 (to every unit board J1): A2B Z2B A1B Z1B, L0-L4 (a4), T0-T4 (a3), LOAD, +24 V, 0 V.", 150, 130, 1.6)
    return project, root, sheets


def unit_board(outdir: Path):
    project = "counter_unit"
    root_uuid = uid(project, "root")
    root = Sheet(project, root_uuid, "A2", "Relay counter board - PER UNIT (a2, a1, a3/a4 buffers)",
                 ["Project Christopher - relay bombe track, counter board (per unit)",
                  "Generated by gen_counter.py - do not edit by hand",
                  "a1 = r + k, a2 = m - (r + k): preset from the offset dial at HOME (a1 = k, a2 = -k); CTRL outputs to the four shifter boards"])
    path0 = f"/{root_uuid}"
    sheets = []
    connector(root, path0, S_DB25, "J1", "BUS", 25, 40.0, 70.0, BUS_PINS, BUS_DESC)
    dial = {1: "LOAD", 12: "GND", 13: "GND"}
    dial.update({2 + b: f"PA1_{b}" for b in range(BITS)})
    dial.update({7 + b: f"PA2_{b}" for b in range(BITS)})
    connector(root, path0, S_DA15, "J2", "DIAL", 15, 40.0, 220.0, dial,
              "D-sub 15 pins: 1 DIAL_COM (= LOAD level, to the offset dial common), 2-6 a1 preset code (bit0..4, = k), "
              "7-11 a2 preset code (= -k mod 26), 12-13 0 V, 14-15 reserved. Dial contacts join COM to the code pins of the 1 bits")
    for j, (ref, nm, pref) in enumerate((("J3", "CTRL_A1", "C1_"), ("J4", "CTRL_A2", "C2_"),
                                         ("J5", "CTRL_A3", "C3_"), ("J6", "CTRL_A4", "C4_"))):
        pins = {b + 1: f"{pref}{b}" for b in range(BITS)}
        pins.update({6: "GND", 7: "GND"})
        connector(root, path0, S_DB9, ref, nm, 9, 40.0 + 55.0 * j, 330.0, pins,
                  f"D-sub 9 pins: 1-5 = CTRL0..CTRL4 of shifter board {nm[-2:].lower()} (24 V, weights 1,2,4,8,16), 6-7 = 0 V, 8-9 reserved")

    for idx, (key, label, fname, d, a, nm) in enumerate((("A2", "a2 = m - (r + k)", "unit_a2.kicad_sch", 1, "A2", "2"),
                                                          ("A1", "a1 = r + k", "unit_a1.kicad_sch", 2, "A1", "1"))):
        nets = dict(AIN=f"A{nm}B", ZIN=f"Z{nm}B", AOUT=None, ZOUT=None, NOUT=None)
        nets.update({f"P{b}": f"PA{nm}_{b}" for b in range(BITS)})
        nets.update({f"Q{b}": f"C{nm}_{b}" for b in range(BITS)})
        su = add_sheet(root, project, root_uuid, key, f"DIGIT_{key}", fname, g(160.0 + idx * 95.0), g(40.0),
                       DIGIT_LEFT, DIGIT_RIGHT, nets, idx + 2)
        sheets.append((digit_sheet(project, root_uuid, su, d, label), fname))
    left = [f"L{b}" for b in range(BITS)] + [f"T{b}" for b in range(BITS)] + ["+24V", "GND"]
    right = [f"C4_{b}" for b in range(BITS)] + [f"C3_{b}" for b in range(BITS)]
    su = add_sheet(root, project, root_uuid, "BUF", "BUFFERS", "unit_buf.kicad_sch", g(350.0), g(40.0),
                   left, right, {}, 4)
    sheets.append((buffer_sheet(project, root_uuid, su), "unit_buf.kicad_sch"))
    root.text("a2 digit: AIN = A2B, ZIN = Z2B (common board). a1 digit: AIN = A1B, ZIN = Z1B.\n"
              "Preset (HOME): clock lines both 0 V clears the digits, LOAD = 24 V feeds the dial; PA*_b lines load the slave relays.\n"
              "Buffers: the common a3/a4 code lines (T*, L*) drive 10 local relays whose contacts feed this unit's CTRL lines.", 160, 150, 1.6)
    return project, root, sheets


PRO = """{
  "meta": {
    "filename": "%s.kicad_pro",
    "version": 3
  },
  "schematic": {
    "drawing": {
      "default_line_thickness": 6.0,
      "default_text_size": 50.0
    }
  },
  "sheets": [],
  "text_variables": {}
}
"""


def write_project(outdir: Path, project: str, root: Sheet, sheets):
    pro_path = outdir / f"{project}.kicad_pro"
    text = PRO % project
    if pro_path.exists():   # keep the board design rules / netclasses written by gen_counter_pcb.py
        old = json.loads(pro_path.read_text(encoding="utf8"))
        keep = {k: v for k, v in old.items() if k in ("board", "net_settings")}
        if keep:
            text = json.dumps({**json.loads(text), **keep}, indent=2) + "\n"
    pro_path.write_text(text, encoding="utf8", newline="\n")
    (outdir / f"{project}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    for sh, fname in sheets:
        (outdir / fname).write_text(sh.render(), encoding="utf8", newline="\n")
    print(f"wrote {project}.kicad_pro, {project}.kicad_sch and {len(sheets)} sheets to {outdir}")


def main(outdir: Path = HERE):
    for build in (common_board, unit_board):
        project, root, sheets = build(outdir)
        write_project(outdir, project, root, sheets)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
