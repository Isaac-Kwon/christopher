#!/usr/bin/env python3
"""Generate the offset dial (layout.md module N): a 26-position PCB code-track rotary switch, one per unit.

The dial gives the counter unit board its HOME preset through the counter's DIAL connector J2 (D-sub 15): pin 1
DIAL_COM (= LOAD), 2-6 the a1 code (bit 0..4) and 7-11 the a2 code; the dial joins COM to the pins of the 1 bits
(counter-board README 5).  The codes are taken from the counter's own reference model, the Python port of relay.rs
`Counters` in counter-board/verify_counter.py: at HOME with crib offset k, (a1, a2) = Counters(k).controls()[:2]
= (k, -k mod 26).  The scale letter of position k is A + k (menu cards: drum_offset_letters).

Stator = this board (116 x 58, 2 layers): the F side faces into the machine and carries the code tracks (F.Cu, bare /
hard gold) and the D-sub; the B side faces the visitor and carries the letter scale.  Rotor (mechanical, README 4):
a wiper comb with 11 tips on one radial line (dialmech.py).  Every ring is cut into arcs at the half-way angles
between detents; an arc of a code ring is copper of that ring's pin where the bit is 1 and an isolated dummy island
where it is 0 (flat surface for the tip).  The code tracks are pads of a generated footprint
(christopher_dial.pretty/OffsetDial26_CodeTracks); joining the arcs of a ring to the connector is routing (by hand).

    python gen_dial.py      # offset_dial.kicad_pro/.kicad_sch + dial.kicad_sch, the project symbol / footprint library
"""
from __future__ import annotations

import math
import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "position-display"))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402   (counter reference model: relay.rs Counters)
import dialmech as M  # noqa: E402
from kigen import CustomSym, Design, Sym, f  # noqa: E402

PROJECT = "offset_dial"
LIB = "christopher_dial"
FPNAME = "OffsetDial26_CodeTracks"
NS = uuid.UUID("9a4c2e61-7b3d-4f08-b5e2-1c6d8f0a3e21")
BITS = 5
# ring j (0 = innermost) -> (code, bit): low bits (many transitions) on the outer rings where an arc is longest
TRACKS = [("COM", None), ("A1", 4), ("A2", 4), ("A1", 3), ("A2", 3), ("A1", 2), ("A2", 2), ("A1", 1), ("A2", 1),
          ("A1", 0), ("A2", 0)]
PIN = {("COM", None): "1", **{("A1", b): str(2 + b) for b in range(BITS)}, **{("A2", b): str(7 + b) for b in range(BITS)}}
NET = {"1": "DIAL_COM", **{str(2 + b): f"A1_{b}" for b in range(BITS)}, **{str(7 + b): f"A2_{b}" for b in range(BITS)}}
FP_DA15 = "Connector_Dsub:DSUB-15_Pins_Vertical_P2.77x2.84mm_MountingHoles"


def home_codes(k: int) -> tuple[int, int]:
    """(a1, a2) preset that the counter unit board must load at HOME for crib offset k (relay.rs Counters::home)."""
    a1, a2, _a3, _a4 = vc.Counters(k).controls()
    return a1, a2


def bit_at(track, p, code=home_codes):
    kind, b = track
    a1, a2 = code(p)
    return ((a1 if kind == "A1" else a2) >> b) & 1


# ----------------------------------------------------------------------------- footprint
def _u(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(x) for x in parts)))


def runs(bits: list[int]) -> list[tuple[int, int, int]]:
    """Cyclic runs of equal values: (first position, length, value)."""
    n = len(bits)
    start = next((i for i in range(n) if bits[i] != bits[i - 1]), None)
    if start is None:
        return [(0, n, bits[0])]
    out, i = [], start
    while True:
        j = 1
        while j < n and bits[(i + j) % n] == bits[i]:
            j += 1
        out.append((i, j, bits[i]))
        i = (i + j) % n
        if i == start:
            return out


def arc_poly(phi_a, phi_b, r_in, r_out, slot, center=(0.0, 0.0)) -> list[tuple[float, float]]:
    """Annular sector between visitor angles phi_a < phi_b, ends pulled in by slot/2 (linear) at each radius."""
    da_o, da_i = math.degrees(slot / 2 / r_out), math.degrees(slot / 2 / r_in)
    a0, a1 = phi_a + da_o, phi_b - da_o
    b0, b1 = phi_a + da_i, phi_b - da_i
    n_o = max(2, int(math.ceil((a1 - a0) / 1.0)) + 1)
    n_i = max(2, int(math.ceil((b1 - b0) / 1.0)) + 1)
    pts = [M.xy(a0 + (a1 - a0) * t / (n_o - 1), r_out, center) for t in range(n_o)]
    pts += [M.xy(b1 - (b1 - b0) * t / (n_i - 1), r_in, center) for t in range(n_i)]
    return pts


