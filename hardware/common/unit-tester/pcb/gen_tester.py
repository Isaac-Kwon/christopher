#!/usr/bin/env python3
"""Generate the KiCad 10 project + hierarchical schematic of the scrambler unit tester main board.

Design: hardware/common/unit-tester/README.md.  Pin map: firmware/hal_pico.py (checked by
check_netlist.py).  Stdlib only; library symbols are copied from the installed KiCad libraries
(env KICAD_SHARE overrides the install location).

    python gen_tester.py        # writes unit_tester.kicad_pro/.kicad_sch + sub-sheets next to this file

Sheets (all generated, do not edit by hand):
    power        24 V in, PTC fuses, TVS, 24 V -> 5 V field rail
    mcu          Raspberry Pi Pico (RP2040/RP2350), 3.3 V rail
    isolation    ISO7340C/FC digital isolators (595 control + RUN/HOME), STROBE optocoupler
    shift_out    7 x 74HC595 (56 bits, field side)
    shift_in     10 x 74HC165 (80 bits, MCU side)
    frontend_g0..g6   one TBD62783A group: driver + 1k + 2.2k sense + TLP291-4 for 8 nodes
    aux_sense    22 auxiliary inputs (POS, STOPPED, AT_HOME, END, ESTOP, ILOCK, JUDGE, CLEAR)
    connectors   J_A, J_B, J_C (DC37 sockets), ADC-option headers
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from kilib import LABEL_ANGLE, OUT, Sym, rot

HERE = Path(__file__).resolve().parent
PROJECT = "unit_tester"
NS = uuid.UUID("3b7d5a52-9a41-4d6f-8f07-5b6c1d0c7e01")

# ----------------------------------------------------------------------------- spec tables
N_NODES = 52          # 26 A + 26 B
SENSE_BITS = 80       # 10 x 74HC165
DRIVE_BITS = 56       # 7 x 74HC595
N_OPTO = 19           # TLP291-4 packages; package p carries sense bits 4p..4p+3

# auxiliary 165 inputs: bit -> (field-side net, J_C pin)
AUX = [(52 + k, f"POS{k}_IN", 7 + k) for k in range(15)] + [
    (67, "STOPPED_IN", 3), (68, "AT_HOME_IN", 5), (69, "END_IN", 6),
    (70, "ESTOP_RET", 23), (71, "ILOCK_RET", 25), (72, "JUDGE_IN", 34), (73, "CLEAR_IN", 35)]

JC_PINS = {1: "STROBE_IN", 2: "RUN_OUT", 3: "STOPPED_IN", 4: "HOME_OUT", 5: "AT_HOME_IN", 6: "END_IN",
           22: "ESTOP_SRC", 23: "ESTOP_RET", 24: "ILOCK_SRC", 25: "ILOCK_RET",
           26: "+24V_BANK", 27: "+24V_BANK", 34: "JUDGE_IN", 35: "CLEAR_IN"}
for _k in range(15):
    JC_PINS[7 + _k] = f"POS{_k}_IN"
for _p in range(28, 34):
    JC_PINS[_p] = "GND_F"
JC_NC = (36, 37)

# Pico pin numbers by GPIO (module pinout)
PICO_PIN = {0: 1, 1: 2, 2: 4, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14, 11: 15, 12: 16, 13: 17,
            14: 19, 15: 20, 16: 21, 17: 22, 18: 24, 19: 25, 20: 26, 21: 27, 22: 29, 26: 31, 27: 32, 28: 34}
# GPIO -> net (hal_pico.py; MUX_S3 moved GP23 -> GP27, GP23 is not on the Pico header)
PICO_NETS = {2: "STROBE_M", 3: "RUN_M", 4: "HOME_M", 6: "MUX_EN0", 7: "MUX_EN1", 8: "MUX_EN2", 9: "MUX_EN3",
             10: "SCK_165", 12: "MISO_165", 13: "LD_165", 16: "OE_M", 17: "RCLK_M", 18: "SCK_M", 19: "MOSI_M",
             20: "MUX_S0", 21: "MUX_S1", 22: "MUX_S2", 27: "MUX_S3", 26: "ADC_IN"}

# footprints
FP = dict(
    pico="Module:RaspberryPi_Pico_Common_THT",
    dsub="Connector_Dsub:DSUB-37_Socket_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm",
    so16="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    so18w="Package_SO:SOIC-18W_7.5x11.6mm_P1.27mm",
    tlp="Package_SO:SOIC-16_4.55x10.3mm_P1.27mm",
    iso="Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm",
    dip8="Package_DIP:DIP-8_W7.62mm",
    sot235="Package_TO_SOT_SMD:SOT-23-5",
    r0603="Resistor_SMD:R_0603_1608Metric",
    r2512="Resistor_SMD:R_2512_6332Metric",
    d123="Diode_SMD:D_SOD-123",
    dsma="Diode_SMD:D_SMA",
    c0603="Capacitor_SMD:C_0603_1608Metric",
    c0805="Capacitor_SMD:C_0805_2012Metric",
    c1206="Capacitor_SMD:C_1206_3216Metric",
    cp="Capacitor_SMD:CP_Elec_8x10",
    f1812="Fuse:Fuse_1812_4532Metric",
    f2920="Fuse:Fuse_2920_7451Metric",
    tsr="Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT",
    term="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
    sj="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    led="LED_SMD:LED_0603_1608Metric",
    sw="Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92",
    hdr2x6="Connector_PinHeader_2.54mm:PinHeader_2x06_P2.54mm_Vertical",
    hdr2x27="Connector_PinHeader_2.54mm:PinHeader_2x27_P2.54mm_Vertical",
    mh="MountingHole:MountingHole_3.2mm_M3",
)

S_PICO = Sym("MCU_Module", "RaspberryPi_Pico")
S_595 = Sym("74xx", "74HC595")
S_165 = Sym("74xx", "74HC165")
S_TBD = Sym("Transistor_Array", "TBD62783A")
S_TLP = Sym("Isolator", "TLP291-4")
S_ISO = Sym("Isolator", "ISO7340C")
S_ISOF = Sym("Isolator", "ISO7340FC")
S_6N137 = Sym("Isolator", "6N137")
S_INV = Sym("74xGxx", "74LVC1G14")
S_R = Sym("Device", "R_US")
S_C = Sym("Device", "C")
S_CP = Sym("Device", "C_Polarized")
S_D = Sym("Device", "D")
S_LED = Sym("Device", "LED")
S_PTC = Sym("Device", "Polyfuse")
S_TVS = Sym("Diode", "SMAJ33A")
S_TSR = Sym("Converter_DCDC", "TSR_1-2450")
S_TERM = Sym("Connector", "Screw_Terminal_01x02")
S_SJ = Sym("Jumper", "SolderJumper_2_Open")
S_SW = Sym("Switch", "SW_Push")
S_DB37 = Sym("Connector", "DC37_Socket")
S_H6 = Sym("Connector_Generic", "Conn_02x06_Odd_Even")
S_H27 = Sym("Connector_Generic", "Conn_02x27_Odd_Even")
S_FLAG = Sym("power", "PWR_FLAG")
S_MH = Sym("Mechanical", "MountingHole")


_FLG = 0


def g(v: float) -> float:
    return round(round(v / 2.54) * 2.54, 2)


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def G(name: str) -> str:
    """mark a net name as global"""
    return "@" + name


# ----------------------------------------------------------------------------- sheet emitter
class Sheet:
    def __init__(self, key: str, title: str, comments=(), paper="A1"):
        self.key = key
        self.uuid = uid("file", key)
        self.title, self.paper, self.comments = title, paper, list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.path = ""          # instance path, set by the root builder
        self.nflag = 0

    def _u(self, kind):
        self.n += 1
        return uid(self.uuid, kind, self.n)

    def wire(self, x1, y1, x2, y2):
        self.items.append(
            f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self._u("w")}")\n\t)')

    def label(self, name, x, y, angle, is_global=False):
        just = "right" if angle in (180, 270) else "left"
        if is_global:
            self.items.append(
                f'\t(global_label "{name}"\n\t\t(shape passive)\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n'
                f'\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                f'\t\t(uuid "{self._u("gl")}")\n\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
                f'\t\t\t(at {f(x)} {f(y)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n'
                f'\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n\t)')
        else:
            self.items.append(
                f'\t(label "{name}"\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n\t\t\t(font\n'
                f'\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just} bottom)\n\t\t)\n'
                f'\t\t(uuid "{self._u("l")}")\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def net_at(self, x, y, out, name, length=5.08):
        """stub wire from (x, y) (screen coords) along `out`, then a label."""
        is_global = name.startswith("@")
        name = name.lstrip("@")
        dx, dy = out
        ex, ey = x + dx * length, y - dy * length
        self.wire(x, y, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[out], is_global)

    def place(self, sym: Sym, x, y, ref, value, fp, nets=None, unit=1, rotation=0, nc=(), dnp=False,
              desc="", ds="", in_bom=True, on_board=True, show_value=True, stub=5.08, extra=None):
        nets = nets or {}
        x, y = g(x), g(y)
        self.libs[sym.lib_id] = sym
        pins = sym.pins(unit)
        ox, oy = sym.fields.get("Reference", (0.0, 0.0))
        vx, vy = sym.fields.get("Value", (0.0, 0.0))

        def prop(name, val, pos, hide=False):
            h = "\n\t\t\t(hide yes)" if hide else ""
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {f(pos[0])} {f(pos[1])} 0){h}\n\t\t\t(effects\n'
                    f'\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')

        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} {rotation})\n\t\t(unit {unit})\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom {"yes" if in_bom else "no"})\n'
             f'\t\t(on_board {"yes" if on_board else "no"})\n\t\t(dnp {"yes" if dnp else "no"})\n'
             f'\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", ref, (x + ox, y - oy))
        s += prop("Value", value, (x + vx, y - vy), hide=not show_value)
        s += prop("Footprint", fp, (x, y), hide=True)
        s += prop("Datasheet", ds, (x, y), hide=True)
        s += prop("Description", desc, (x, y), hide=True)
        for k, v in (extra or {}).items():
            s += prop(k, v, (x, y), hide=True)
        for num in pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)
        # pin connections
        seen: dict[tuple, str] = {}
        nets = {str(k): v for k, v in nets.items()}
        nc = {str(k) for k in nc}
        for num, (px, py, a, pname, ptype) in pins.items():
            rx, ry = rot(px, py, rotation)
            o = OUT[a]
            o = tuple(int(round(c)) for c in rot(o[0], o[1], rotation))
            sx, sy = round(x + rx, 3), round(y - ry, 3)
            key = (sx, sy)
            if num in nc:
                if seen.get(key) not in (None, "<nc>"):
                    raise SystemExit(f"{ref}.{num}: NC on a net point")
                if key not in seen:
                    self.noconnect(sx, sy)
                    seen[key] = "<nc>"
            elif num in nets:
                if key in seen:
                    if seen[key] != nets[num]:
                        raise SystemExit(f"{ref}.{num}: stacked pins with different nets {seen[key]} / {nets[num]}")
                    continue
                seen[key] = nets[num]
                self.net_at(sx, sy, o, nets[num], stub)
            else:
                raise SystemExit(f"{ref} unit {unit}: pin {num} ({pname}) has no net / nc")
        extra_nets = set(nets) - set(pins)
        if extra_nets:
            raise SystemExit(f"{ref}: nets given for unknown pins {extra_nets}")

    def flag(self, net, x, y):
        """PWR_FLAG on a (global) net at (x, y)."""
        self.nflag += 1
        x, y = g(x), g(y)
        self.label(net, x, y, 0, True)
        self.libs[S_FLAG.lib_id] = S_FLAG
        global _FLG
        _FLG += 1
        ref = f"#FLG{_FLG:04d}"
        s = (f'\t(symbol\n\t\t(lib_id "{S_FLAG.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board no)\n\t\t(dnp no)\n'
             f'\t\t(uuid "{self._u("s")}")\n'
             f'\t\t(property "Reference" "{ref}"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
             f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
             f'\t\t(property "Value" "PWR_FLAG"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
             f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
             f'\t\t(pin "1"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
             f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.path}"\n'
             f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

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


# helpers to create the common small parts -------------------------------------------------------
def res(sh: Sheet, x, y, ref, value, fp, a, b, rotation=90, dnp=False, desc="", show=False):
    sh.place(S_R, x, y, ref, value, fp, {"1": a, "2": b}, rotation=rotation, dnp=dnp, desc=desc or "Resistor",
             show_value=show)


def cap(sh: Sheet, x, y, ref, value, fp, a, b, desc="Decoupling capacitor"):
    sh.place(S_C, x, y, ref, value, fp, {"1": a, "2": b}, desc=desc, show_value=True)


def opto_nets(unit: int, anode, cathode, coll, emit):
    a = 2 * unit - 1
    return {str(a): anode, str(a + 1): cathode, str(18 - 2 * unit): coll, str(17 - 2 * unit): emit}


# ----------------------------------------------------------------------------- sheets
def sheet_power() -> Sheet:
    sh = Sheet("power", "Power and protection (24 V field supply, 5 V field rail)",
               ["24 V SELV adapter -> PTC 0.5 A -> field 24 V; separate PTC 2 A + jumper for the J_C bank supply (default open)",
                "TSR 1-2450 (24 V -> 5 V, 1 A) supplies 74HC595, TBD62783A inputs and the field side of the isolators",
                "The 24 V adapter is floating w.r.t. USB: GND_F is the field ground, GND_M the USB/MCU ground"])
    sh.place(S_TERM, 60, 80, "J4", "24V_IN", FP["term"], {"1": G("+24V_IN"), "2": G("GND_F")}, desc="24 V DC input, screw terminal 5.08 mm")
    sh.place(S_TVS, 60, 140, "D1", "SMAJ33A", FP["dsma"], {"1": G("+24V_IN"), "2": G("GND_F")},
             desc="TVS 33 V across the 24 V input (unidirectional part, class: mains-borne surge on the adapter lead)")
    sh.place(S_PTC, 120, 80, "F1", "PTC 0.5A 30V", FP["f1812"], {"1": G("+24V_IN"), "2": G("+24V_F")},
             desc="Resettable fuse, test supply (e.g. Bourns MF-MSMF050-2)")
    sh.place(S_PTC, 120, 140, "F2", "PTC 2A 16V", FP["f2920"], {"1": G("+24V_IN"), "2": "BANK_F"},
             desc="Resettable fuse, bank prototype supply (e.g. Bourns MF-MSMF200)")
    sh.place(S_SJ, 180, 140, "JP1", "BANK_24V", FP["sj"], {"1": "BANK_F", "2": G("+24V_BANK")},
             desc="Solder jumper (default OPEN): connects +24 V to J_C pins 26/27", rotation=0, in_bom=False)
    sh.place(S_CP, 180, 80, "C1", "100u 35V", FP["cp"], {"1": G("+24V_F"), "2": G("GND_F")}, desc="Bulk capacitor, 24 V rail")
    cap(sh, 220, 80, "C2", "10u 50V", FP["c1206"], G("+24V_F"), G("GND_F"), "24 V rail ceramic")
    sh.place(S_TSR, 300, 90, "U80", "TSR 1-2450", FP["tsr"],
             {"1": G("+24V_F"), "2": G("GND_F"), "3": G("+5V_F")},
             desc="Traco TSR 1-2450, 24 V -> 5 V 1 A switching regulator (non-isolated; isolation comes from the separate adapter)",
             ds="https://www.tracopower.com/products/tsr1.pdf")
    cap(sh, 360, 80, "C3", "22u 10V", FP["c1206"], G("+5V_F"), G("GND_F"), "5 V rail")
    cap(sh, 400, 80, "C4", "100n", FP["c0603"], G("+5V_F"), G("GND_F"), "5 V rail HF")
    # indicators
    res(sh, 60, 220, "R406", "4.7k", FP["r0603"], G("+24V_F"), "LED24", desc="24 V indicator series resistor", show=True)
    sh.place(S_LED, 100, 220, "DL1", "24V", FP["led"], {"1": G("GND_F"), "2": "LED24"}, desc="24 V present", rotation=0)
    res(sh, 160, 220, "R407", "1k", FP["r0603"], G("+5V_F"), "LED5", desc="5 V indicator series resistor", show=True)
    sh.place(S_LED, 200, 220, "DL2", "5V", FP["led"], {"1": G("GND_F"), "2": "LED5"}, desc="5 V field rail present", rotation=0)
    sh.flag("+24V_IN", 40, 40)
    sh.flag("+24V_F", 60, 40)
    sh.flag("+24V_BANK", 80, 40)
    sh.flag("GND_F", 100, 40)
    sh.text("Field domain: +24V_IN (adapter) -> F1 -> +24V_F -> TSR 1-2450 -> +5V_F.  GND_F = adapter 0 V.\n"
            "J_C pins 26/27 (+24V_BANK) are fed from F2 (2 A) through solder jumper JP1, normally open.\n"
            "No protective earth connection on this board: connect the adapter PE to the enclosure.", 40, 280)
    return sh


def sheet_mcu() -> Sheet:
    sh = Sheet("mcu", "MCU: Raspberry Pi Pico (RP2040) / Pico 2 (RP2350)",
               ["Pin map = firmware/hal_pico.py (one change: MUX_S3 on GP27 because GP23 is not on the Pico header)",
                "Powered from USB only; 3V3(OUT) feeds the MCU side of the isolators, the 74HC165 chain and the optocoupler outputs"])
    nets: dict[str, str] = {}
    nc = []
    for gp, pin in PICO_PIN.items():
        if gp in PICO_NETS:
            nets[str(pin)] = G(PICO_NETS[gp])
        else:
            nc.append(str(pin))
    for p in (3, 8, 13, 18, 23, 28, 38):
        nets[str(p)] = G("GND_M")
    nets["36"] = G("+3V3")
    nets["39"] = G("VBUS")      # VSYS tied to VBUS (USB powered)
    nets["40"] = G("VBUS")
    nets["30"] = "RUN_PIN"
    nc += ["33", "35", "37"]   # AGND (module-internal tie to GND), ADC_VREF, 3V3_EN
    sh.place(S_PICO, 200, 200, "U1", "Raspberry Pi Pico", FP["pico"], nets, nc=nc,
             desc="Raspberry Pi Pico / Pico 2 module (40-pin DIP footprint, 2x20)",
             ds="https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf")
    sh.place(S_SW, 80, 120, "SW1", "RESET", FP["sw"], {"1": "RUN_PIN", "2": G("GND_M")},
             desc="Reset button (RUN pin to ground)", rotation=0)
    cap(sh, 100, 280, "C240", "10u", FP["c0805"], G("+3V3"), G("GND_M"), "3.3 V bulk")
    cap(sh, 140, 280, "C241", "100n", FP["c0603"], G("+3V3"), G("GND_M"), "3.3 V HF")
    cap(sh, 180, 280, "C242", "10u", FP["c0805"], G("VBUS"), G("GND_M"), "USB 5 V bulk")
    sh.text("GP2 STROBE (via 6N137 + inverter)  GP3 RUN  GP4 HOME  GP6-9 MUX_EN0-3 (ADC option header J5)\n"
            "GP10 SCK / GP12 MISO / GP13 LD  -> 74HC165 chain (SPI1)\n"
            "GP16 OE / GP17 RCLK / GP18 SCK / GP19 MOSI -> 74HC595 chain (SPI0) via ISO7340C\n"
            "GP20 GP21 GP22 GP27 MUX_S0-3 (ADC option header J5)   GP26 ADC_IN (ADC option header J5)\n"
            "VSYS is tied to VBUS (USB-powered only; the module's own VBUS->VSYS diode is bypassed).", 40, 340)
    return sh


def sheet_iso() -> Sheet:
    sh = Sheet("isolation", "Isolation barrier: digital isolators, STROBE optocoupler",
               ["MCU side: +3V3 / GND_M (USB).  Field side: +5V_F / GND_F (24 V adapter).",
                "U2 ISO7340C (default output HIGH): SCK, MOSI, RCLK, OE -> OE defaults to disabled",
                "U3 ISO7340FC (default output LOW): RUN, HOME default to off"])
    sh.place(S_ISO, 100, 140, "U2", "ISO7340C", FP["iso"],
             {"1": G("+3V3"), "2": G("GND_M"), "8": G("GND_M"), "3": G("SCK_M"), "4": G("MOSI_M"),
              "5": G("RCLK_M"), "6": G("OE_M"), "9": G("GND_F"), "15": G("GND_F"), "16": G("+5V_F"),
              "10": G("+5V_F"), "14": G("SCK_F"), "13": G("MOSI_F"), "12": G("RCLK_F"), "11": G("OE_F")},
             desc="4-channel digital isolator, 4 forward; TI ISO7340C, default output high (substitute for README ISO7740)",
             ds="https://www.ti.com/lit/ds/symlink/iso7340.pdf", stub=7.62)
    sh.place(S_ISOF, 260, 140, "U3", "ISO7340FC", FP["iso"],
             {"1": G("+3V3"), "2": G("GND_M"), "8": G("GND_M"), "3": G("RUN_M"), "4": G("HOME_M"),
              "5": G("GND_M"), "6": G("GND_M"), "9": G("GND_F"), "15": G("GND_F"), "16": G("+5V_F"),
              "10": G("+5V_F"), "14": G("RUN_F"), "13": G("HOME_F"), "12": "U3_OUTC", "11": "U3_OUTD"},
             nc=("12", "11"),
             desc="4-channel digital isolator, fail-safe LOW; TI ISO7340FC. Unused inputs tied low, unused outputs open",
             ds="https://www.ti.com/lit/ds/symlink/iso7340.pdf", stub=7.62)
    # OE pull-up on the field side: wire the isolator output to OE_F through a 0-R-like label join
    res(sh, 180, 220, "R401", "10k", FP["r0603"], G("OE_F"), G("+5V_F"), desc="OE pull-up (drivers off if isolator unpowered)", show=True)
    # MCU-side defaults while the Pico GPIOs float during boot: OE high (drivers off), RUN / HOME low
    res(sh, 220, 220, "R409", "10k", FP["r0603"], G("OE_M"), G("+3V3"), desc="OE_M pull-up: drivers stay off while the Pico boots", show=True)
    res(sh, 260, 220, "R410", "10k", FP["r0603"], G("RUN_M"), G("GND_M"), desc="RUN_M pull-down while the Pico boots", show=True)
    res(sh, 300, 220, "R411", "10k", FP["r0603"], G("HOME_M"), G("GND_M"), desc="HOME_M pull-down while the Pico boots", show=True)
    for i, (ref, a, b) in enumerate([("C231", G("+3V3"), G("GND_M")), ("C232", G("+5V_F"), G("GND_F")),
                                      ("C233", G("+3V3"), G("GND_M")), ("C234", G("+5V_F"), G("GND_F"))]):
        cap(sh, 80 + 40 * i, 320, ref, "100n", FP["c0603"], a, b, "Isolator supply decoupling")
    # STROBE: 24 V logic -> 2.2k -> 6N137 LED ; output pulled up, inverted
    res(sh, 380, 100, "R402", "2.2k", FP["r0603"], G("STROBE_IN"), "STB_A", desc="STROBE LED series resistor", show=True)
    sh.place(S_D, 420, 130, "D175", "1N4148W", FP["d123"], {"1": "STB_A", "2": G("GND_F")}, rotation=0,
             desc="Reverse protection for the STROBE LED")
    sh.place(S_6N137, 480, 140, "U4", "6N137", FP["dip8"],
             {"2": "STB_A", "3": G("GND_F"), "5": G("GND_M"), "6": "STROBE_N", "7": G("+3V3"), "8": G("+3V3")},
             nc=("1", "4"),
             desc="High-speed optocoupler (STROBE); output is open collector, pulled up and inverted by U5",
             ds="https://www.vishay.com/docs/81127/6n137.pdf", stub=7.62)
    res(sh, 560, 100, "R403", "1k", FP["r0603"], "STROBE_N", G("+3V3"), desc="STROBE output pull-up", show=True)
    cap(sh, 560, 180, "C235", "100n", FP["c0603"], G("+3V3"), G("GND_M"), "6N137 supply decoupling")
    sh.place(S_INV, 640, 140, "U5", "SN74LVC1G14", FP["sot235"],
             {"2": "STROBE_N", "4": G("STROBE_M"), "3": G("GND_M"), "5": G("+3V3")}, nc=("1",),
             desc="Schmitt inverter: restores STROBE polarity (high = strobe active) for GP2")
    cap(sh, 640, 190, "C236", "100n", FP["c0603"], G("+3V3"), G("GND_M"), "Inverter decoupling")
    sh.text("Isolation barrier: everything left of the isolator body is USB / MCU domain, right is 24 V field domain.\n"
            "Field-side isolator supply is the TSR 5 V rail; no isolated DC/DC is needed because the 24 V adapter is\n"
            "itself floating (SELV) w.r.t. USB.  Barrier crossing parts: U2, U3 (digital), U4 + 19 x TLP291-4 (opto).", 40, 400)
    return sh


def sheet_shift_out() -> Sheet:
    sh = Sheet("shift_out", "Output shift registers: 7 x 74HC595 (56 bits)",
               ["Chain: MOSI_F -> U10 (bits 0-7) -> U11 (8-15) ... -> U16 (48-55); HAL sends 7 bytes MSB first",
                "Bit n is on chip U10+n//8, output Q(A + n%8); DRV_IN_n goes to the TBD62783A input of node n",
                "Bits 54, 55 unused (RUN/HOME drivers take RUN_F/HOME_F directly); bits 52, 53 spare"])
    for c in range(7):
        x, y = 80 + c * 100, 150
        ref = f"U{10 + c}"
        nets = {"16": G("+5V_F"), "8": G("GND_F"), "10": G("+5V_F"), "11": G("SCK_F"), "12": G("RCLK_F"),
                "13": G("OE_F"), "14": G("MOSI_F") if c == 0 else f"CH{c - 1}"}
        nc = []
        nets["9"] = f"CH{c}" if c < 6 else "QHS_NC"
        if c == 6:
            nc.append("9")
        q = {"15": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
        for pin, j in q.items():
            bit = 8 * c + j
            if bit in (54, 55):
                nc.append(pin)
                nets[pin] = "UNUSED"
            else:
                nets[pin] = G(f"DRV_IN_{bit}")
        sh.place(S_595, x, y, ref, "74HC595", FP["so16"], nets, nc=nc,
                 desc=f"8-bit shift register with latch, bits {8 * c}-{8 * c + 7}", ds="https://www.ti.com/lit/ds/symlink/sn74hc595.pdf")
        cap(sh, x - 30, y + 60, f"C{201 + c}", "100n", FP["c0603"], G("+5V_F"), G("GND_F"), "595 decoupling")
    sh.text("SRCLR tied high (never cleared); OE low only after the firmware has shifted a known pattern.\n"
            "Spare outputs on U16: Q4/Q5 -> DRV_IN_52/53 (inputs of the unused TBD62783A channels 5, 6).", 40, 300)
    return sh


def sheet_shift_in() -> Sheet:
    sh = Sheet("shift_in", "Input shift registers: 10 x 74HC165 (80 bits)",
               ["Chain: DS of U30 (bits 0-7) = GND; Q7 of chip c -> DS of chip c+1; Q7 of U39 (bits 72-79) -> MISO_165",
                "First bit shifted out = bit 79 (HAL: first byte = most significant)",
                "Bit n: chip U30+n//8, input D(n%8); bits 0-51 nodes, 52-66 POS, 67-71 flags, 72 JUDGE, 73 CLEAR, 74-79 tied low"])
    for c in range(10):
        x = 90 + (c % 5) * 150
        y = 120 + (c // 5) * 220
        nets = {"1": G("LD_165"), "2": G("SCK_165"), "15": G("GND_M"), "8": G("GND_M"), "16": G("+3V3"),
                "10": G("GND_M") if c == 0 else f"CH{c - 1}", "9": G("MISO_165") if c == 9 else f"CH{c}"}
        dpins = ["11", "12", "13", "14", "3", "4", "5", "6"]
        for j, pin in enumerate(dpins):
            bit = 8 * c + j
            nets[pin] = G(f"SENSE_{bit}") if bit < 74 else G("GND_M")
        nets["7"] = "QN_NC"
        sh.place(S_165, x, y, f"U{30 + c}", "74HC165", FP["so16"], nets, nc=("7",),
                 desc=f"8-bit parallel-in shift register, bits {8 * c}-{8 * c + 7}", ds="https://www.ti.com/lit/ds/symlink/sn74hc165.pdf")
        cap(sh, x + 50, y + 70, f"C{221 + c}", "100n", FP["c0603"], G("+3V3"), G("GND_M"), "165 decoupling")
    sh.text("CE tied low (clock always enabled). PL low-pulse parallel-loads all chips (HAL: ld 0 then 1).\n"
            "Optocoupler outputs are emitter followers with a 10k pull-down, so a conducting contact reads 1 (active high,\n"
            "no inversion in tester_core).", 40, 440)
    return sh


def frontend_group(g_: int) -> Sheet:
    nodes = list(range(8 * g_, min(8 * g_ + 8, N_NODES)))
    sh = Sheet(f"frontend_g{g_}", f"Driver / sense front end, node group {g_} (nodes {nodes[0]}-{nodes[-1]})",
               [f"U{20 + g_} TBD62783A: 8 high-side channels, one per node; 1k series resistor per node (2512, 1 W)",
                "Sense: node -> 2.2k -> TLP291-4 LED (1N4148W anti-parallel) -> GND_F; output emitter follower + 10k -> SENSE_n",
                "Group 6 additionally carries the RUN / HOME high-side outputs (channels 7, 8)"])
    ud = f"U{20 + g_}"
    inp, outp = {}, {}
    nc = []
    for k in range(8):
        n = 8 * g_ + k
        if g_ == 6 and k == 6:
            inp[str(1 + k)] = G("RUN_F")
        elif g_ == 6 and k == 7:
            inp[str(1 + k)] = G("HOME_F")
        else:
            inp[str(1 + k)] = G(f"DRV_IN_{n}")
        o = str(18 - k)
        if n < N_NODES or (g_ == 6 and k in (6, 7)):
            outp[o] = f"DRV_OUT_{k}"
        else:
            nc.append(o)
            outp[o] = "UNUSED"
    nets = {**inp, **outp, "9": G("+24V_F"), "10": G("GND_F")}
    sh.place(S_TBD, 60, 240, ud, "TBD62783APG", FP["so18w"], nets, nc=nc,
             desc="Toshiba TBD62783A 8-channel high-side DMOS source driver (README: TBD62783A; fits SOIC-18W 7.5x11.6)",
             ds="https://toshiba.semicon-storage.com/info/TBD62783APG_datasheet_en_20171212.pdf", stub=7.62)
    cap(sh, 60, 330, f"C{211 + g_}", "100n 50V", FP["c0805"], G("+24V_F"), G("GND_F"), "Driver supply decoupling")
    for k in range(8):
        n = 8 * g_ + k
        y = 90 + 55 * k
        if n < N_NODES:
            res(sh, 160, y, f"R{n + 1}", "1k 1W", FP["r2512"], f"DRV_OUT_{k}", G(f"NODE_{n}"),
                desc=f"Node {n} drive series resistor", show=True)
            b = n
            sin = G(f"NODE_{n}")
            res(sh, 250, y, f"R{101 + b}", "2.2k", FP["r0603"], sin, f"SNS_{k}", desc=f"Node {n} sense resistor", show=True)
            sh.place(S_D, 320, y + 10, f"D{101 + b}", "1N4148W", FP["d123"], {"1": f"SNS_{k}", "2": G("GND_F")},
                     desc="Optocoupler LED reverse protection", rotation=0, show_value=False)
            p, u = divmod(b, 4)
            sh.place(S_TLP, 410, y, f"U{50 + p}", "TLP291-4", FP["tlp"],
                     opto_nets(u + 1, f"SNS_{k}", G("GND_F"), G("+3V3"), G(f"SENSE_{b}")), unit=u + 1,
                     desc="4-channel optocoupler (README: TLP291-4 or LTV-847)", ds="https://toshiba.semicon-storage.com/info/TLP291-4_datasheet_en_20190301.pdf",
                     show_value=(u == 0))
            res(sh, 500, y, f"R{201 + b}", "10k", FP["r0603"], G(f"SENSE_{b}"), G("GND_M"), desc=f"Sense bit {b} emitter pull-down", show=True)
        elif g_ == 6 and k in (6, 7):
            nm = "RUN" if k == 6 else "HOME"
            res(sh, 160, y, f"R{53 + k - 6}", "1k 1W", FP["r2512"], f"DRV_OUT_{k}", G(f"{nm}_OUT"),
                desc=f"{nm} output series resistor (current limit; bank load value TBD)", show=True)
        # driver input pull-down
        if n < N_NODES + 2:
            res(sh, 590, y, f"R{301 + n}", "47k", FP["r0603"], G(f"DRV_IN_{n}"), G("GND_F"),
                desc="Driver input pull-down (outputs of the 595 are high-Z until OE low)", show=True)
    sh.text(f"Group {g_}: drivers {ud}; sense optocouplers "
            + ", ".join(f"U{50 + (b // 4)}" for b in sorted({8 * g_ + k for k in range(8) if 8 * g_ + k < N_NODES}))
            + "\nRUN_OUT / HOME_OUT are 24 V high-side outputs (group 6 only).", 40, 560, 1.5)
    return sh


def sheet_aux() -> Sheet:
    sh = Sheet("aux_sense", "Auxiliary sense inputs (J_C): POS, flags, ESTOP/ILOCK, JUDGE, CLEAR",
               ["Bits 52-66 POS0-14, 67 STOPPED, 68 AT_HOME, 69 END, 70 ESTOP_OK, 71 ILOCK_OK (HAL flag bits), 72 JUDGE, 73 CLEAR (new)",
                "ESTOP / ILOCK: +24V_F -> 2.2k -> J_C 22 / 24 ... external chain ... J_C 23 / 25 -> sense; closed chain reads 1",
                "Opto units 74/75 of U68 are spare (no connection); 165 inputs 74-79 are tied low on the shift_in sheet"])
    for i, (b, net, jpin) in enumerate(AUX):
        y = 60 + 22 * i
        res(sh, 130, y, f"R{101 + b}", "2.2k", FP["r0603"], G(net), f"SNS_{b}", desc=f"{net} sense resistor (J_C pin {jpin})", show=True)
        sh.place(S_D, 200, y, f"D{101 + b}", "1N4148W", FP["d123"], {"1": f"SNS_{b}", "2": G("GND_F")},
                 desc="Optocoupler LED reverse protection", rotation=0, show_value=False)
        p, u = divmod(b, 4)
        sh.place(S_TLP, 300, y, f"U{50 + p}", "TLP291-4", FP["tlp"],
                 opto_nets(u + 1, f"SNS_{b}", G("GND_F"), G("+3V3"), G(f"SENSE_{b}")), unit=u + 1,
                 desc="4-channel optocoupler", show_value=(u == 0))
        res(sh, 400, y, f"R{201 + b}", "10k", FP["r0603"], G(f"SENSE_{b}"), G("GND_M"), desc=f"Sense bit {b} emitter pull-down", show=True)
    # spare units of package 18 (bits 74, 75)
    for u, yy in ((2, 60 + 22 * 22), (3, 60 + 22 * 23)):
        pins = opto_nets(u + 1, 0, 0, 0, 0)
        sh.place(S_TLP, 300, yy, "U68", "TLP291-4", FP["tlp"], {}, unit=u + 1, nc=list(pins.keys()),
                 desc="spare unit", show_value=False)
    res(sh, 560, 100, "R404", "2.2k", FP["r0603"], G("+24V_F"), G("ESTOP_SRC"), desc="ESTOP chain source resistor (J_C pin 22)", show=True)
    res(sh, 560, 140, "R405", "2.2k", FP["r0603"], G("+24V_F"), G("ILOCK_SRC"), desc="ILOCK chain source resistor (J_C pin 24)", show=True)
    sh.text("ESTOP chain: J_C 22 (source, 24 V via 2.2k) -> external ESTOP contacts -> J_C 23 -> 2.2k -> opto LED.\n"
            "ILOCK chain: J_C 24 -> external interlock contacts -> J_C 25 -> 2.2k -> opto LED.\n"
            "POS/STOPPED/AT_HOME/END/JUDGE/CLEAR are 24 V logic outputs of the bank, referenced to GND_F (J_C 28-33).", 40, 600, 1.5)
    return sh


def sheet_conn() -> Sheet:
    sh = Sheet("connectors", "Connectors J_A / J_B / J_C and ADC-option headers",
               ["J_A: IN / A side, pins 1-26 = lines A-Z (nodes 0-25), 27-29 = 0 V, 30-37 reserved",
                "J_B: OUT / B side, pins 1-26 = lines A-Z (nodes 26-51), 27-29 = 0 V, 30-37 reserved",
                "J_C: control; see README 3.4 (JUDGE 34, CLEAR 35 added to the inputs)"])
    for ref, val, x, base, side in (("J1", "J_A", 120, 0, "IN / A"), ("J2", "J_B", 420, 26, "OUT / B")):
        nets = {str(i + 1): G(f"NODE_{base + i}") for i in range(26)}
        nets.update({"27": G("GND_F"), "28": G("GND_F"), "29": G("GND_F")})
        sh.place(S_DB37, x, 250, ref, val, FP["dsub"], nets, nc=[str(p) for p in range(30, 38)], stub=10.16,
                 desc=f"D-sub 37 socket, {side} port, 1-26 lines A-Z, 27-29 0 V", ds="")
    nets = {}
    for p in range(1, 38):
        if p in JC_PINS:
            nets[str(p)] = G(JC_PINS[p])
    sh.place(S_DB37, 720, 250, "J3", "J_C", FP["dsub"], nets, nc=[str(p) for p in JC_NC], stub=10.16,
             desc="D-sub 37 socket, control port (README 3.4)")
    # ADC option headers
    h5 = {"1": G("+3V3"), "2": G("GND_M"), "3": G("MUX_S0"), "4": G("MUX_S1"), "5": G("MUX_S2"), "6": G("MUX_S3"),
          "7": G("MUX_EN0"), "8": G("MUX_EN1"), "9": G("MUX_EN2"), "10": G("MUX_EN3"), "11": G("ADC_IN"), "12": G("GND_M")}
    sh.place(S_H6, 120, 450, "J5", "ADC_OPT_MCU", FP["hdr2x6"], h5, desc="ADC option (MCU side): daughter board with 4 x CD74HC4067 + buffer; defeats isolation when connected")
    h6 = {str(i + 1): G(f"NODE_{i}") for i in range(52)}
    h6.update({"53": G("GND_F"), "54": G("GND_F")})
    sh.place(S_H27, 420, 450, "J6", "ADC_OPT_FIELD", FP["hdr2x27"], h6, stub=7.62,
             desc="ADC option (field side): 52 node taps + 2 x GND_F for the ADC daughter board")
    sh.text("Mounting: D-sub sockets at the board edge (bottom). J5/J6 are the optional analogue-sense (M5) port:\n"
            "the 82k/10k dividers, 4 x CD74HC4067 and the buffer live on a separate daughter board (not designed here),\n"
            "because a populated divider ties GND_F to GND_M and so bypasses the isolation barrier. Leave J5/J6 empty for normal use.", 40, 640, 1.5)
    return sh


# ----------------------------------------------------------------------------- root
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
    root_uuid = uid("file", "root")
    # patch Sheet so that every new sheet picks up its instance path
    orig_init = Sheet.__init__

    def init(self, key, *a, **k):
        orig_init(self, key, *a, **k)
        self.path = f"/{root_uuid}/{uid('sheet', key)}" if key != "root" else f"/{root_uuid}"

    Sheet.__init__ = init
    sheets = []
    keys = ([("POWER", "power", sheet_power), ("MCU", "mcu", sheet_mcu), ("ISOLATION", "isolation", sheet_iso),
             ("SHIFT_OUT", "shift_out", sheet_shift_out), ("SHIFT_IN", "shift_in", sheet_shift_in)]
            + [(f"FRONTEND_G{k}", f"frontend_g{k}", (lambda k=k: frontend_group(k))) for k in range(7)]
            + [("AUX_SENSE", "aux_sense", sheet_aux), ("CONNECTORS", "connectors", sheet_conn)])
    for name, key, fn in keys:
        sheets.append((name, key, fn()))
    root = Sheet("root", "Scrambler unit tester - main board",
                 ["Project Christopher - Phase 3.0 deliverable 5 (unit tester)",
                  "Generated by gen_tester.py - do not edit by hand",
                  "Design: hardware/common/unit-tester/README.md; pin map firmware/hal_pico.py"], paper="A2")
    # sheet symbols
    col, row = 0, 0
    for i, (name, key, sh) in enumerate(sheets):
        x = 30 + (i % 4) * 90
        y = 40 + (i // 4) * 40
        root.items.append(sheet_block(uid("sheet", key), root_uuid, name, f"{key}.kicad_sch", x, y, 60, 20, i + 2))
    # mounting holes
    for k, (mx, my) in enumerate([(30, 190), (60, 190), (90, 190), (120, 190), (150, 190), (180, 190)]):
        root.place(S_MH, mx, my, f"H{k + 1}", "M3", FP["mh"], {}, desc="M3 mounting hole (pad-less)", show_value=False, in_bom=False)
    root.text("Scrambler unit tester main board (Raspberry Pi Pico, 7 x 74HC595, 10 x 74HC165, 52 node channels,\n"
              "TBD62783A high-side drivers, TLP291-4 sensing, ISO7340 digital isolators, D-sub 37 J_A/J_B/J_C).\n"
              "Domains: FIELD = 24 V adapter (GND_F, +24V_F, +5V_F); MCU = USB (GND_M, +3V3).  Barrier = ISO7340 + optocouplers.\n"
              "Global labels join sheets; see README (pcb section) for the net and bit-order conventions.", 30, 20, 1.8)
    (outdir / f"{PROJECT}.kicad_pro").write_text(PRO % PROJECT, encoding="utf8", newline="\n") \
        if not (outdir / f"{PROJECT}.kicad_pro").exists() else None
    (outdir / f"{PROJECT}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    for name, key, sh in sheets:
        (outdir / f"{key}.kicad_sch").write_text(sh.render(), encoding="utf8", newline="\n")
    print(f"wrote {PROJECT}.kicad_sch + {len(sheets)} sheets to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
