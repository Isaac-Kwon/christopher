#!/usr/bin/env python3
"""Generate the KiCad 10 project + hierarchical schematic of the GEAR MACHINE CONTROL BOARD (drive control board).

Design: README.md.  Interface: docs/decisions/0003-interfaces.md (control connector, 24 V level signals, L1-L9,
protection) ; drive: docs/decisions/0002 section 5 (closed-loop stepper, enable / step / dir).
Stdlib only; library symbols are copied from the installed KiCad libraries (env KICAD_SHARE overrides).

    python gen_control.py        # writes control_board.kicad_pro/.kicad_sch + the sub-sheets next to this file

Sheets:  POWER  MCU  INPUTS  SENSORS  LOGIC  OUTPUTS  CONNECTORS
Every net name is global (label '@' in the code) so the netlist has plain names; the pin map of the Pico is PICO_GPIO.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from kilib import LABEL_ANGLE, OUT, Sym, rot

HERE = Path(__file__).resolve().parent
PROJECT = "control_board"
NS = uuid.UUID("5c1e8a52-7b41-4d6f-8f07-5b6c1d0c9e11")

# ----------------------------------------------------------------------------- spec tables
# Pico GPIO -> net (the pin map also used by the firmware model and checked by verify_control.py)
PICO_GPIO = {0: "PUL_M", 1: "DIR_M", 2: "ENA_M", 3: "TXEN", 4: "HOMING", 5: "SCK_M", 6: "MOSI_M", 7: "RCLK_M",
             8: "OE_M", 9: "RUN_M", 10: "STOPL_M", 11: "HOME_M", 12: "V48_M", 13: "ALM_M", 14: "SW_M",
             15: "ESTOPOK_M", 16: "ILOCKOK_M", 17: "SLOT_S", 18: "SLOT_J", 19: "SLOT_C", 20: "T_L", 21: "I_L",
             22: "T_M", 26: "I_M", 27: "T_S", 28: "I_S"}
PICO_PIN = {0: 1, 1: 2, 2: 4, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14, 11: 15, 12: 16, 13: 17,
            14: 19, 15: 20, 16: 21, 17: 22, 18: 24, 19: 25, 20: 26, 21: 27, 22: 29, 26: 31, 27: 32, 28: 34}

# 24 V inputs: (name, net seen at the board edge, source)
INPUTS = [("RUN", "RUN", "J_CTRL pin 2"), ("STOP_LATCH", "STOP_LATCH", "J_CTRL pin 36"),
          ("HOME", "HOME", "J_CTRL pin 4"), ("ESTOP_RET", "ESTOP_RET", "J_CTRL pin 23"),
          ("ILOCK_RET", "ILOCK_RET", "J_CTRL pin 25"), ("V48_OK", "V48_OK", "J_AUX pin 1 (KM3 auxiliary contact, 24 V level)"),
          ("SW_OK", "+24V_SW", "+24V_SW rail present"), ("ALM", "ALM_IN", "J_DRV pin 5 (drive alarm, open collector)")]
INPUT_M = ["RUN_M", "STOPL_M", "HOME_M", "ESTOPOK_M", "ILOCKOK_M", "V48_M", "SW_M", "ALM_M"]

# J_CTRL (decision 0003 5.2)
POS = [f"POS{k}" for k in range(15)]
JC_PINS = {1: "STROBE", 2: "RUN", 3: "STOPPED", 4: "HOME", 5: "AT_HOME", 6: "END", 22: "ESTOP_A", 23: "ESTOP_RET",
           24: "ILOCK_A", 25: "ILOCK_RET", 34: "JUDGE", 35: "CLEAR", 36: "STOP_LATCH"}
for _k in range(15):
    JC_PINS[7 + _k] = f"POS{_k}"
for _p in range(28, 34):
    JC_PINS[_p] = "GND"
JC_NC = (26, 27, 37)

BITS = ["STOPPED", "AT_HOME", "END"] + POS            # 595 bits 0..17 (bits 18..23 spare)
OUT_SIGNALS = ["STROBE", "JUDGE", "CLEAR"] + BITS      # 21 connector outputs, each with a resettable fuse

FP = dict(
    pico="Module:RaspberryPi_Pico_Common_THT",
    dsub="Connector_Dsub:DSUB-37_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm",
    so16="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    so14="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
    so18w="Package_SO:SOIC-18W_7.5x11.6mm_P1.27mm",
    tlp="Package_SO:SOIC-16_4.55x10.3mm_P1.27mm",
    r0603="Resistor_SMD:R_0603_1608Metric",
    d123="Diode_SMD:D_SOD-123", dsma="Diode_SMD:D_SMA",
    c0603="Capacitor_SMD:C_0603_1608Metric", c0805="Capacitor_SMD:C_0805_2012Metric",
    c1206="Capacitor_SMD:C_1206_3216Metric", cp="Capacitor_SMD:CP_Elec_8x10",
    f1812="Fuse:Fuse_1812_4532Metric", f1206="Fuse:Fuse_1206_3216Metric",
    sot223="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    sjb="Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm",
    sj="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    tsr="Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT",
    t2="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
    t6="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-6-5.08_1x06_P5.08mm_Horizontal",
    xh3="Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical",
    hdr4="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    led="LED_SMD:LED_0603_1608Metric", hole="MountingHole:MountingHole_3.2mm_M3_Pad",
    sw="Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92",
)

S_PICO = Sym("MCU_Module", "RaspberryPi_Pico")
S_595 = Sym("74xx", "74HC595")
S_TBD = Sym("Transistor_Array", "TBD62783A")
S_TLP = Sym("Isolator", "TLP291-4")
S_14 = Sym("74xx", "74HC14")
S_08 = Sym("74xx", "74LS08")        # same pinout as the 74HC08 (the value field says 74HC08)
S_32 = Sym("74xx", "74LS32")        # same pinout as the 74HC32
S_02 = Sym("74xx", "74HC02")
S_74 = Sym("74xx", "74HC74")
S_R = Sym("Device", "R_US")
S_C = Sym("Device", "C")
S_CP = Sym("Device", "C_Polarized")
S_D = Sym("Device", "D")
S_DS = Sym("Device", "D_Schottky")
S_LED = Sym("Device", "LED")
S_PTC = Sym("Device", "Polyfuse")
S_TVS = Sym("Diode", "SMAJ33A")
S_ZENER = Sym("Device", "D_Zener")
S_TSR = Sym("Converter_DCDC", "TSR_1-2450")
S_T2 = Sym("Connector", "Screw_Terminal_01x02")
S_T6 = Sym("Connector", "Screw_Terminal_01x06")
S_DB37 = Sym("Connector", "DC37_Pins")
S_XH3 = Sym("Connector_Generic", "Conn_01x03")
S_H4 = Sym("Connector_Generic", "Conn_01x04")
S_QP = Sym("Transistor_FET", "Q_PMOS_GDS")
S_SJB = Sym("Jumper", "SolderJumper_2_Bridged")
S_SJ = Sym("Jumper", "SolderJumper_2_Open")
S_HOLE = Sym("Mechanical", "MountingHole_Pad")
S_FLAG = Sym("power", "PWR_FLAG")
S_SW = Sym("Switch", "SW_Push")
_FLG = 0


def g(v: float) -> float:
    return round(round(v / 2.54) * 2.54, 2)


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def G(name: str) -> str:
    return "@" + name


# ----------------------------------------------------------------------------- sheet emitter (as the unit tester)
class Sheet:
    def __init__(self, key: str, title: str, comments=(), paper="A1"):
        self.key = key
        self.uuid = uid("file", key)
        self.title, self.paper, self.comments = title, paper, list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.path = ""

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
        is_global = name.startswith("@")
        name = name.lstrip("@")
        dx, dy = out
        ex, ey = x + dx * length, y - dy * length
        self.wire(x, y, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[out], is_global)

    def place(self, sym: Sym, x, y, ref, value, fp, nets=None, unit=1, rotation=0, nc=(), dnp=False,
              desc="", ds="", in_bom=True, on_board=True, show_value=True, stub=5.08):
        nets = dict(nets or {})
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
        for num in pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)
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
                if key not in seen:
                    self.noconnect(sx, sy)
                    seen[key] = "<nc>"
            elif num in nets:
                if key in seen:
                    if seen[key] != nets[num]:
                        raise SystemExit(f"{ref}.{num}: stacked pins with different nets")
                    continue
                seen[key] = nets[num]
                self.net_at(sx, sy, o, nets[num], stub)
            else:
                raise SystemExit(f"{ref} unit {unit}: pin {num} ({pname}) has no net / nc")
        extra = set(nets) - set(pins)
        if extra:
            raise SystemExit(f"{ref}: nets given for unknown pins {extra}")

    def flag(self, net, x, y):
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


def res(sh, x, y, ref, value, fp, a, b, rotation=90, desc="", show=False):
    sh.place(S_R, x, y, ref, value, fp, {"1": a, "2": b}, rotation=rotation, desc=desc or "Resistor", show_value=show)


def cap(sh, x, y, ref, value, fp, a, b, desc="Capacitor"):
    sh.place(S_C, x, y, ref, value, fp, {"1": a, "2": b}, desc=desc, show_value=True)


def opto_nets(unit, anode, cathode, coll, emit):
    a = 2 * unit - 1
    return {str(a): anode, str(a + 1): cathode, str(18 - 2 * unit): coll, str(17 - 2 * unit): emit}


# ----------------------------------------------------------------------------- sheets
def sheet_power() -> Sheet:
    sh = Sheet("power", "Power: +24V_L (always on), +24V_SW (cut by the emergency stop), 5 V / 3.3 V logic",
               ["Two 24 V inputs, each: terminal -> PTC -> series P-MOSFET (reverse polarity) -> TVS 33 V + bulk C (decision 0003 7.3)",
                "+24V_L (power unit F16): logic, sensors, status/POS outputs.  +24V_SW (F21): STROBE/JUDGE/CLEAR, PUL/DIR/ENA drivers (cut by K_E)",
                "TSR 1-2450 5 V from +24V_L -> Pico VSYS (Schottky); Pico 3V3 supplies every 74HC gate and the optocoupler outputs"])
    for k, (ref_t, rail, vin, name, hold) in enumerate([("J1", "+24V_L", "VIN_L", "24V_L_IN", "PTC 0.75A hold"),
                                                       ("J2", "+24V_SW", "VIN_SW", "24V_SW_IN", "PTC 0.5A hold")]):
        x0, y0 = 60, 80 + 160 * k
        n = k + 1
        sh.place(S_T2, x0, y0, ref_t, name, FP["t2"], {"1": vin, "2": G("GND")},
                 desc=f"{rail} input, 5.08 mm screw terminal: 1 = +24 V, 2 = 0 V")
        sh.place(S_PTC, x0 + 60, y0, f"F{n}", hold, FP["f1812"], {"1": vin, "2": f"VF{n}"},
                 desc=f"Resettable fuse {rail} input (part number not confirmed)")
        sh.place(S_QP, x0 + 140, y0 + 50, f"Q{n}", "P-MOSFET (reverse polarity)", FP["sot223"],
                 {"1": f"GATEP{n}", "2": f"VF{n}", "3": G(rail)},
                 desc="Series P-MOSFET: drain = supply side, source = rail; Vgs limited by DZ")
        sh.place(S_ZENER, x0 + 200, y0 + 50, f"DZ{n}", "12V", FP["d123"], {"1": G(rail), "2": f"GATEP{n}"},
                 desc="Gate-source zener 12 V (anode on the gate)", show_value=False)
        res(sh, x0 + 250, y0 + 50, f"RG{n}", "100k", FP["r0603"], f"GATEP{n}", G("GND"), desc="P-MOSFET gate pull-down")
        sh.place(S_TVS, x0 + 60, y0 + 60, f"D{n}", "SMAJ33A", FP["dsma"], {"1": G(rail), "2": G("GND")},
                 desc="TVS 33 V behind the series MOSFET", show_value=False)
        sh.place(S_CP, x0 + 120, y0, f"C{n}", "100u 35V", FP["cp"], {"1": G(rail), "2": G("GND")}, desc="Bulk capacitor")
        sh.flag(rail, x0 - 20, y0 - 30)
    sh.place(S_PTC, 360, 80, "F3", "PTC 0.3A hold", FP["f1206"], {"1": G("+24V_L"), "2": G("+24V_S")},
             desc="Sensor supply resettable fuse: 9 photointerrupters x 20 mA (estimate)")
    cap(sh, 420, 80, "C5", "10u 50V", FP["c1206"], G("+24V_S"), G("GND"), "Sensor supply HF")
    sh.place(S_TSR, 360, 160, "U80", "TSR 1-2450", FP["tsr"], {"1": G("+24V_L"), "2": G("GND"), "3": G("+5V")},
             desc="Traco TSR 1-2450, 24 V -> 5 V 1 A switching regulator", ds="https://www.tracopower.com/products/tsr1.pdf")
    cap(sh, 420, 160, "C3", "22u 10V", FP["c1206"], G("+5V"), G("GND"), "5 V rail")
    cap(sh, 460, 160, "C4", "100n", FP["c0603"], G("+5V"), G("GND"), "5 V rail HF")
    sh.place(S_DS, 520, 160, "D4", "SS14", FP["d123"], {"1": G("VSYS"), "2": G("+5V")}, rotation=0,
             desc="Schottky: +5 V -> Pico VSYS (isolates the USB supply)", show_value=False)
    cap(sh, 580, 160, "C6", "10u 10V", FP["c0805"], G("VSYS"), G("GND"), "VSYS bulk")
    for k, (rail, ref_r, ref_d, y) in enumerate([("+24V_L", "R1", "DL1", 260), ("+24V_SW", "R2", "DL2", 290),
                                                  ("+5V", "R3", "DL3", 320)]):
        val = "1k" if rail == "+5V" else "4.7k"
        res(sh, 360, y, ref_r, val, FP["r0603"], G(rail), f"LED{k}", desc=f"{rail} indicator resistor", show=True)
        sh.place(S_LED, 400, y, ref_d, rail, FP["led"], {"1": G("GND"), "2": f"LED{k}"}, desc=f"{rail} present")
    for k in range(4):
        sh.place(S_HOLE, 60 + 40 * k, 420, f"H{k + 1}", "MountingHole_Pad", FP["hole"], {"1": G("CHASSIS")},
                 desc="Plated M3 mounting hole, net CHASSIS", show_value=False, in_bom=False)
    res(sh, 260, 420, "RCH1", "1M", FP["r0603"], G("CHASSIS"), G("GND"), desc="CHASSIS to 0 V: static drain", show=True)
    cap(sh, 310, 420, "CCH1", "10n 100V", FP["c0603"], G("CHASSIS"), G("GND"), "CHASSIS to 0 V: ESD / HF path")
    sh.flag("VSYS", 160, 40)
    sh.text("0 V is tied to PE only at ONE point in the power unit (XPE); this board never joins 0 V and the frame.\n"
            "D-sub shells (footprint pads SH) and the four plated holes are CHASSIS.", 40, 480)
    return sh


def sheet_mcu() -> Sheet:
    sh = Sheet("mcu", "MCU: Raspberry Pi Pico = drive control (step ramp, HOME / RETURN, position counters, 48 V restart rule)",
               ["The Pico is NOT in the STROBE / JUDGE / CLEAR path (hardware gating on the LOGIC sheet). It only enables / inhibits it (TXEN, HOMING).",
                "Pin map = gen_control.py PICO_GPIO (checked by verify_control.py); firmware spec = firmware_model.py",
                "Boot defaults: TXEN pulled low (no timing outputs), HOMING pulled high (SEEN cleared), 595 OE pulled high (outputs off)"])
    nets, nc = {}, []
    for gp, pin in PICO_PIN.items():
        if gp in PICO_GPIO:
            nets[str(pin)] = G(PICO_GPIO[gp])
        else:
            nc.append(str(pin))
    for p in (3, 8, 13, 18, 23, 28, 38):
        nets[str(p)] = G("GND")
    nets["36"] = G("+3V3")
    nets["39"] = G("VSYS")
    nets["30"] = "RUN_PIN"
    nc += ["40", "35", "37", "33"]      # VBUS, ADC_VREF, 3V3_EN, AGND
    sh.place(S_PICO, 200, 200, "U1", "Raspberry Pi Pico", FP["pico"], nets, nc=nc,
             desc="Raspberry Pi Pico / Pico 2 module (40-pin DIP footprint). USB only for programming (VBUS unconnected: powered from VSYS)",
             ds="https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf")
    sh.place(S_SW, 80, 120, "SW1", "RESET", FP["sw"], {"1": "RUN_PIN", "2": G("GND")}, desc="Reset button (RUN pin to ground)")
    cap(sh, 100, 280, "C10", "10u", FP["c0805"], G("+3V3"), G("GND"), "3.3 V bulk")
    cap(sh, 140, 280, "C11", "100n", FP["c0603"], G("+3V3"), G("GND"), "3.3 V HF")
    res(sh, 300, 120, "R10", "10k", FP["r0603"], G("TXEN"), G("GND"), desc="TXEN pull-down: no timing outputs while the Pico boots", show=True)
    res(sh, 340, 120, "R11", "10k", FP["r0603"], G("HOMING"), G("+3V3"), desc="HOMING pull-up: SEEN stays cleared while the Pico boots", show=True)
    res(sh, 380, 120, "R12", "10k", FP["r0603"], G("OE_M"), G("+3V3"), desc="595 /OE pull-up: outputs off while the Pico boots", show=True)
    sh.text("GP0 PUL  GP1 DIR  GP2 ENA (-> TBD62783A U60 -> 1.5k -> J_DRV)    GP3 TXEN  GP4 HOMING (-> LOGIC)\n"
            "GP5 SCK  GP6 MOSI  GP7 RCLK  GP8 /OE -> 3 x 74HC595 (STOPPED, AT_HOME, END, POS0-14)\n"
            "GP9-16 RUN, STOP_LATCH, HOME, V48_OK, DRV_ALM, SW_OK, ESTOP_OK, ILOCK_OK (optocoupler outputs)\n"
            "GP17-19 conditioned slot levels (monitor only)   GP20-22 T_L I_L T_M   GP26-28 I_M T_S I_S", 40, 340)
    return sh


def sheet_inputs() -> Sheet:
    sh = Sheet("inputs", "24 V level inputs: 2.2k + TLP291-4 (decision 0003 2.2 receiver rules)",
               ["Each input: connector net -> 2.2k -> optocoupler LED -> 0 V, 1N4148W anti-parallel. Load about 10 mA at 24 V (<= 15 mA), on at >= 18 V, off at <= 2.4 V, no pull-up",
                "Optocoupler output: collector +3V3, emitter -> 10k to 0 V -> Pico. ESTOP_RET / ILOCK_RET are the return legs of the bank-owned safety loops (0003 4.6)",
                "SW_OK senses +24V_SW itself; ALM_IN is pulled up by 4.7k (the drive alarm output is open collector, conducting = healthy)"])
    for i, (name, net, src) in enumerate(INPUTS):
        y = 70 + 40 * i
        pkg, u = divmod(i, 4)
        res(sh, 130, y, f"R{20 + i}", "2.2k", FP["r0603"], G(net), f"SNS_{name}", desc=f"{name} sense resistor ({src})", show=True)
        sh.place(S_D, 200, y, f"D{20 + i}", "1N4148W", FP["d123"], {"1": f"SNS_{name}", "2": G("GND")},
                 desc="Optocoupler LED reverse protection", show_value=False)
        sh.place(S_TLP, 300, y, f"U{40 + pkg}", "TLP291-4", FP["tlp"],
                 opto_nets(u + 1, f"SNS_{name}", G("GND"), G("+3V3"), G(INPUT_M[i])), unit=u + 1,
                 desc="4-channel optocoupler", ds="https://toshiba.semicon-storage.com/info/TLP291-4_datasheet_en_20190301.pdf",
                 show_value=(u == 0))
        res(sh, 420, y, f"R{30 + i}", "10k", FP["r0603"], G(INPUT_M[i]), G("GND"), desc=f"{INPUT_M[i]} emitter pull-down", show=True)
    res(sh, 560, 350, "R28", "4.7k", FP["r0603"], G("+24V_L"), G("ALM_IN"), desc="Drive alarm output pull-up (open-collector, conducting = healthy)", show=True)
    sh.text("Polarity: *_M = 1 when the 24 V input is high.  ALM_M = 1 means the drive output is OPEN (alarm / drive unpowered).\n"
            "V48_OK: 24 V level from an a-type auxiliary contact of KM3 (the 48 V contactor); the power unit must supply it (open item).", 40, 440, 1.5)
    return sh


SENSORS = [("S", "STROBE slot"), ("J", "JUDGE slot"), ("C", "CLEAR slot"), ("TL", "T_L position disc F (26 slots)"),
           ("IL", "I_L index F"), ("TM", "T_M position disc M"), ("IM", "I_M index M"), ("TS", "T_S position disc S"),
           ("IS", "I_S index S")]


def sheet_sensors() -> Sheet:
    sh = Sheet("sensors", "Photointerrupter inputs: 3 slot sensors + 6 position sensors, RC filter + 74HC14 Schmitt",
               ["Sensor: 24 V photointerrupter, light-ON = open-collector output conducts (low). Connector JST-XH 3 pin: 1 = +24V_S (PTC F3), 2 = OUT, 3 = 0 V",
                "OUT -> 4.7k pull-up to +3V3 (the open collector only sees 3.3 V) -> 1k + 4.7n (about 5 us) -> 74HC14 (LOGIC sheet): inverted output = HIGH while the slot passes light",
                "The three slot outputs go to the gating (LOGIC sheet) and to the Pico (monitor); T/I outputs go to the Pico only"])
    for i, (k, desc) in enumerate(SENSORS):
        y = 60 + 38 * i
        sh.place(S_XH3, 80, y, f"J{10 + i}", f"SENS_{k}", FP["xh3"], {"1": G("+24V_S"), "2": f"RAW_{k}", "3": G("GND")},
                 desc=f"Photointerrupter connector: {desc}", show_value=True)
        res(sh, 180, y, f"R{40 + i}", "4.7k", FP["r0603"], G("+3V3"), f"RAW_{k}", desc="Open-collector pull-up to 3.3 V", show=True)
        res(sh, 240, y, f"R{50 + i}", "1k", FP["r0603"], f"RAW_{k}", G(f"FLT_{k}"), desc="Filter series resistor", show=True)
        cap(sh, 300, y, f"C{20 + i}", "4.7n", FP["c0603"], G(f"FLT_{k}"), G("GND"), "Filter capacitor")
    sh.text("Slot windows (connector face, layout.md 3.8, CAD params.SLOT_STEPS): STROBE -2.50..+4.52 ms, JUDGE +8.56..+16.63, CLEAR +18.65..+31.73 at 60 rpm.\n"
            "STROBE calibration = rotating bracket of the STROBE sensor (decision 0002); nothing on the board.", 40, 460, 1.5)
    return sh


def sheet_logic() -> Sheet:
    sh = Sheet("logic", "Timing gating (hardware, no firmware): STROBE_G / JUDGE_G / CLEAR_G, cycle hold CYC, SEEN (L8)",
               ["STROBE_G = SLOT_S & TXE ; CLEAR_G = SLOT_C & TXE ; JUDGE_G = SLOT_J & SEEN & TXE ; TXE = TXEN | CYC (74HC32)",
                "CYC (74HC74 unit 2): set while STROBE_G is high, cleared by the falling edge of CLEAR_G -> a started cycle is always finished (L7)",
                "SEEN (74HC74 unit 1): set by the rising edge of STROBE_G, cleared by POR | HOMING | falling edge of STOP_LATCH (RC pulse) -> no JUDGE before a STROBE (L8)"])
    gates14 = [("SLOT_S", "FLT_S"), ("SLOT_J", "FLT_J"), ("SLOT_C", "FLT_C"), ("T_L", "FLT_TL"), ("I_L", "FLT_IL"),
               ("T_M", "FLT_TM"), ("I_M", "FLT_IM"), ("T_S", "FLT_TS"), ("I_S", "FLT_IS"),
               ("SL_N", "STOPL_M"), ("SL_DB", "SL_D"), ("POR", "POR_RC"), ("CLRN", "CLEAR_G"), ("STROBE_N", "STROBE_G")]
    pins14 = [("1", "2"), ("3", "4"), ("5", "6"), ("9", "8"), ("11", "10"), ("13", "12")]      # (input, output)
    for c in range(3):
        ref = f"U{30 + c}"
        for u in range(6):
            idx = 6 * c + u
            x, y = 80 + 140 * c, 70 + 40 * u
            if idx < len(gates14):
                out, inn = gates14[idx]
                nets, nc = {pins14[u][0]: G(inn), pins14[u][1]: G(out)}, []
            else:
                nets, nc = {pins14[u][0]: G("GND")}, [pins14[u][1]]
            sh.place(S_14, x, y, ref, "74HC14", FP["so14"], nets, unit=u + 1, nc=nc,
                     desc="Hex Schmitt-trigger inverter", ds="https://www.ti.com/lit/ds/symlink/sn74hc14.pdf", show_value=(u == 0))
        sh.place(S_14, 80 + 140 * c, 320, ref, "74HC14", FP["so14"], {"7": G("GND"), "14": G("+3V3")}, unit=7, show_value=False)
        cap(sh, 120 + 140 * c, 360, f"C{40 + c}", "100n", FP["c0603"], G("+3V3"), G("GND"), "Decoupling")

    def gate4(sym, ref, val, rows, x0, y0, title):
        pins = [("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"), ("12", "13", "11")]
        for u in range(4):
            a, b, o = pins[u]
            row = rows[u] if u < len(rows) else None
            nets = {a: G(row[0]) if row else G("GND"), b: G(row[1]) if row else G("GND")}
            nc = []
            if row:
                nets[o] = G(row[2])
            else:
                nc = [o]
            sh.place(sym, x0, y0 + 40 * u, ref, val, FP["so14"], nets, unit=u + 1, nc=nc, desc=title, show_value=(u == 0))
        sh.place(sym, x0, y0 + 170, ref, val, FP["so14"], {"7": G("GND"), "14": G("+3V3")}, unit=5, show_value=False)

    gate4(S_08, "U33", "74HC08", [("SLOT_S", "TXE", "STROBE_G"), ("SLOT_C", "TXE", "CLEAR_G"),
                                  ("SLOT_J", "SEEN", "JUDGE_X"), ("JUDGE_X", "TXE", "JUDGE_G")], 560, 70, "Quad AND")
    gate4(S_08, "U34", "74HC08", [("SL_N", "SL_DB", "SL_PULSE")], 700, 70, "Quad AND (one gate used)")
    gate4(S_32, "U35", "74HC32", [("TXEN", "CYC", "TXE"), ("POR", "HOMING", "CLR1")], 840, 70, "Quad OR")
    pins02 = [("2", "3", "1"), ("5", "6", "4"), ("8", "9", "10"), ("11", "12", "13")]      # (in, in, out)
    for u in range(4):
        a, b, o = pins02[u]
        if u == 0:
            nets, nc = {a: G("CLR1"), b: G("SL_PULSE"), o: G("SEEN_R_N")}, []
        else:
            nets, nc = {a: G("GND"), b: G("GND")}, [o]
        sh.place(S_02, 980, 70 + 40 * u, "U36", "74HC02", FP["so14"], nets, unit=u + 1, nc=nc, desc="Quad NOR", show_value=(u == 0))
    sh.place(S_02, 980, 240, "U36", "74HC02", FP["so14"], {"7": G("GND"), "14": G("+3V3")}, unit=5, show_value=False)
    for k, ref in enumerate(["C43", "C44", "C45", "C46"]):
        cap(sh, 560 + 140 * k, 300, ref, "100n", FP["c0603"], G("+3V3"), G("GND"), "Decoupling")
    sh.place(S_74, 560, 400, "U37", "74HC74", FP["so14"],
             {"1": G("SEEN_R_N"), "2": G("+3V3"), "3": G("STROBE_G"), "4": G("+3V3"), "5": G("SEEN")}, unit=1, nc=["6"],
             desc="Dual D flip-flop: unit 1 = SEEN", ds="https://www.ti.com/lit/ds/symlink/sn74hc74.pdf")
    sh.place(S_74, 700, 400, "U37", "74HC74", FP["so14"],
             {"13": G("POR_RC"), "12": G("GND"), "11": G("CLRN"), "10": G("STROBE_N"), "9": G("CYC")},
             unit=2, nc=["8"], desc="Dual D flip-flop: unit 2 = CYC", show_value=False)
    sh.place(S_74, 840, 400, "U37", "74HC74", FP["so14"], {"7": G("GND"), "14": G("+3V3")}, unit=3, show_value=False)
    cap(sh, 560, 470, "C47", "100n", FP["c0603"], G("+3V3"), G("GND"), "Decoupling")
    res(sh, 80, 400, "R60", "100k", FP["r0603"], G("+3V3"), G("POR_RC"), desc="Power-on reset: R (with C50) about 10 ms", show=True)
    cap(sh, 140, 400, "C50", "100n", FP["c0603"], G("POR_RC"), G("GND"), "Power-on reset capacitor")
    res(sh, 80, 450, "R61", "47k", FP["r0603"], G("SL_N"), G("SL_D"), desc="STOP_LATCH falling-edge detector: delay R (with C51) about 0.5 ms", show=True)
    cap(sh, 140, 450, "C51", "10n", FP["c0603"], G("SL_D"), G("GND"), "Edge detector delay capacitor")
    sh.text("CYC ~S = STROBE_N (low while STROBE_G high), ~R = POR_RC, D = 0, clock = CLRN (rises when CLEAR_G falls).\n"
            "SEEN D = 1, clock = STROBE_G, ~R = SEEN_R_N = NOR(POR | HOMING, SL_PULSE); SL_PULSE = SL_N & SL_DB (STOP_LATCH falling edge, about 0.5 ms).\n"
            "TP header (CONNECTORS sheet): SLOT_S SLOT_J SLOT_C = conditioned slot levels for the oscilloscope.", 40, 520, 1.5)
    return sh


def sheet_outputs() -> Sheet:
    sh = Sheet("outputs", "Outputs: 3 x 74HC595 (STOPPED, AT_HOME, END, POS0-14), 4 x TBD62783A, resettable fuses, PUL/DIR/ENA",
               ["Decision 0003 2.2 sender rules: high >= 20 V at 30 mA, low <= 2 V, 0 V short proof (resettable fuse hold >= 30 mA / trip <= 100 mA per output; part number not confirmed)",
                "U60 (+24V_SW): STROBE, JUDGE, CLEAR (hardware gating) + PUL, DIR, ENA (drive).  U61-U63 (+24V_L): status and POS.  Every driver input has a 47k pull-down",
                "595 chain: MOSI -> U50 (bits 0-7) -> U51 (8-15) -> U52 (16-23); bit n = chip n//8, output Q(A + n%8); bits 18-23 spare"])
    q = ["15", "1", "2", "3", "4", "5", "6", "7"]
    for c in range(3):
        nets = {"16": G("+3V3"), "8": G("GND"), "10": G("+3V3"), "11": G("SCK_M"), "12": G("RCLK_M"), "13": G("OE_M"),
                "14": G("MOSI_M") if c == 0 else f"CH{c - 1}"}
        nc = []
        if c < 2:
            nets["9"] = f"CH{c}"
        else:
            nc.append("9")
        for j, pin in enumerate(q):
            bit = 8 * c + j
            if bit < 18:
                nets[pin] = G(f"BIT{bit}")
            else:
                nc.append(pin)
        sh.place(S_595, 80 + c * 100, 120, f"U{50 + c}", "74HC595", FP["so16"], nets, nc=nc,
                 desc=f"8-bit shift register with latch, bits {8 * c}-{8 * c + 7}", ds="https://www.ti.com/lit/ds/symlink/sn74hc595.pdf")
        cap(sh, 80 + c * 100 - 30, 180, f"C{60 + c}", "100n", FP["c0603"], G("+3V3"), G("GND"), "595 decoupling")
    drivers = {0: ("+24V_SW", [("STROBE_G", "DO_STROBE"), ("JUDGE_G", "DO_JUDGE"), ("CLEAR_G", "DO_CLEAR"),
                              ("PUL_M", "DO_PUL"), ("DIR_M", "DO_DIR"), ("ENA_M", "DO_ENA"), (None, None), (None, None)]),
               1: ("+24V_L", [(f"BIT{b}", f"DO_{BITS[b]}") for b in range(8)]),
               2: ("+24V_L", [(f"BIT{b}", f"DO_{BITS[b]}") for b in range(8, 16)]),
               3: ("+24V_L", [("BIT16", "DO_POS13"), ("BIT17", "DO_POS14")] + [(None, None)] * 6)}
    for d in range(4):
        rail, chans = drivers[d]
        ref = f"U{60 + d}"
        nets, nc = {"9": G(rail), "10": G("GND")}, []
        for k, (inp, outp) in enumerate(chans):
            if inp is None:
                nets[str(1 + k)] = G("GND")
                nc.append(str(18 - k))
            else:
                nets[str(1 + k)] = G(inp)
                nets[str(18 - k)] = G(outp)
        sh.place(S_TBD, 60, 330 + 60 * d, ref, "TBD62783APG", FP["so18w"], nets, nc=nc, stub=7.62,
                 desc="Toshiba TBD62783A 8-channel high-side DMOS source driver",
                 ds="https://toshiba.semicon-storage.com/info/TBD62783APG_datasheet_en_20171212.pdf")
        cap(sh, 20, 330 + 60 * d, f"C{70 + d}", "100n 50V", FP["c0805"], G(rail), G("GND"), "Driver supply decoupling")
        for k, (inp, outp) in enumerate(chans):
            if inp is None:
                continue
            res(sh, 160 + 90 * (k % 4), 330 + 60 * d + 10 * (k // 4), f"R{70 + 8 * d + k}", "47k", FP["r0603"], G(inp), G("GND"),
                desc="Driver input pull-down", show=False)
    for i, s in enumerate(OUT_SIGNALS):
        sh.place(S_PTC, 520 + 100 * (i % 4), 90 + 40 * (i // 4), f"F{20 + i}", "PTC 30mA/100mA", FP["f1206"],
                 {"1": G("DO_" + s), "2": G(s)},
                 desc=f"{s} output resettable fuse (hold >= 30 mA, trip <= 100 mA; part number not confirmed)", show_value=False)
        res(sh, 570 + 100 * (i % 4), 110 + 40 * (i // 4), f"R{110 + i}", "47k", FP["r0603"], G(s), G("GND"),
            desc=f"{s} pin-side pull-down (defined low level)", show=False)
    for k, nm in enumerate(["PUL", "DIR", "ENA"]):
        res(sh, 520 + 100 * k, 360, f"R{150 + k}", "1.5k", FP["r0603"], G(f"DO_{nm}"), G(f"DRV_{nm}"),
            desc=f"{nm} series resistor to the drive optocoupler input (about 11 mA at 24 V)", show=True)
    sh.text("DRV_PUL / DRV_DIR / DRV_ENA = 24 V high-side (+24V_SW) through 1.5k to the drive terminal J_DRV; PUL-, DIR-, ENA- are tied to 0 V at the drive.\n"
            "Pulses: 400 per position = 2600 per motor revolution (4:1 belt, 26 positions per F axis revolution) (calculated).", 40, 640, 1.5)
    return sh


def sheet_conn() -> Sheet:
    sh = Sheet("connectors", "Connectors: J_CTRL (control connector, decision 0003 5.2), J_SAFE, J_DRV, J_AUX, test header",
               ["J_CTRL D-sub 37 PINS (male; the cable is socket-socket): 1 STROBE 2 RUN 3 STOPPED 4 HOME 5 AT_HOME 6 END 7-21 POS0-14 22-25 safety loops 28-33 0 V 34 JUDGE 35 CLEAR 36 STOP_LATCH",
                "Safety loops are bank-owned: J_SAFE feeds ESTOP_A / ILOCK_A to J_CTRL 22 / 24 and returns 23 / 25 to the safety relays K_E / K_I of the power unit",
                "Shell pads (SH) and mounting holes = CHASSIS"])
    nets = {str(p): G(JC_PINS[p]) for p in range(1, 38) if p in JC_PINS}
    sh.place(S_DB37, 200, 250, "J3", "J_CTRL", FP["dsub"], nets, nc=[str(p) for p in JC_NC], stub=10.16,
             desc="D-sub 37 pins (male), control connector of decision 0003 5.2")
    sh.place(S_T6, 420, 120, "J4", "J_SAFE", FP["t6"],
             {"1": G("ESTOP_SRC"), "2": G("ILOCK_SRC"), "3": G("ILOCK_CH"), "4": G("ESTOP_K"), "5": G("ILOCK_K"), "6": G("GND")},
             desc="Safety loops (6-way terminal): 1 ESTOP supply (F18, bank E-stop switch if fitted), 2 ILOCK supply (F19), 3 return of the 3 interlock switches in series, 4 ESTOP return to K_E, 5 ILOCK return to K_I, 6 0 V")
    sh.place(S_SJB, 520, 90, "SJ1", "ESTOP SRC-A (bridged)", FP["sjb"], {"1": G("ESTOP_SRC"), "2": G("ESTOP_A")},
             desc="Solder jumper, closed: feeds the ESTOP loop out of the board (cut it to insert the bank's own switch chain between J_SAFE 1 and J_CTRL 22)", in_bom=False, show_value=False)
    sh.place(S_SJ, 520, 120, "SJ2", "ILOCK SRC-A (open)", FP["sj"], {"1": G("ILOCK_SRC"), "2": G("ILOCK_A")},
             desc="Solder jumper, OPEN: close only on the bench when the three interlock switches are not fitted", in_bom=False, show_value=False)
    sh.place(S_SJB, 520, 150, "SJ3", "ILOCK CH-A (bridged)", FP["sjb"], {"1": G("ILOCK_CH"), "2": G("ILOCK_A")},
             desc="Solder jumper, closed: the return of the interlock switch chain (J_SAFE 3) is ILOCK_A (J_CTRL 24)", in_bom=False, show_value=False)
    sh.place(S_SJB, 520, 180, "SJ4", "ESTOP K (bridged)", FP["sjb"], {"1": G("ESTOP_RET"), "2": G("ESTOP_K")},
             desc="Solder jumper, closed: ESTOP_RET (J_CTRL 23) to J_SAFE 4", in_bom=False, show_value=False)
    sh.place(S_SJB, 520, 210, "SJ5", "ILOCK K (bridged)", FP["sjb"], {"1": G("ILOCK_RET"), "2": G("ILOCK_K")},
             desc="Solder jumper, closed: ILOCK_RET (J_CTRL 25) to J_SAFE 5", in_bom=False, show_value=False)
    sh.place(S_T6, 420, 260, "J5", "J_DRV", FP["t6"],
             {"1": G("DRV_PUL"), "2": G("DRV_DIR"), "3": G("DRV_ENA"), "4": G("GND"), "5": G("ALM_IN"), "6": G("GND")},
             desc="Stepper drive (6-way terminal): 1 PUL+, 2 DIR+, 3 ENA+, 4 0 V (PUL- DIR- ENA-), 5 ALM output collector, 6 0 V (ALM emitter)")
    sh.place(S_T2, 420, 360, "J6", "J_AUX", FP["t2"], {"1": G("V48_OK"), "2": G("GND")},
             desc="48 V present: 1 = 24 V level from an a-type auxiliary contact of KM3 (power unit), 2 = 0 V")
    sh.place(S_H4, 420, 440, "J7", "TP_SLOT", FP["hdr4"],
             {"1": G("SLOT_S"), "2": G("SLOT_J"), "3": G("SLOT_C"), "4": G("GND")},
             desc="Test header: conditioned slot levels (high = slot) and 0 V, for the oscilloscope / STROBE bracket calibration")
    sh.text("Safety-loop pins are bank-owned (0003 4.6): K_E / K_I sit in the power unit; the board only passes the loop through J_CTRL and reads the returns.\n"
            "A cable pulled out from J_CTRL opens both loops.", 40, 520, 1.5)
    return sh


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
    orig_init = Sheet.__init__

    def init(self, key, *a, **k):
        orig_init(self, key, *a, **k)
        self.path = f"/{root_uuid}/{uid('sheet', key)}" if key != "root" else f"/{root_uuid}"

    Sheet.__init__ = init
    keys = [("POWER", "power", sheet_power), ("MCU", "mcu", sheet_mcu), ("INPUTS", "inputs", sheet_inputs),
            ("SENSORS", "sensors", sheet_sensors), ("LOGIC", "logic", sheet_logic), ("OUTPUTS", "outputs", sheet_outputs),
            ("CONNECTORS", "connectors", sheet_conn)]
    sheets = [(n, k, fn()) for n, k, fn in keys]
    root = Sheet("root", "Gear machine control board (drive control + timing signal conditioning)",
                 ["Project Christopher - gear bank control board (control connector, decision 0003 5.2)",
                  "Generated by gen_control.py - do not edit by hand", "Design: hardware/gear/control-board/README.md"], paper="A2")
    for i, (name, key, sh) in enumerate(sheets):
        root.items.append(sheet_block(uid("sheet", key), root_uuid, name, f"{key}.kicad_sch", 30 + (i % 4) * 90,
                                      40 + (i // 4) * 40, 60, 20, i + 2))
    root.text("Gear machine control board: Raspberry Pi Pico (drive control), 74HC gating (STROBE / JUDGE / CLEAR hardware path),\n"
              "3 x 74HC595 + 4 x TBD62783A outputs, 8 x TLP291 24 V inputs, 9 photointerrupter inputs, stepper drive interface (PUL / DIR / ENA).\n"
              "The stop decision stays in the relay test register; this board only drives the motor and conditions signals.", 30, 20, 1.8)
    pro = outdir / f"{PROJECT}.kicad_pro"
    if not pro.exists():
        pro.write_text(PRO % PROJECT, encoding="utf8", newline="\n")
    (outdir / f"{PROJECT}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    for name, key, sh in sheets:
        (outdir / f"{key}.kicad_sch").write_text(sh.render(), encoding="utf8", newline="\n")
    print(f"wrote {PROJECT}.kicad_sch + {len(sheets)} sheets to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