def footprint_text(code=home_codes, edge_shift=0.0, track_w=M.TRACK_W, com_gap_deg=0.0, letter_shift=0, pitch=M.PITCH) -> str:
    """The stator footprint.  The keyword arguments exist only for the verifier's negative tests (deliberately broken
    variants: a different code, arc ends moved, rings for another comb pitch, a gap in the COM ring, a shifted scale)."""
    o = [f'(footprint "{FPNAME}"\n\t(version 20241229)\n\t(generator "gen_dial.py")\n\t(generator_version "1.0")\n'
         f'\t(layer "F.Cu")\n'
         f'\t(descr "Offset dial stator: 26-position code-track rotary switch. Rings (inside out): COM, a1/a2 bits 4..0 '
         f'interleaved. Code from relay.rs Counters at HOME: a1 = k, a2 = -k mod 26. Tracks on F.Cu (rotor side), letter '
         f'scale on B.SilkS (visitor side). Generated by gen_dial.py")\n\t(tags "rotary switch code track dial")\n']

    def prop(name, val, x, y, layer, hide=False, size=1.0):
        return (f'\t(property "{name}" "{val}"\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(layer "{layer}")\n'
                + ("\t\t(hide yes)\n" if hide else "") +
                f'\t\t(uuid "{_u("fp", name)}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {f(size)} {f(size)})\n'
                f'\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n')
    o.append(prop("Reference", "REF**", -24.0, -27.0, "F.SilkS"))
    o.append(prop("Value", "OffsetDial26", 0.0, 27.8, "F.Fab"))
    o.append(prop("Datasheet", "", 0.0, 0.0, "F.Fab", hide=True))
    o.append(prop("Description", "26-position PCB code-track rotary switch (offset dial)", 0.0, 0.0, "F.Fab", hide=True))
    o.append("\t(attr smd)\n")
    k = [0]

    def nid():
        k[0] += 1
        return _u("item", k[0])

    def circle(r, layer, width, fill="no"):
        o.append(f'\t(fp_circle\n\t\t(center 0 0)\n\t\t(end {f(r)} 0)\n\t\t(stroke\n\t\t\t(width {f(width)})\n'
                 f'\t\t\t(type solid)\n\t\t)\n\t\t(fill {fill})\n\t\t(layer "{layer}")\n\t\t(uuid "{nid()}")\n\t)\n')

    def line(p, q, layer, width):
        o.append(f'\t(fp_line\n\t\t(start {f(p[0])} {f(p[1])})\n\t\t(end {f(q[0])} {f(q[1])})\n\t\t(stroke\n'
                 f'\t\t\t(width {f(width)})\n\t\t\t(type solid)\n\t\t)\n\t\t(layer "{layer}")\n\t\t(uuid "{nid()}")\n\t)\n')

    def text(s, x, y, layer, size, thick, mirror=False):
        j = "\n\t\t\t(justify mirror)" if mirror else ""
        o.append(f'\t(fp_text user "{s}"\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(layer "{layer}")\n\t\t(uuid "{nid()}")\n'
                 f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {f(size)} {f(size)})\n\t\t\t\t(thickness {f(thick)})\n'
                 f'\t\t\t){j}\n\t\t)\n\t)\n')

    radius = lambda j: M.R0 + pitch * j
    r_out_last = radius(M.NTIPS - 1) + track_w / 2
    r_in_first = radius(0) - track_w / 2
    circle(r_out_last + 1.2, "F.CrtYd", 0.05)                                     # rotor sweep
    circle(r_out_last + 0.8, "F.Fab", 0.1)
    line(M.xy(0, M.R0 - 1.5, (0, 0)), M.xy(0, r_out_last + 0.5, (0, 0)), "F.Fab", 0.15)   # comb tip line at position A
    text("comb at A", *M.xy(0, r_out_last + 2.0, (0, 0)), "F.Fab", 0.8, 0.12)
    circle((r_in_first + r_out_last) / 2, "F.Mask", r_out_last - r_in_first + 0.4)  # bare copper band (no mask)
    circle(M.KNOB_D / 2, "B.Fab", 0.1)                                              # knob skirt (visitor side)
    for p in range(M.N):                                                            # letter scale + ticks (B side)
        phi = p * M.ALPHA
        line(M.xy(phi, M.R_TICK[0], (0, 0)), M.xy(phi, M.R_TICK[1], (0, 0)), "B.SilkS", 0.3)
        text(chr(65 + (p + letter_shift) % M.N), *M.xy(phi, M.R_SCALE, (0, 0)), "B.SilkS", M.LETTER_H, 0.4, mirror=True)
    text("OFFSET k", 0.0, -27.0, "B.SilkS", 1.6, 0.25, mirror=True)
    # pads
    pads = []
    for j, tr in enumerate(TRACKS):
        r = radius(j)
        r_in, r_out = r - track_w / 2, r + track_w / 2
        if tr[0] == "COM":
            if com_gap_deg:                    # negative test only: a broken COM ring
                pads.append(("1", M.xy(180.0, r, (0, 0)),
                             arc_poly(com_gap_deg / 2, 360.0 - com_gap_deg / 2, r_in, r_out, 0.0)))
            else:
                pads.append(("1", M.xy(0.0, r, (0, 0)), ("ring", r, track_w)))
            continue
        bits = [bit_at(tr, p, code) for p in range(M.N)]
        for first, length, val in runs(bits):
            phi_a = (first - 0.5) * M.ALPHA + edge_shift
            phi_b = (first + length - 0.5) * M.ALPHA + edge_shift
            num = PIN[tr] if val else ""
            mid = (phi_a + phi_b) / 2
            pads.append((num, M.xy(mid, r, (0, 0)), arc_poly(phi_a, phi_b, r_in, r_out, M.SLOT)))
    for num, (px, py), shape in pads:
        o.append(f'\t(pad "{num}" smd custom\n\t\t(at {f(px)} {f(py)})\n\t\t(size 0.5 0.5)\n\t\t(layers "F.Cu" "F.Mask")\n'
                 f'\t\t(options\n\t\t\t(clearance outline)\n\t\t\t(anchor circle)\n\t\t)\n\t\t(primitives\n')
        if shape[0] == "ring":
            _, r, w = shape
            o.append(f'\t\t\t(gr_circle\n\t\t\t\t(center {f(-px)} {f(-py)})\n\t\t\t\t(end {f(-px + r)} {f(-py)})\n'
                     f'\t\t\t\t(width {f(w)})\n\t\t\t\t(fill no)\n\t\t\t)\n')
        else:
            pts = " ".join(f"(xy {f(x - px)} {f(y - py)})" for x, y in shape)
            o.append(f'\t\t\t(gr_poly\n\t\t\t\t(pts\n\t\t\t\t\t{pts}\n\t\t\t\t)\n\t\t\t\t(width 0)\n\t\t\t\t(fill yes)\n\t\t\t)\n')
        o.append(f'\t\t)\n\t\t(uuid "{nid()}")\n\t)\n')
    o.append(f'\t(pad "" np_thru_hole circle\n\t\t(at 0 0)\n\t\t(size {f(M.SHAFT_HOLE)} {f(M.SHAFT_HOLE)})\n'
             f'\t\t(drill {f(M.SHAFT_HOLE)})\n\t\t(layers "*.Cu" "*.Mask")\n\t\t(uuid "{nid()}")\n\t)\n')
    o.append("\t(embedded_fonts no)\n)\n")
    return "".join(o)


