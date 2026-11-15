#!/usr/bin/env python3
"""Generate the KiCad 10 project + hierarchical schematic of the relay shifter board.

One board = one binary shifter PAIR (outbound half + return half) = 5 stages x 26 DPDT relays.
The two halves share the coils: one DPDT relay per line per stage, pole 1 on the outbound half,
pole 2 on the return half.  See README.md for the conventions and `relay.rs` (shift_binary) for
the golden model.

Stdlib only.  Library symbols are copied from the installed KiCad standard libraries
(env KICAD_SHARE overrides the default install location).

    python gen_shifter.py            # writes shifter_board.kicad_pro/.kicad_sch + stage_*.kicad_sch
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
SYMDIR = KICAD_SHARE / "symbols"

PROJECT = "shifter_board"
N = 26                      # lines
STAGES = [1, 2, 4, 8, 16]   # shift per stage = 2**bit
NS = uuid.UUID("6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0b01")  # fixed namespace -> stable UUIDs

# ----------------------------------------------------------------------------- parts
FP_RELAY = "Relay_THT:Relay_DPDT_Omron_G6S-2"
FP_DSUB37 = ("Connector_Dsub:DSUB-37_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_"
             "MountingHolesOffset11.32mm")
FP_DSUB9 = ("Connector_Dsub:DSUB-9_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_"
            "MountingHolesOffset11.32mm")
FP_DIODE = "Diode_SMD:D_SOD-123"
FP_R = "Resistor_SMD:R_0603_1608Metric"
FP_LED = "LED_SMD:LED_0603_1608Metric"

# G6S-2 pin functions (verified against the library symbol graphics: the armature of pole 1
# rests on pin 3, of pole 2 on pin 10, when the coil is off).
RELAY_PINS = dict(coil_p="1", coil_n="12", c1="4", nc1="3", no1="5", c2="9", nc2="10", no2="8")


def g(v: float) -> float:
    """Snap to the 2.54 mm grid (keeps every pin/wire end on the 1.27 mm connection grid)."""
    return round(round(v / 2.54) * 2.54, 2)


def letter(i: int) -> str:
    return chr(ord("A") + i)


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
            x, y, a, num = float(m[1]), float(m[2]), int(m[3]), m[4]
            self.pins[num] = (x, y, a)
        self.fields = {}
        for m in re.finditer(r'\(property "(Reference|Value)"\s+"[^"]*"\s+\(at ([-\d.]+) ([-\d.]+) ', self.block):
            self.fields[m[1]] = (float(m[2]), float(m[3]))

    def embedded(self) -> str:
        return self.block.replace(f'(symbol "{self.name}"', f'(symbol "{self.lib_id}"', 1)


S_RELAY = Sym("Relay", "G6S-2")
S_DIODE = Sym("Device", "D")
S_R = Sym("Device", "R_US")
S_LED = Sym("Device", "LED")
S_DB37 = Sym("Connector", "DC37_Pins")   # 37-pin D-sub, pins (male); "DC" = shell size C
S_DB9 = Sym("Connector", "DE9_Pins")     # 9-pin D-sub, pins (male); "DE" = shell size E


# ----------------------------------------------------------------------------- emitter
OUT = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}  # pin angle -> outward unit (x right, y up)
LABEL_ANGLE = {(-1, 0): 180, (1, 0): 0, (0, 1): 90, (0, -1): 270}  # outward dir (screen up = +y here)


class Sheet:
    def __init__(self, file_uuid: str, paper: str, title: str, comments=()):
        self.uuid, self.paper, self.title, self.comments = file_uuid, paper, title, list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.points: set[tuple[str, str]] = set()

    # -- primitives
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

    # -- symbols
    def symbol(self, sym: Sym, x, y, ref, value, footprint, datasheet="", desc="", inst_path="", show_value=True):
        self.libs[sym.lib_id] = sym
        x, y = g(x), g(y)
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
        s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{inst_path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

    def pin_xy(self, sym: Sym, x, y, num):
        px, py, a = sym.pins[num]
        return x + px, y - py, OUT[a]

    def stub_label(self, px, py, out, name, length=2.54):
        """wire from pin point (screen coords) outward, then a label at the far end."""
        dx, dy = out
        ex, ey = px + dx * length, py - dy * length  # screen y grows downward
        self.wire(px, py, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[out])

    def pin_net(self, sym, x, y, num, name, length=2.54):
        px, py, out = self.pin_xy(sym, x, y, num)
        self.stub_label(px, py, out, name, length)

    # -- file text
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


# ----------------------------------------------------------------------------- net naming
def chain_net(half: str, k: int, i: int) -> str:
    """Root-level net after k stages (0 = board input, 5 = board output) of half 'O'/'R'."""
    name = {0: "IN", 5: "OUT"}.get(k)
    if name:
        return f"{half}{name}_{letter(i)}"
    return f"{half}{k}_{letter(i)}"


# ----------------------------------------------------------------------------- stage sheet
def stage_sheet(root_uuid: str, sheet_uuid: str, bit: int) -> Sheet:
    shift = 1 << bit
    sh = Sheet(uid("stagefile", bit), "A1", f"Shifter stage {bit + 1}: shift {shift}",
               [f"stage {bit + 1} of 5 (control bit {bit}, weight {shift})",
                f"outbound pole: line i -> i+{shift} when energised; return pole: line i -> i-{shift}",
                "26 DPDT relays, coils in parallel (24 V), one flyback diode per coil"])
    path = f"/{root_uuid}/{sheet_uuid}"
    st = bit + 1

    # relay grid
    for i in range(N):
        col, row = i % 6, i // 6
        x, y = g(140.0 + col * 95.0), g(60.0 + row * 55.0)
        ref = f"K{st}{i + 1:02d}"
        sh.symbol(S_RELAY, x, y, ref, "G6S-2 24VDC", FP_RELAY,
                  "http://omronfs.omron.com/en_US/ecb/products/pdf/en-g6s.pdf",
                  f"DPDT signal relay, stage {st} line {letter(i)}", path)
        P = RELAY_PINS
        sh.pin_net(S_RELAY, x, y, P["c1"], f"OIN_{letter(i)}")
        sh.pin_net(S_RELAY, x, y, P["nc1"], f"OOUT_{letter(i)}")
        sh.pin_net(S_RELAY, x, y, P["no1"], f"OOUT_{letter((i + shift) % N)}")
        sh.pin_net(S_RELAY, x, y, P["c2"], f"RIN_{letter(i)}")
        sh.pin_net(S_RELAY, x, y, P["nc2"], f"ROUT_{letter(i)}")
        sh.pin_net(S_RELAY, x, y, P["no2"], f"ROUT_{letter((i - shift) % N)}")
        sh.pin_net(S_RELAY, x, y, P["coil_p"], "CTRL")
        sh.pin_net(S_RELAY, x, y, P["coil_n"], "GND")
        # flyback diode across the coil (cathode to the + side)
        dref = f"D{st}{i + 1:02d}"
        dx = g(x - 24.0)
        sh.symbol(S_DIODE, dx, y, dref, "1N4148W", FP_DIODE, "", "Coil flyback diode", path, show_value=False)
        sh.pin_net(S_DIODE, dx, y, "1", "CTRL")   # K
        sh.pin_net(S_DIODE, dx, y, "2", "GND")    # A

    # stage indicator: CTRL -> R -> LED -> GND
    rx, ry = g(140.0), g(360.0)
    sh.symbol(S_R, rx, ry, f"R{st}", "4.7k", FP_R, "", "Stage indicator series resistor", path)
    sh.pin_net(S_R, rx, ry, "1", "CTRL")
    sh.pin_net(S_R, rx, ry, "2", "IND")
    lx = g(rx + 40.0)
    sh.symbol(S_LED, lx, ry, f"DL{st}", "LED", FP_LED, "", f"Stage {st} energised indicator", path)
    sh.pin_net(S_LED, lx, ry, "2", "IND")   # A
    sh.pin_net(S_LED, lx, ry, "1", "GND")   # K
    sh.text(f"Stage {st} of 5: control bit {bit} (weight {shift}).\n"
            f"Outbound pole 1: COM=OIN_i, NC->OOUT_i, NO->OOUT_(i+{shift} mod 26).\n"
            f"Return pole 2:   COM=RIN_i, NC->ROUT_i, NO->ROUT_(i-{shift} mod 26).\n"
            f"CTRL = 24 V stage drive, GND = 0 V. Coil pin 1 = +, pin 12 = - (verify polarity).",
            140, 395)

    # ports: left (inputs) and right (outputs)
    y0 = g(30.0)
    names_left = [f"OIN_{letter(i)}" for i in range(N)] + [f"RIN_{letter(i)}" for i in range(N)] + ["CTRL", "GND"]
    for r, nm in enumerate(names_left):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(40.0), y, 180)
        sh.wire(g(40.0), y, g(50.0), y)
        sh.label(nm, g(50.0), y, 0)
    names_right = [f"OOUT_{letter(i)}" for i in range(N)] + [f"ROUT_{letter(i)}" for i in range(N)]
    for r, nm in enumerate(names_right):
        y = y0 + r * 2.54
        sh.hlabel(nm, g(800.0), y, 0)
        sh.wire(g(800.0), y, g(790.0), y)
        sh.label(nm, g(790.0), y, 180)
    return sh


# ----------------------------------------------------------------------------- root sheet
def sheet_block(sheet_uuid, root_uuid, name, fname, x, y, w, h, left, right, top, page) -> str:
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
    for nm, (px, py) in top:
        s += pin(nm, px, py, 90, "right", "T")
    s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "/{root_uuid}"\n\t\t\t\t\t(page "{page}")\n'
          f'\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
    return s


def root_sheet() -> tuple[Sheet, list[tuple[Sheet, str]]]:
    root_uuid = uid("root")
    root = Sheet(root_uuid, "A1", "Relay shifter board (binary 5-stage shifter pair)",
                 ["Project Christopher - relay bombe track, R.1/R.2",
                  "Generated by gen_shifter.py - do not edit by hand",
                  "Outbound: i -> i+a.  Return: i -> i-a.  a = control bit sum (0..25).  Golden model: relay.rs shift_binary"])
    path0 = f"/{root_uuid}"

    # connectors: left = IN side, right = OUT side
    conns = [("J1", "OUTBOUND_IN", "O", 0, 70.0, 110.0),
             ("J2", "RETURN_IN", "R", 0, 70.0, 330.0),
             ("J3", "OUTBOUND_OUT", "O", 5, 770.0, 110.0),
             ("J4", "RETURN_OUT", "R", 5, 770.0, 330.0)]
    for ref, val, half, k, x, y in conns:
        x, y = g(x), g(y)
        root.symbol(S_DB37, x, y, ref, val, FP_DSUB37, "",
                    f"D-sub 37 pins: 1-26 = lines A-Z, 27-29 = 0 V, 30-37 reserved. {val}", path0)
        for i in range(N):
            root.pin_net(S_DB37, x, y, str(i + 1), chain_net(half, k, i), 5.08)
        for p in ("27", "28", "29"):
            root.pin_net(S_DB37, x, y, p, "GND", 5.08)
        for p in range(30, 38):
            px, py, _ = root.pin_xy(S_DB37, x, y, str(p))
            root.noconnect(px, py)

    # control connector
    jx, jy = g(70.0), g(480.0)
    root.symbol(S_DB9, jx, jy, "J5", "CONTROL", FP_DSUB9, "",
                "D-sub 9 pins: 1-5 = CTRL0..CTRL4 (24 V stage drive, weight 1,2,4,8,16), 6-7 = 0 V, 8-9 reserved", path0)
    for b in range(5):
        root.pin_net(S_DB9, jx, jy, str(b + 1), f"CTRL{b}", 5.08)
    for p in ("6", "7"):
        root.pin_net(S_DB9, jx, jy, p, "GND", 5.08)
    for p in ("8", "9"):
        px, py, _ = root.pin_xy(S_DB9, jx, jy, p)
        root.noconnect(px, py)

    # stage sheets
    sheets = []
    sx0, sy0, sw, pitch = g(180.0), g(95.0), 50.8, g(115.0)
    rows = 2 * N
    sh_h = 2.54 * (rows + 4)
    for b in range(5):
        sheet_uuid = uid("sheet", b)
        x = sx0 + b * pitch
        fname = f"stage_{1 << b}.kicad_sch"
        left, right = [], []
        for half, base in (("O", 0), ("R", N)):
            for i in range(N):
                yy = sy0 + 5.08 + 2.54 * (base + i)
                left.append((f"{half}IN_{letter(i)}", (x, yy)))
                right.append((f"{half}OUT_{letter(i)}", (x + sw, yy)))
        top = [("CTRL", (x + 12.7, sy0)), ("GND", (x + 25.4, sy0))]
        root.items.append(sheet_block(sheet_uuid, root_uuid, f"STAGE_{1 << b}", fname, x, sy0, sw, sh_h,
                                      left, right, top, b + 2))
        # root-level nets at the sheet pins
        for nm, (px, py) in left:
            half, i = nm[0], ord(nm[-1]) - 65
            root.stub_label(px, py, (-1, 0), chain_net(half, b, i))
        for nm, (px, py) in right:
            half, i = nm[0], ord(nm[-1]) - 65
            root.stub_label(px, py, (1, 0), chain_net(half, b + 1, i))
        root.stub_label(x + 12.7, sy0, (0, 1), f"CTRL{b}")
        root.stub_label(x + 25.4, sy0, (0, 1), "GND")
        sheets.append((stage_sheet(root_uuid, sheet_uuid, b), fname))
    root.text("Relay shifter board - one shifter pair (outbound + return halves share the coils)\n"
              "Connectors: J1 outbound IN, J2 return IN (one board edge); J3 outbound OUT, J4 return OUT (opposite edge); "
              "J5 control.\nD-sub 37: pins 1-26 = lines A-Z (as unit tester J_A/J_B), 27-29 = 0 V, 30-37 reserved.\n"
              "J5 D-sub 9: 1-5 = CTRL0-4 (24 V, weights 1,2,4,8,16), 6-7 = 0 V.\n"
              "Reverse mounting swaps IN/OUT on both halves: outbound -> i-a, return -> i+a.", 40, 30)
    return root, sheets


# ----------------------------------------------------------------------------- project file
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


def main(outdir: Path = HERE):
    root, stages = root_sheet()
    (outdir / f"{PROJECT}.kicad_pro").write_text(PRO % PROJECT, encoding="utf8", newline="\n")
    (outdir / f"{PROJECT}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    for sh, fname in stages:
        (outdir / fname).write_text(sh.render(), encoding="utf8", newline="\n")
    print(f"wrote {PROJECT}.kicad_pro, {PROJECT}.kicad_sch and {len(stages)} stage sheets to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
