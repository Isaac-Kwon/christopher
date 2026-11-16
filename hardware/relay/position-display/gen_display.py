#!/usr/bin/env python3
"""Generate the KiCad 10 project + schematic of the relay machine's POSITION DISPLAY board (layout.md module I).

What it does (README.md): the core position (l, m, r) arrives on the POS bus (counter common board J4, D-sub 25,
5-bit binary per digit, 24 V level) and is shown to visitors as three letters A-Z.  Per digit:

  * 5 input buffers (one per POS line): a light, display-only receiver (33k pull-down + 100k/18k divider, about 1 mA)
    drives a BSS138, which switches a high-side P-MOSFET (AO3401A) from +24V_L onto the bit's COIL BUS;
  * a relay CONTACT TREE: 25 changeover contacts = 13 G6S-2 relays (bits 0..4 carry 1, 1, 2, 4, 5 relays: the tree is
    rooted at bit 0, so the bit levels need 1, 2, 4, 8 and 10 contacts) route +24 V to exactly one of 26 leaves;
  * 26 lamps (5 mm LED + 2.7k), one per letter, behind a letter scale.

Power from +24V_L (power unit F12) through the decision-0003 input chain.  Display only: nothing here takes part in a
judgement (decision 0003 4.5).

    python gen_display.py            # writes position_display.kicad_pro/.kicad_sch + sheets (inputs, digit_l/m/r, power)
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kigen  # noqa: E402
from kigen import Design, Sym  # noqa: E402

PROJECT = "position_display"
NS = uuid.UUID("5e0d1a7c-2b9f-4c3e-8a61-0f4d2c7b9e11")
BITS, N = 5, 26
DIGITS = [(1, "L", "l (left rotor = fast)"), (2, "M", "m (middle)"), (3, "R", "r (right rotor = slow)")]
# tree rooted at bit 0 (LSB): bit b needs 2**b contacts (b < 4) and 10 at bit 4 (only prefixes 0..9 have a 16+p <= 25)
CONTACTS = [1, 2, 4, 8, 10]
RELAYS = [(c + 1) // 2 for c in CONTACTS]        # G6S-2 = 2 poles -> 1, 1, 2, 4, 5 = 13 per digit

FP = {
    "relay": "Relay_THT:Relay_DPDT_Omron_G6S-2", "diode": "Diode_SMD:D_SOD-123", "r0603": "Resistor_SMD:R_0603_1608Metric",
    "r1206": "Resistor_SMD:R_1206_3216Metric", "c0603": "Capacitor_SMD:C_0603_1608Metric", "led0603": "LED_SMD:LED_0603_1608Metric",
    "led5": "LED_THT:LED_D5.0mm", "sot23": "Package_TO_SOT_SMD:SOT-23", "sot223": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "db25": "Connector_Dsub:DSUB-25_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm",
    "term": "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
    "ptc": "Fuse:Fuse_1812_4532Metric", "tvs": "Diode_SMD:D_SMA", "cp": "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm",
    "hole": "MountingHole:MountingHole_3.2mm_M3_Pad",
}
SYMS = {
    "K": Sym("Relay", "G6S-2"), "D": Sym("Device", "D"), "R": Sym("Device", "R_US"), "C": Sym("Device", "C"),
    "LED": Sym("Device", "LED"), "QN": Sym("Transistor_FET", "BSS138"), "QP": Sym("Transistor_FET", "AO3401A"),
    "DB25": Sym("Connector", "DB25_Pins"), "T2": Sym("Connector", "Screw_Terminal_01x02"), "F": Sym("Device", "Polyfuse"),
    "QPWR": Sym("Transistor_FET", "Q_PMOS_GDS"), "DZ": Sym("Device", "D_Zener"), "TVS": Sym("Device", "D_TVS"),
    "CP": Sym("Device", "C_Polarized"), "HOLE": Sym("Mechanical", "MountingHole_Pad"),
}
# G6S-2: coil 1 (+) / 12 (-); pole 1 COM 4, NC 3, NO 5; pole 2 COM 9, NC 10, NO 8 (as the counter / shifter boards)
POLE = [("4", "3", "5"), ("9", "10", "8")]


def pin_of(sym: Sym, name: str) -> str:
    for num, (_x, _y, _a, pname, _t) in sym.pins(1).items():
        if pname == name:
            return num
    raise SystemExit(f"{sym.lib_id}: no pin {name}")


def letter(v: int) -> str:
    return chr(ord("A") + v)


def node(D: str, b: int, p: int) -> str:
    """Tree node at level b (relay contacts of bit b have their COM here) with prefix p = value of bits 0..b-1."""
    if b == 0:
        return "+24V"
    if b == 4 and p >= 10:          # bit 4 has no contact here: 16 + p > 25, the node is the lamp leaf itself
        return leaf(D, p)
    return f"{D}N{b}_{p:02d}"


def leaf(D: str, v: int) -> str:
    return f"{D}LAMP{v:02d}"


def build() -> Design:
    d = Design()
    G, S, Dd = (pin_of(SYMS["QN"], n) for n in ("G", "S", "D"))
    PG, PS, PD = (pin_of(SYMS["QP"], n) for n in ("G", "S", "D"))

    # ------------------------------------------------------------------ power / connectors (decision 0003 7.3, 7.1)
    sp = "power"
    pos = {1 + j: f"POS{j}" for j in range(15)}
    pos.update({19: "GND", 20: "GND", 21: "GND"})
    d.add("J1", "DB25", "POS_IN", FP["db25"], pos, sp, "conn", nc=[p for p in range(1, 26) if p not in pos],
          desc="D-sub 25 pins = counter common J4 POS: 1-5 l, 6-10 m, 11-15 r (5-bit binary, bit 0 first, 24 V = 1), 19-21 0 V, "
               "16-18 / 22-25 reserved. A third IDC socket on the POS ribbon (counter J4 -> sequencer J5 -> this board)")
    d.add("J2", "T2", "24V_L_IN", FP["term"], {1: "VIN", 2: "GND"}, sp, "pwr",
          desc="Power input (5.08 mm): 1 = +24V_L (power unit F12), 2 = 0 V")
    d.add("F1", "F", "PTC 0.5A hold", FP["ptc"], {1: "VIN", 2: "VF"}, sp, "pwr",
          desc="Resettable fuse, hold 0.5 A (max load 0.33 A; part number not confirmed)")
    d.add("Q1", "QPWR", "P-MOSFET (reverse polarity)", FP["sot223"], {1: "GATE", 2: "VF", 3: "+24V"}, sp, "pwr",
          desc="Series P-channel MOSFET: D = supply side, S = board +24V; Vgs limited by DZ1")
    d.add("DZ1", "DZ", "12V", FP["diode"], {1: "+24V", 2: "GATE"}, sp, "pwr", desc="Gate-source zener 12 V")
    d.add("RG1", "R", "100k", FP["r0603"], {1: "GATE", 2: "GND"}, sp, "pwr", desc="Gate pull-down")
    d.add("TVS1", "TVS", "SMAJ33A", FP["tvs"], {1: "GND", 2: "+24V"}, sp, "pwr", desc="TVS 33 V across the board supply")
    d.add("CB1", "CP", "100uF 35V", FP["cp"], {1: "+24V", 2: "GND"}, sp, "pwr", desc="Bulk capacitor")
    for k in range(1, 5):
        d.add(f"H{k}", "HOLE", "MountingHole_Pad", FP["hole"], {1: "CHASSIS"}, sp, "chassis", show_value=False, in_bom=False,
              desc="Plated M3 mounting hole, net CHASSIS")
    d.add("RCH1", "R", "1M", FP["r0603"], {1: "CHASSIS", 2: "GND"}, sp, "chassis", desc="CHASSIS to 0 V: static drain")
    d.add("CCH1", "C", "10nF 100V", FP["c0603"], {1: "CHASSIS", 2: "GND"}, sp, "chassis", desc="CHASSIS to 0 V: ESD / HF")

    # ------------------------------------------------------------------ input buffers (one per POS line)
    for dn, D, label in DIGITS:
        for b in range(BITS):
            j = 5 * (dn - 1) + b
            pin, gt, nd, pg, bc, bl = f"POS{j}", f"{D}G{b}", f"{D}ND{b}", f"{D}PG{b}", f"{D}BC{b}", f"{D}BL{b}"
            grp = f"in{dn}{b}"
            t = f"{label} bit {b} (POS{j})"
            d.add(f"RP{dn}{b}", "R", "33k", FP["r0603"], {1: pin, 2: "GND"}, "inputs", grp,
                  desc=f"{t}: pin-side pull-down (display-only receiver, README 3.2)")
            d.add(f"RA{dn}{b}", "R", "100k", FP["r0603"], {1: pin, 2: gt}, "inputs", grp, desc=f"{t}: gate divider, upper")
            d.add(f"RB{dn}{b}", "R", "18k", FP["r0603"], {1: gt, 2: "GND"}, "inputs", grp, desc=f"{t}: gate divider, lower")
            d.add(f"CG{dn}{b}", "C", "100n", FP["c0603"], {1: gt, 2: "GND"}, "inputs", grp, desc=f"{t}: gate filter (about 1.5 ms)")
            d.add(f"QN{dn}{b}", "QN", "BSS138", FP["sot23"], {G: gt, S: "GND", Dd: nd}, "inputs", grp,
                  desc=f"{t}: input switch (Vth 0.8-1.5 V)")
            d.add(f"RC{dn}{b}", "R", "10k", FP["r0603"], {1: nd, 2: pg}, "inputs", grp, desc=f"{t}: P-MOSFET gate drive")
            d.add(f"RD{dn}{b}", "R", "4.7k", FP["r0603"], {1: "+24V", 2: pg}, "inputs", grp, desc=f"{t}: P-MOSFET gate pull-up")
            d.add(f"QP{dn}{b}", "QP", "AO3401A", FP["sot23"], {PG: pg, PS: "+24V", PD: bc}, "inputs", grp,
                  desc=f"{t}: high-side switch +24V -> coil bus {bc}")
            d.add(f"RL{dn}{b}", "R", "4.7k", FP["r0603"], {1: bc, 2: bl}, "inputs", grp, desc=f"{t}: bit lamp resistor")
            d.add(f"DB{dn}{b}", "LED", "LED", FP["led0603"], {1: "GND", 2: bl}, "inputs", grp, desc=f"{t}: bit lamp (on = 1)")

    # ------------------------------------------------------------------ contact tree + lamps, one sheet per digit
    for dn, D, label in DIGITS:
        sh = f"digit_{D.lower()}"
        for b in range(BITS):
            prefixes = list(range(2 ** b)) if b < 4 else list(range(10))
            assert len(prefixes) == CONTACTS[b]
            poles = []
            for p in prefixes:
                com = node(D, b, p)
                if b < 4:
                    nc, no = node(D, b + 1, p), node(D, b + 1, p + 2 ** b)
                else:
                    nc, no = leaf(D, p), leaf(D, p + 16)
                poles.append((com, nc, no))
            for i in range(RELAYS[b]):
                ref = f"K{dn}{b}{i}"
                nets = {"1": f"{D}BC{b}", "12": "GND"}
                ncs = []
                for k in range(2):
                    c = 2 * i + k
                    if c < len(poles):
                        for pn, net in zip(POLE[k], poles[c]):
                            nets[pn] = net
                    else:
                        ncs += list(POLE[k])
                desc = (f"{label}: bit {b} tree relay {i + 1}/{RELAYS[b]} (coil bus {D}BC{b}); contacts at prefixes "
                        + ", ".join(str(prefixes[c]) for c in (2 * i, 2 * i + 1) if c < len(prefixes)))
                d.add(ref, "K", "G6S-2 24VDC", FP["relay"], nets, sh, f"k{b}", nc=ncs, desc=desc)
                d.add(f"D{dn}{b}{i}", "D", "1N4148W", FP["diode"], {1: f"{D}BC{b}", 2: "GND"}, sh, f"k{b}",
                      desc=f"{ref} coil flyback", show_value=False)
        for v in range(N):
            d.add(f"RS{dn}{v:02d}", "R", "2.7k", FP["r1206"], {1: leaf(D, v), 2: f"{D}LA{v:02d}"}, sh, f"lamp{v // 13}",
                  desc=f"{label}: lamp {letter(v)} series resistor (about 8 mA, 0.17 W)")
            d.add(f"DS{dn}{v:02d}", "LED", f"Lamp {letter(v)}", FP["led5"], {1: "GND", 2: f"{D}LA{v:02d}"}, sh,
                  f"lamp{v // 13}", desc=f"{label}: letter lamp {letter(v)} (value {v})")
    return d


SHEETS = [("power", "POWER", "Power input (+24V_L), POS connector, CHASSIS", "A3"),
          ("inputs", "INPUTS", "Input buffers: POS line -> divider -> BSS138 -> AO3401A high side -> bit coil bus (15 channels)", "A1"),
          ("digit_l", "DIGIT_L", "Digit l: contact tree (13 G6S-2) and 26 letter lamps", "A1"),
          ("digit_m", "DIGIT_M", "Digit m: contact tree (13 G6S-2) and 26 letter lamps", "A1"),
          ("digit_r", "DIGIT_R", "Digit r: contact tree (13 G6S-2) and 26 letter lamps", "A1")]
NOTES = {
    "power": [("J1 POS_IN = counter common J4 (same pins; a third IDC socket on the POS ribbon).  J2 = +24V_L from power unit F12.\n"
               "Power input chain (decision 0003 7.3): PTC 0.5 A -> series P-MOSFET Q1 (drain on the supply side) + DZ1/RG1 -> TVS1, CB1.\n"
               "CHASSIS (0003 7.1): H1-H4 + D-sub shell = CHASSIS, RCH1 1M || CCH1 10nF to 0 V.", 20, 14)],
    "inputs": [("Display-only receiver per POS line (README 3.2): 33k pull-down + 100k/18k divider = 25.8 kOhm, about 0.93 mA at 24 V.\n"
                "Gate = 0.153 x pin voltage: 2.4 V -> 0.37 V (off, BSS138 Vth >= 0.8 V); 18 V -> 2.75 V (on).  AO3401A Vgs = -24 x 4.7/14.7 = -7.7 V.\n"
                "Each P-MOSFET feeds the coil bus <D>BC<b> of the 1/1/2/4/5 tree relays of that bit (and the bit lamp).", 20, 14)],
}
for _dn, _D, _lab in DIGITS:
    NOTES[f"digit_{_D.lower()}"] = [(
        f"Digit {_lab}: contact tree rooted at bit 0.  Level b has its COM on node {_D}N<b>_<p> (p = value of bits 0..b-1; level 0 = +24V);\n"
        f"NC -> prefix p, NO -> prefix p + 2^b.  Bit 4 has contacts only for p = 0..9 (16 + p <= 25); nodes {_D}N4_10..15 are the lamps 10..15 directly.\n"
        f"Exactly one leaf {_D}LAMPvv is at +24 V for every code 0..25 -> lamp vv (letter A + vv).  Codes 26..31 never occur (counter 0..25).", 20, 14)]


def main(outdir: Path = HERE):
    design = build()
    files = kigen.write_schematic(
        outdir, PROJECT, NS, design, SYMS, SHEETS,
        "Relay position display (module I): POS (l, m, r) -> three letters A-Z",
        ["Project Christopher - relay bombe track, position display board", "Generated by gen_display.py - do not edit by hand",
         "Design: hardware/relay/position-display/README.md"],
        "Position display: 15 POS lines (counter common J4) -> 15 light input buffers -> bit coil buses -> per digit a contact tree\n"
        "of 25 changeover contacts (13 G6S-2) -> exactly one of 26 letter lamps.  Supply +24V_L.  Display only (decision 0003 4.5).",
        NOTES)
    n_rel = sum(1 for p in design.parts if p.kind == "K")
    print(f"wrote {', '.join(files)} to {outdir}; {len(design.parts)} parts, {n_rel} relays")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