# ----------------------------------------------------------------------------- symbol
def symbol_block() -> str:
    def fx(name, val, x, y, hide=False):
        h = "\n\t\t\t\t(hide yes)" if hide else ""
        return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {f(x)} {f(y)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
                f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t){h}\n\t\t\t)\n\t\t)\n')

    def pin(num, name, x, y, ang):
        return (f'\t\t\t(pin passive line\n\t\t\t\t(at {f(x)} {f(y)} {ang})\n\t\t\t\t(length 2.54)\n'
                f'\t\t\t\t(name "{name}"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t(size 1.27 1.27)\n'
                f'\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(number "{num}"\n\t\t\t\t\t(effects\n'
                f'\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)\n')
    s = ('\t(symbol "OffsetDial26"\n\t\t(pin_names\n\t\t\t(offset 1.016)\n\t\t)\n\t\t(exclude_from_sim no)\n'
         '\t\t(in_bom yes)\n\t\t(on_board yes)\n')
    s += fx("Reference", "SW", 0, 16.51)
    s += fx("Value", "OffsetDial26", 0, -16.51)
    s += fx("Footprint", f"{LIB}:{FPNAME}", 0, 0, True)
    s += fx("Datasheet", "", 0, 0, True)
    s += fx("Description", "26-position code-track rotary switch: COM joined to the code pins of the 1 bits at each detent", 0, 0, True)
    s += ('\t\t(symbol "OffsetDial26_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start -7.62 13.97)\n\t\t\t\t(end 7.62 -13.97)\n'
          '\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n\t\t\t\t(fill\n'
          '\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t(symbol "OffsetDial26_1_1"\n')
    s += pin("1", "COM", -10.16, 0, 0)
    for b in range(BITS):
        s += pin(str(2 + b), f"A1_{b}", 10.16, 12.7 - 2.54 * b, 180)
        s += pin(str(7 + b), f"A2_{b}", 10.16, -1.27 - 2.54 * b, 180)
    s += "\t\t)\n\t\t(embedded_fonts no)\n\t)"
    return s


