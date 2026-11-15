#!/usr/bin/env python3
"""Generate the KiCad 10 project + hierarchical schematic of the TEST REGISTER board.

Circuit = docs/decisions/0001-test-register-timing.md, option (c): 26 self-holding sense relays K,
blocking diodes, hold bus H, pass relay P, judge contact j, latching stop relay S, CLEAR release.
See README.md for the explanation, the connector pinouts and the verification.

Stdlib only.  Library symbols are copied from the installed KiCad standard libraries
(env KICAD_SHARE overrides the default install location).

    python gen_register.py            # writes test_register.kicad_pro/.kicad_sch + sense_lanes.kicad_sch
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

PROJECT = "test_register"
N = 26
NS = uuid.UUID("6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0c01")  # fixed namespace -> stable UUIDs

# ----------------------------------------------------------------------------- parts
FP_RELAY = "Relay_THT:Relay_DPDT_Omron_G6S-2"
FP_DSUB37 = ("Connector_Dsub:DSUB-37_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_"
             "MountingHolesOffset11.32mm")
FP_DIODE = "Diode_SMD:D_SOD-123"
FP_TVS = "Diode_SMD:D_SMA"
FP_R = "Resistor_SMD:R_0603_1608Metric"
FP_LED = "LED_SMD:LED_0603_1608Metric"
FP_Q = "Package_TO_SOT_SMD:SOT-23"
FP_F1 = "Fuse:Fuse_1812_4532Metric"
FP_F2 = "Fuse:Fuse_2920_7451Metric"
FP_CP = "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm"
FP_TB2 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
FP_TB4 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08_1x04_P5.08mm_Horizontal"
FP_HOLE = "MountingHole:MountingHole_3.2mm_M3"
DS_G6S = "http://omronfs.omron.com/en_US/ecb/products/pdf/en-g6s.pdf"

# G6S-2 pin functions (same as the shifter board): pole 1 = COM 4 / NC 3 / NO 5, pole 2 = COM 9 / NC 10 / NO 8,
# coil + = 1, coil - = 12.
COIL_P, COIL_N = "1", "12"
POLE1 = ("4", "3", "5")
POLE2 = ("9", "10", "8")

# nets that cross between the root sheet and the lane sheet (and the rails): global labels
GLOBAL = ({f"L_{chr(65 + i)}" for i in range(N)} | {f"LAMP_{chr(65 + i)}" for i in range(N)}
          | {"H", "+24V", "GND", "PN", "LAMPGND"})


def g(v: float) -> float:
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
S_DB37 = Sym("Connector", "DC37_Pins")
S_Q = Sym("Transistor_FET", "Q_NMOS_GSD")     # pins 1 = G, 2 = S, 3 = D (AO3400A, SOT-23)
S_FUSE = Sym("Device", "Polyfuse")
S_TVS = Sym("Device", "D_TVS")
S_ZENER = Sym("Device", "D_Zener")
S_CP = Sym("Device", "C_Polarized")
S_TB2 = Sym("Connector", "Screw_Terminal_01x02")
S_TB4 = Sym("Connector", "Screw_Terminal_01x04")
S_HOLE = Sym("Mechanical", "MountingHole")

OUT = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}
LABEL_ANGLE = {(-1, 0): 180, (1, 0): 0, (0, 1): 90, (0, -1): 270}


class Sheet:
    def __init__(self, file_uuid: str, paper: str, title: str, comments=(), inst_path=""):
        self.uuid, self.paper, self.title, self.comments = file_uuid, paper, title, list(comments)
        self.inst_path = inst_path
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
        if name in GLOBAL:
            just = "right" if angle in (180, 270) else "left"
            self.items.append(
                f'\t(global_label "{name}"\n\t\t(shape bidirectional)\n\t\t(at {f(x)} {f(y)} {angle})\n'
                f'\t\t(fields_autoplaced yes)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n'
                f'\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{self._u("gl")}")\n'
                f'\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n\t\t\t(at {f(x)} {f(y)} 0)\n'
                f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n\t)')
            return
        self.items.append(
            f'\t(label "{name}"\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t)\n\t\t\t(justify {self._just(angle)})\n\t\t)\n\t\t(uuid "{self._u("l")}")\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def symbol(self, sym: Sym, x, y, ref, value, footprint, datasheet="", desc="", show_value=True):
        self.libs[sym.lib_id] = sym
        x, y = g(x), g(y)

        def prop(name, val, key=None, hide=False):
            ox, oy = sym.fields.get(key or name, (0.0, 0.0))
            h = "\n\t\t\t(hide yes)" if hide else ""
            pos = f"{f(x + ox)} {f(y - oy)}" if key else f"{f(x)} {f(y)}"
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {pos} 0){h}\n\t\t\t(effects\n\t\t\t\t(font\n'
                    f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')
        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom {"no" if sym is S_HOLE else "yes"})\n\t\t(on_board yes)\n\t\t(dnp no)\n'
             f'\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", ref, "Reference")
        s += prop("Value", value, "Value", hide=not show_value)
        s += prop("Footprint", footprint, hide=True)
        s += prop("Datasheet", datasheet, hide=True)
        s += prop("Description", desc, hide=True)
        for num in sym.pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.inst_path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

    def pin_xy(self, sym: Sym, x, y, num):
        px, py, a = sym.pins[num]
        return g(x) + px, g(y) - py, OUT[a]

    def pin_net(self, sym, x, y, num, name, length=2.54):
        px, py, (dx, dy) = self.pin_xy(sym, x, y, num)
        ex, ey = px + dx * length, py - dy * length
        self.wire(px, py, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[(dx, dy)])

    def part(self, sym, x, y, ref, value, footprint, nets: dict, datasheet="", desc="", length=2.54,
             show_value=True):
        """Place a symbol; `nets` maps pin number -> net name; every other pin gets a no-connect flag."""
        self.symbol(sym, x, y, ref, value, footprint, datasheet, desc, show_value)
        for num in sym.pins:
            if num in nets:
                self.pin_net(sym, x, y, num, nets[num], length)
            else:
                px, py, _ = self.pin_xy(sym, x, y, num)
                self.noconnect(px, py)
        missing = set(nets) - set(sym.pins)
        assert not missing, (ref, missing)

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


def relay(sh: Sheet, x, y, ref, nets, desc):
    sh.part(S_RELAY, x, y, ref, "G6S-2 DC24", FP_RELAY, nets, DS_G6S, desc)


def rel_nets(coil_p, coil_n="GND", p1=None, p2=None):
    """p1 / p2 = (com, nc, no) net names (None = leave the pin unconnected)."""
    nets = {COIL_P: coil_p, COIL_N: coil_n}
    for pole, pins in ((p1, POLE1), (p2, POLE2)):
        if pole:
            for net, pin in zip(pole, pins):
                if net:
                    nets[pin] = net
    return nets


# ----------------------------------------------------------------------------- lane sheet
def lanes_sheet(root_uuid: str, sheet_uuid: str) -> Sheet:
    sh = Sheet(uid("lanesfile"), "A1", "Sense lanes K1..K26 (self-holding sense relay + blocking diode + lamp driver)",
               ["26 identical lanes. Lane i: line L_i -> blocking diode -> node N_i -> coil K_i (+ flyback diode).",
                "K_i pole 1 = hold contact (H -> N_i). Pole 2 = chain contact (series chain H ... PN -> pass relay P).",
                "Lamp driver: N_i -> divider -> MOSFET Q_i; drain LAMP_i sinks the display slave relay; source LAMPGND = switched by S."],
               f"/{root_uuid}/{sheet_uuid}")
    cols, pitch_x, pitch_y = 4, 140.0, 66.0
    for i in range(N):
        L = letter(i)
        x, y = g(70.0 + (i % cols) * pitch_x), g(50.0 + (i // cols) * pitch_y)
        n, gt, la = f"N_{L}", f"G_{L}", f"LA_{L}"
        chain_in = "H" if i == 0 else f"CH{i}"
        chain_out = "PN" if i == N - 1 else f"CH{i + 1}"
        relay(sh, x, y, f"K{i + 1}", rel_nets(n, "GND", (f"H", None, n), (chain_in, None, chain_out)),
              f"Sense relay K{i + 1}, letter {L}: hold contact + chain contact")
        sh.part(S_DIODE, x - 34, y - 12, f"D{i + 1}", "1N4148W", FP_DIODE, {"1": n, "2": f"L_{L}"},
                desc=f"Blocking diode, line {L} -> K{i + 1} (anode on the test-register line)", show_value=False)
        sh.part(S_DIODE, x - 34, y + 10, f"DF{i + 1}", "1N4148W", FP_DIODE, {"1": n, "2": "GND"},
                desc=f"Coil flyback diode K{i + 1}", show_value=False)
        sh.part(S_R, x + 32, y - 16, f"RA{i + 1}", "33k", FP_R, {"1": n, "2": gt},
                desc="Gate divider top", show_value=False)
        sh.part(S_R, x + 40, y - 16, f"RB{i + 1}", "22k", FP_R, {"1": gt, "2": "GND"},
                desc="Gate divider bottom (keeps Vgs below 12 V)", show_value=False)
        sh.part(S_Q, x + 54, y - 16, f"Q{i + 1}", "AO3400A", FP_Q, {"1": gt, "2": "LAMPGND", "3": f"LAMP_{L}"},
                "http://www.aosmd.com/pdfs/datasheet/AO3400A.pdf",
                f"Lamp driver {L}: sinks the display slave relay coil when K{i + 1} is latched and S is operated",
                show_value=False)
        sh.part(S_R, x + 32, y + 12, f"RL{i + 1}", "3.3k", FP_R, {"1": n, "2": la},
                desc="Lamp LED series resistor", show_value=False)
        sh.part(S_LED, x + 48, y + 12, f"DL{i + 1}", "LED", FP_LED, {"1": "LAMPGND", "2": la},
                desc=f"On-board lamp {L} (lit while stopped and K{i + 1} latched)", show_value=False)
    sh.text("Lamp polarity: LAMP_i is pulled low (display slave relay energised) for letters whose sense relay K is LATCHED\n"
            "(= wire live at the instant of STROBE) while S is operated.  Dropped (dead) letters = stecker candidates are the\n"
            "letters whose display relay is NOT energised; choose lit/dark at the display with the slave relay NO/NC contact.",
            40, 20)
    return sh


# ----------------------------------------------------------------------------- root sheet
def sheet_block(sheet_uuid, root_uuid, name, fname, x, y, w, h, page) -> str:
    return (f'\t(sheet\n\t\t(at {f(x)} {f(y)})\n\t\t(size {f(w)} {f(h)})\n\t\t(exclude_from_sim no)\n'
            f'\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(stroke\n\t\t\t(width 0.1524)\n'
            f'\t\t\t(type solid)\n\t\t)\n\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n\t\t(uuid "{sheet_uuid}")\n'
            f'\t\t(property "Sheetname" "{name}"\n\t\t\t(at {f(x)} {f(y - 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t(thickness 0.254)\n\t\t\t\t\t(bold yes)\n\t\t\t\t)\n'
            f'\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
            f'\t\t(property "Sheetfile" "{fname}"\n\t\t\t(at {f(x)} {f(y + h + 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n'
            f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "/{root_uuid}"\n\t\t\t\t\t(page "{page}")\n'
            f'\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')


# J2 (BANK) pin map; same as the unit tester's J_C where it overlaps (README 3.4), plus the proposed pin 36
J2_MAP = {1: "STROBE", 2: "RUN", 3: "STOPPED", 34: "JUDGE", 35: "CLEAR", 36: "STOP_LATCH"}
J2_GND = list(range(28, 34))


def root_sheet(root_uuid: str, lanes_uuid: str) -> Sheet:
    sh = Sheet(root_uuid, "A1", "Test register (26 self-holding sense relays, pass relay P, latching stop relay S)",
               ["Project Christopher - common part, both machines.  Circuit: docs/decisions/0001-test-register-timing.md",
                "Generated by gen_register.py - do not edit by hand",
                "Stop iff NOT all 26 sense relays latched at JUDGE time (pass relay P released)."],
               f"/{root_uuid}")

    # ---- connectors (left column)
    def dsub(ref, val, x, y, mapping, gnd, desc):
        nets = {}
        for p in range(1, 38):
            if p in mapping:
                nets[str(p)] = mapping[p]
            elif p in gnd:
                nets[str(p)] = "GND"
        sh.part(S_DB37, x, y, ref, val, FP_DSUB37, nets, "", desc, length=5.08)

    dsub("J1", "TEST_REG_IN", 70, 80, {i + 1: f"L_{letter(i)}" for i in range(N)}, [27, 28, 29],
         "D-sub 37 pins: 1-26 = lines A-Z from the menu panel (test register cable), 27-29 = 0 V, 30-37 reserved")
    dsub("J2", "BANK", 70, 190, J2_MAP, J2_GND,
         "D-sub 37 pins (unit tester J_C map): 1 STROBE, 2 RUN, 3 STOPPED, 34 JUDGE, 35 CLEAR, 36 STOP_LATCH (proposed), 28-33 0 V")
    dsub("J3", "DISPLAY", 70, 300, {**{i + 1: f"LAMP_{letter(i)}" for i in range(N)}, 27: "LAMP_STOP",
                                    28: "+24VD", 29: "+24VD", 30: "+24VD"}, [31, 32, 33, 34, 35, 36, 37],
         "D-sub 37 pins: 1-26 = LAMP_A..Z (low = display slave relay energised), 27 = LAMP_STOP, 28-30 = +24 V (fused), 31-37 = 0 V")
    sh.part(S_TB2, 70, 400, "J4", "24V_IN", FP_TB2, {"1": "VIN", "2": "GND"}, "", "Power in: 1 = +24 V, 2 = 0 V",
            length=5.08)
    sh.part(S_TB2, 70, 420, "J5", "TEST_OUT", FP_TB2, {"1": "TEST", "2": "GND"}, "",
            "Test voltage lead to the menu panel searching plug: 1 = TEST (+24 V while STROBE), 2 = 0 V", length=5.08)
    sh.part(S_TB4, 70, 445, "J6", "PANEL", FP_TB4, {"1": "+24V", "2": "START_SW", "3": "RELEASE_SW", "4": "GND"}, "",
            "Front panel: 1 = +24 V (switch common), 2 = START switch return (normally open, maintained), "
            "3 = RELEASE push-button return (normally open), 4 = 0 V", length=5.08)

    # ---- power
    sh.part(S_FUSE, 190, 400, "F1", "PTC 1.1A hold", FP_F1, {"1": "VIN", "2": "+24V"}, "", "Logic supply PTC fuse")
    sh.part(S_FUSE, 220, 400, "F2", "PTC 2A hold", FP_F2, {"1": "VIN", "2": "+24VD"}, "", "Display supply PTC fuse")
    sh.part(S_CP, 250, 400, "C1", "100u/35V", FP_CP, {"1": "+24V", "2": "GND"}, "", "Bulk capacitor, logic rail")
    sh.part(S_CP, 280, 400, "C2", "100u/35V", FP_CP, {"1": "+24VD", "2": "GND"}, "", "Bulk capacitor, display rail")
    sh.part(S_TVS, 310, 400, "DV1", "SMAJ33A", FP_TVS, {"1": "VIN", "2": "GND"}, "", "Transient suppressor (cathode on +24 V)",
            show_value=False)
    for k, (hx, hy) in enumerate([(340, 400), (360, 400), (380, 400), (400, 400)], 1):
        sh.part(S_HOLE, hx, hy, f"H{k}", "M3", FP_HOLE, {}, "", "Mounting hole", show_value=False)

    # ---- timing relays: STROBE (KT), JUDGE (KJ), CLEAR (KC)
    x0, y0, dx = 190, 90, 80
    relay(sh, x0, y0, "K27", rel_nets("STROBE", "GND", ("TESTSRC", None, "TEST"), ("TESTSRC", None, "TEST")),
          "STROBE relay: contact 'STROBE' (both poles in parallel) puts the test voltage on TEST")
    relay(sh, x0 + dx, y0, "K28", rel_nets("JUDGE", "GND", ("+24V", None, "JNODE"), ("+24V", None, "JNODE")),
          "JUDGE relay: contact j (both poles in parallel) feeds the stop-relay path")
    relay(sh, x0 + 2 * dx, y0, "K29", rel_nets("CLEAR", "GND", ("HSUP", "H", None), ("HSUP", "H", None)),
          "CLEAR relay: contact r, normally closed (both poles in parallel), feeds the hold bus H from HSUP; opens during CLEAR")
    # ---- pass relay P
    relay(sh, x0 + 3 * dx, y0, "K30", rel_nets("PN", "GND", ("H", None, "PH"), ("JNODE", "SN", None)),
          "Pass relay P: pole 1 self-hold H->PH (diode D33 to PN: stops the S2 lock supply at PN from feeding H through p); pole 2 NC = judge path j -> S coil (open when P is operated)")
    # ---- stop relay S (three relays, coils in parallel, 6 poles)
    relay(sh, x0 + 4 * dx, y0, "K31", rel_nets("SN", "GND", ("HSUP", None, "H"), ("HSUP", None, "SN")),
          "Stop relay S1: pole 1 bypasses CLEAR contact r (H stays alive); pole 2 self-hold (supply HSUP, cut by the RELEASE relay)")
    relay(sh, x0 + 5 * dx, y0, "K32", rel_nets("SN", "GND", ("HSUP", None, "PN"), ("+24V", "TESTSRC", None)),
          "Stop relay S2: pole 1 keeps P operated (restart lock, supply HSUP); pole 2 NC cuts the test voltage while stopped")
    relay(sh, x0 + 6 * dx, y0, "K33", rel_nets("SN", "GND", ("START_SW", "RUN_A", None), ("+24V", None, "STOP_LATCH")),
          "Stop relay S3: pole 1 NC breaks RUN (RUN_A, then through the RELEASE relay); pole 2 NO reports STOP_LATCH to the bank")
    relay(sh, x0 + 7 * dx, y0, "K34", rel_nets("RELEASE_SW", "GND", ("RUN_A", "RUN", None), ("+24V", "HSUP", None)),
          "RELEASE relay R: pole 1 NC in the RUN path; pole 2 NC supplies HSUP (hold-bus and S self-hold supply). "
          "Operated by the front-panel RELEASE push button (normally open): unlatches S, clears all K and P, holds RUN low")
    sh.part(S_DIODE, x0 + 3 * dx + 30, y0 + 34, "D33", "1N4148W", FP_DIODE, {"1": "PN", "2": "PH"},
            desc="P self-hold diode: stops PN (fed by the S2 lock or the chain) from feeding the hold bus H through the p contact",
            show_value=False)
    # coil flyback diodes; the three timing relays use diode + zener (fast release), the others a plain diode
    for k, (ref, net) in enumerate([("D27", "STROBE"), ("D28", "JUDGE"), ("D29", "CLEAR")]):
        xx = x0 + k * dx
        sh.part(S_DIODE, xx, y0 + 34, ref, "1N4148W", FP_DIODE, {"1": f"X{k + 1}", "2": "GND"},
                desc=f"Coil clamp diode ({net}), in series with a zener", show_value=False)
        sh.part(S_ZENER, xx, y0 + 46, f"DZ{27 + k}", "BZT52C30", FP_DIODE, {"1": f"X{k + 1}", "2": net},
                desc=f"Coil clamp zener 30 V ({net}): releases faster than a plain flyback diode", show_value=False)
    for k, (ref, net) in ((3, ("D30", "PN")), (4, ("D31", "SN")), (7, ("D32", "RELEASE_SW"))):
        sh.part(S_DIODE, x0 + k * dx, y0 + 34, ref, "1N4148W", FP_DIODE, {"1": net, "2": "GND"},
                desc=f"Coil flyback diode ({net})", show_value=False)

    # ---- bank-signal indicators (STROBE / JUDGE / CLEAR / STOPPED), pulldowns
    for k, net in enumerate(["STROBE", "JUDGE", "CLEAR", "STOPPED"]):
        xx = 190 + k * 40
        sh.part(S_R, xx, 170, f"RI{k + 1}", "4.7k", FP_R, {"1": net, "2": f"IND{k + 1}"},
                desc=f"Indicator resistor ({net})", show_value=False)
        sh.part(S_LED, xx, 190, f"DI{k + 1}", "LED", FP_LED, {"1": "GND", "2": f"IND{k + 1}"},
                desc=f"Indicator LED ({net})", show_value=False)
    sh.part(S_R, 190, 220, "RR1", "10k", FP_R, {"1": "RUN", "2": "GND"}, desc="RUN pull-down", show_value=False)
    sh.part(S_R, 230, 220, "RR2", "100k", FP_R, {"1": "START_SW", "2": "GND"}, desc="START pull-down", show_value=False)
    sh.part(S_R, 270, 220, "RR3", "100k", FP_R, {"1": "RELEASE_SW", "2": "GND"}, desc="RELEASE pull-down", show_value=False)

    # ---- stop-bus MOSFET (LAMPGND) and stop lamp driver
    sh.part(S_R, 190, 270, "RS1", "33k", FP_R, {"1": "SN", "2": "SG"}, desc="Stop gate divider top", show_value=False)
    sh.part(S_R, 230, 270, "RS2", "22k", FP_R, {"1": "SG", "2": "GND"}, desc="Stop gate divider bottom", show_value=False)
    sh.part(S_Q, 290, 270, "Q27", "AO3400A", FP_Q, {"1": "SG", "2": "GND", "3": "LAMPGND"},
            "http://www.aosmd.com/pdfs/datasheet/AO3400A.pdf",
            "LAMPGND switch: connects the lamp-driver sources to 0 V while S is operated", show_value=False)
    sh.part(S_Q, 350, 270, "Q28", "AO3400A", FP_Q, {"1": "SG", "2": "GND", "3": "LAMP_STOP"},
            "http://www.aosmd.com/pdfs/datasheet/AO3400A.pdf", "Stop lamp driver", show_value=False)
    sh.part(S_R, 190, 320, "RS3", "3.3k", FP_R, {"1": "SN", "2": "SL"}, desc="Stop LED resistor", show_value=False)
    sh.part(S_LED, 230, 320, "DL27", "LED red", FP_LED, {"1": "GND", "2": "SL"}, desc="STOP indicator (S operated)",
            show_value=False)

    sh.items.append(sheet_block(lanes_uuid, root_uuid, "SENSE_LANES", "sense_lanes.kicad_sch", 450, 200, 60, 20, 2))
    sh.text("TEST REGISTER (decision 0001, option c)\n"
            "K1..K26 sense relays (lanes sheet), K27 STROBE relay, K28 JUDGE relay, K29 CLEAR relay (contact r, NC),\n"
            "K30 pass relay P, K31..K33 stop relay S (three relays, coils in parallel), K34 RELEASE relay.  All G6S-2 DPDT signal relays.\n"
            "J1 = 26-wire test register cable (from the menu panel), J2 = bank control, J3 = display (lamp panel),\n"
            "J4 = 24 V in, J5 = test voltage lead, J6 = front panel (START, RELEASE).  See README.md.",
            40, 18)
    return sh


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


def build():
    root_uuid = uid("root")
    lanes_uuid = uid("lanessheet")
    root = root_sheet(root_uuid, lanes_uuid)
    lanes = lanes_sheet(root_uuid, lanes_uuid)
    return root, lanes


def main(outdir: Path = HERE):
    root, lanes = build()
    (outdir / f"{PROJECT}.kicad_pro").write_text(PRO % PROJECT, encoding="utf8", newline="\n")
    (outdir / f"{PROJECT}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    (outdir / "sense_lanes.kicad_sch").write_text(lanes.render(), encoding="utf8", newline="\n")
    print(f"wrote {PROJECT}.kicad_pro, {PROJECT}.kicad_sch, sense_lanes.kicad_sch to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