def write_libraries(outdir: Path):
    (outdir / f"{LIB}.kicad_sym").write_text(
        '(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "gen_dial.py")\n\t(generator_version "1.0")\n'
        + symbol_block() + "\n)\n", encoding="utf8", newline="\n")
    pretty = outdir / f"{LIB}.pretty"
    pretty.mkdir(exist_ok=True)
    (pretty / f"{FPNAME}.kicad_mod").write_text(footprint_text(), encoding="utf8", newline="\n")
    (outdir / "sym-lib-table").write_text(
        '(sym_lib_table\n\t(version 7)\n\t(lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/%s.kicad_sym")(options "")'
        '(descr "Project Christopher offset dial (generated by gen_dial.py)"))\n)\n' % (LIB, LIB), encoding="utf8", newline="\n")
    (outdir / "fp-lib-table").write_text(
        '(fp_lib_table\n\t(version 7)\n\t(lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/%s.pretty")(options "")'
        '(descr "Project Christopher offset dial (generated by gen_dial.py)"))\n)\n' % (LIB, LIB), encoding="utf8", newline="\n")


# ----------------------------------------------------------------------------- schematic
def build() -> Design:
    d = Design()
    d.add("SW1", "DIAL", "OffsetDial26", f"{LIB}:{FPNAME}", {p: n for p, n in NET.items()}, "dial", "sw",
          desc="Offset dial stator: 26-position code-track rotary switch (rotor = wiper comb, README 4)")
    pins = {int(p): n for p, n in NET.items()}
    d.add("J1", "DA15", "DIAL", FP_DA15, pins, "dial", "conn", nc=[12, 13, 14, 15],
          desc="D-sub 15 pins, vertical, on the rotor side: 1 DIAL_COM (= LOAD), 2-6 a1 code bit 0..4 (= k), 7-11 a2 code "
               "(= -k mod 26), 12-13 0 V (not used here), 14-15 reserved. 1:1 cable (sockets) to counter unit J2")
    for k in range(1, 5):
        d.add(f"H{k}", "HOLE", "MountingHole_Pad", "MountingHole:MountingHole_3.2mm_M3_Pad", {1: "CHASSIS"}, "dial", "holes",
              show_value=False, in_bom=False, desc="Plated M3 mounting hole (standoffs to the faceplate and the rear bracket), CHASSIS")
    return d


def syms() -> dict:
    return {"DIAL": CustomSym(LIB, "OffsetDial26", symbol_block()), "DA15": Sym("Connector", "DA15_Pins"),
            "HOLE": Sym("Mechanical", "MountingHole_Pad"), "FLAG": Sym("power", "PWR_FLAG")}


def main(outdir: Path = HERE):
    write_libraries(outdir)
    design = build()
    table = "  ".join(f"{chr(65 + k)}:{home_codes(k)[0]:>2},{home_codes(k)[1]:>2}" for k in range(M.N))
    files = kigen.write_schematic(
        outdir, PROJECT, NS, design, syms(), [("dial", "DIAL", "Offset dial: code-track switch SW1 -> D-sub 15 J1 (to counter unit J2)", "A3")],
        "Offset dial (module N): 26-position code-track rotary switch, one per unit",
        ["Project Christopher - relay bombe track, offset dial", "Generated by gen_dial.py - do not edit by hand",
         "Design: hardware/relay/offset-dial/README.md"],
        "Offset dial: at detent k the wiper comb joins DIAL_COM to the a1 pins of k and the a2 pins of -k mod 26\n"
        "(relay.rs Counters at HOME).  The counter unit board loads them while LOAD (= DIAL_COM) is high during HOME.",
        {"dial": [("Code per position (letter: a1, a2):\n" + "\n".join(table[i:i + 72] for i in range(0, len(table), 72)), 150, 120),
                  ("CHASSIS = H1-H4 + D-sub shell (no 0 V on this passive board; PWR_FLAG for ERC, as the wiring cards).", 150, 110)]},
        flags={"dial": {"CHASSIS": (200, 30)}})
    print(f"wrote {', '.join(files)}, {LIB}.kicad_sym, {LIB}.pretty/{FPNAME}.kicad_mod, sym-lib-table, fp-lib-table to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
