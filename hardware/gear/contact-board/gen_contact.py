#!/usr/bin/env python3
"""Generate the KiCad 10 schematics (and the Omniball footprint) of the gear bombe's column wiring.

Four small designs, all generated:
  contact_board   one drum STATION: 104 spring contacts (Mill-Max Omniball 7845-0) on the four drum rings,
                  each wired to pin (letter + 1) of the 2 x 13 IDC header of its ring (J1..J4 = ring 1..4).
                  Three identical boards per column.
  column_terminal the column's IN / OUT 26-line jacks (male D-sub 37, scrambler side: pins 27-29 NOT tied)
                  with the two ribbon headers that go to the lowest station (ring 4 = IN, ring 1 = OUT).
  reflector_B/_C  reflector wiring card: two ribbon headers (from the top station's ring 3 and ring 2)
                  wired through the reflector permutation of data/wirings/enigma.txt.
Conventions: hardware/gear/contact-board/README.md.   Standard library only.

    python gen_contact.py            # footprint library + the four projects
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import ki_common as K

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WIRING_FILE = REPO / "data" / "wirings" / "enigma.txt"
NS = "6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0e01"
N = 26
DELTA = 360.0 / N

# ----------------------------------------------------------------------------- geometry shared with the PCB step
RING_R = {1: 22.0, 2: 29.0, 3: 36.0, 4: 43.0}       # = drum PCB ring radii (hardware/gear/drum-pcb/README.md)
BOARD = 110.0                                       # layout.md 3.4
CENTER = (150.0, 100.0)                             # board centre in the KiCad sheet
CENTRE_HOLE_D = 38.0                                # index flange Ø34 passes
POST = 48.0                                         # mounting posts at (+-48, +-48)
FP_LIB = "Christopher"
FP_CONTACT = "Omniball_7845-0-15-20-52-14-11-0"
CONTACT_BODY_D = 4.37
CONTACT_PAD_D, CONTACT_DRILL = 2.8, 1.7             # ASSUMED tail hole (datasheet value to be confirmed)
CORNER_ORDER = {"J2": "TL", "J1": "BL", "J3": "TR", "J4": "BR"}   # ring header -> board corner (front view)

FP_IDC13 = "Connector_IDC:IDC-Header_2x13_P2.54mm_Vertical"
FP_DSUB = "Connector_Dsub:DSUB-37_Pins_Vertical_P2.77x2.84mm_MountingHoles"
FP_HOLE = "MountingHole:MountingHole_3.2mm_M3_Pad"

S_CONTACT = K.Sym("Connector_Generic", "Conn_01x01")
S_IDC13 = K.Sym("Connector_Generic", "Conn_02x13_Odd_Even")
S_DSUB = K.Sym("Connector", "DC37_Pins_MountingHoles")
S_HOLE = K.Sym("Mechanical", "MountingHole_Pad")


def L(i: int) -> str:
    return chr(65 + i)


def reflector(name: str) -> list[int]:
    for line in WIRING_FILE.read_text(encoding="utf-8").splitlines():
        f = line.split()
        if len(f) >= 3 and f[0] == "reflector" and f[1] == name:
            return [ord(c) - 65 for c in f[2]]
    raise SystemExit(f"reflector {name} not found")


def contact_ref(ring: int, j: int) -> str:
    return f"P{ring}{L(j)}"


def ring_net(ring: int, j: int) -> str:
    return f"R{ring}_{L(j)}"


# ----------------------------------------------------------------------------- footprint (no library part exists)
def write_footprint(libdir: Path):
    """Mill-Max 7845-0-15-20-52-14-11-0: Omniball spring-loaded contact, through-hole (ball Ø3.96, body Ø4.37).
    Pad/hole and body size are taken from decision 0002 section 3; the tail hole is an assumption (see README)."""
    ids = K.Ids(NS)
    u = lambda *p: ids("fp", FP_CONTACT, *p)
    eff = "(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)"
    txt = f'''(footprint "{FP_CONTACT}"
\t(version 20241229)
\t(generator "gen_contact.py")
\t(layer "F.Cu")
\t(descr "Mill-Max 7845-0-15-20-52-14-11-0 Omniball spring contact, through hole, body 4.37 mm, ball 3.96 mm, free height 9.75 mm (decision 0002 section 3). Tail hole assumed 1.7 mm: confirm with the datasheet.")
\t(tags "Mill-Max Omniball spring contact rolling contact 7845")
\t(property "Reference" "REF**"
\t\t(at 0 -3.6 0)
\t\t(layer "F.SilkS")
\t\t(uuid "{u('ref')}")
\t\t{eff}
\t)
\t(property "Value" "{FP_CONTACT}"
\t\t(at 0 3.6 0)
\t\t(layer "F.Fab")
\t\t(uuid "{u('val')}")
\t\t{eff}
\t)
\t(attr through_hole)
\t(fp_circle
\t\t(center 0 0)
\t\t(end {K.fmt(CONTACT_BODY_D / 2)} 0)
\t\t(stroke
\t\t\t(width 0.1)
\t\t\t(type solid)
\t\t)
\t\t(fill no)
\t\t(layer "F.Fab")
\t\t(uuid "{u('body')}")
\t)
\t(fp_circle
\t\t(center 0 0)
\t\t(end 1.98 0)
\t\t(stroke
\t\t\t(width 0.1)
\t\t\t(type solid)
\t\t)
\t\t(fill no)
\t\t(layer "F.Fab")
\t\t(uuid "{u('ball')}")
\t)
\t(fp_circle
\t\t(center 0 0)
\t\t(end 2.3 0)
\t\t(stroke
\t\t\t(width 0.05)
\t\t\t(type solid)
\t\t)
\t\t(fill no)
\t\t(layer "F.CrtYd")
\t\t(uuid "{u('crt')}")
\t)
\t(fp_text user "${{REFERENCE}}"
\t\t(at 0 0 0)
\t\t(layer "F.Fab")
\t\t(uuid "{u('txt')}")
\t\t{eff}
\t)
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size {K.fmt(CONTACT_PAD_D)} {K.fmt(CONTACT_PAD_D)})
\t\t(drill {K.fmt(CONTACT_DRILL)})
\t\t(layers "*.Cu" "*.Mask")
\t\t(remove_unused_layers no)
\t\t(uuid "{u('pad')}")
\t)
\t(embedded_fonts no)
)
'''
    libdir.mkdir(exist_ok=True)
    (libdir / f"{FP_CONTACT}.kicad_mod").write_text(txt, encoding="utf8", newline="\n")


# ----------------------------------------------------------------------------- schematics
def station_sheet() -> K.Sheet:
    ids = K.Ids(NS)
    sh = K.Sheet(ids, "contact_board", "A2", "Gear bombe contact board (one drum station): 104 Omniball contacts",
                 ["Project Christopher - gear track, hardware/gear/layout.md 3.4; generated by gen_contact.py",
                  "Ring r contact of letter position j -> pin j+1 of header Jr (ring 1 innermost, r = 22 / 29 / 36 / 43 mm)",
                  "Three identical boards per column; no 0 V on the 26-line headers (decision 0003 5.2)"],
                 inst_path=f"/{ids('sheetfile', 'contact_board')}")
    side = {1: "return OUT side (entry-side ring, copy B)", 2: "return IN side (reflector-side ring, copy B)",
            3: "forward OUT side (reflector-side ring, copy A)", 4: "forward IN side (entry-side ring, copy A)"}
    for ring in (1, 2, 3, 4):
        x0 = 40.0 + (ring - 1) * 135.0
        for j in range(N):
            y = 40.0 + j * 10.16
            sh.part(S_CONTACT, x0, y, contact_ref(ring, j), f"7845-0 R{ring}{L(j)}", f"{FP_LIB}:{FP_CONTACT}",
                    {"1": ring_net(ring, j)}, "https://www.mill-max.com/products/datasheet/7845-0-15-20-52-14-11-0",
                    f"Omniball contact, ring {ring} (r = {RING_R[ring]:g} mm), position {L(j)}", show_value=False)
        nets = {str(k + 1): ring_net(ring, k) for k in range(N)}
        sh.part(S_IDC13, x0 + 70.0, 90.0, f"J{ring}", f"RING{ring}", FP_IDC13, nets, "",
                f"Ribbon header of ring {ring}: pin k = contact position k-1 (A..Z), {side[ring]}", length=5.08)
        sh.text(f"RING {ring}\nr = {RING_R[ring]:g} mm\n{side[ring]}", x0 - 5.0, 25.0, 2.0)
    sh.text("Contact position j (A = 0 ... Z = 25) sits at j x 13.846 deg from the index mark, counter-clockwise seen from the\n"
            "contact (front) side = mirror image of the drum's clockwise letter order seen from its pad side.\n"
            "Column chain: IN - lower R4 | lower R3 - middle R4 | middle R3 - upper R4 | upper R3 - reflector - upper R2 |\n"
            "upper R1 - middle R2 | middle R1 - lower R2 | lower R1 - OUT.  Ribbons: 1:1, pin k to pin k.", 40, 12.0, 1.6)
    return sh


def terminal_sheet() -> K.Sheet:
    ids = K.Ids(NS)
    sh = K.Sheet(ids, "column_terminal", "A3", "Gear bombe column terminal: unit IN / OUT jacks",
                 ["Project Christopher - gear track; generated by gen_contact.py",
                  "Male D-sub 37, pins 1-26 = wire A..Z, 27-37 NOT connected (scrambler side, decision 0003 5.2), shell = CHASSIS",
                  "K1 = ribbon to the lowest station's ring 4 header (J4), K2 = ribbon to its ring 1 header (J1)"],
                 inst_path=f"/{ids('sheetfile', 'column_terminal')}")
    for ref, val, tag, x, y in (("J1", "UNIT_IN", "IN", 80.0, 80.0), ("J2", "UNIT_OUT", "OUT", 80.0, 220.0)):
        nets = {str(i + 1): f"{tag}_{L(i)}" for i in range(N)}
        nets["SH"] = "CHASSIS"
        sh.part(S_DSUB, x, y, ref, val, FP_DSUB, nets, "",
                f"Unit {tag} jack (male D-sub 37): pins 1-26 = wire A..Z, 27-37 not connected", length=5.08)
    for ref, val, tag, x, y in (("K1", "IN_RIBBON", "IN", 170.0, 80.0), ("K2", "OUT_RIBBON", "OUT", 170.0, 220.0)):
        sh.part(S_IDC13, x, y, ref, val, FP_IDC13, {str(i + 1): f"{tag}_{L(i)}" for i in range(N)}, "",
                f"26-way ribbon to the lowest station: pin k = wire k", length=5.08)
    for h in range(4):
        sh.part(S_HOLE, 230.0 + h * 20.0, 40.0, f"H{h + 1}", "M3_CHASSIS", FP_HOLE, {"1": "CHASSIS"}, "",
                "M3 mounting hole, plated, CHASSIS", in_bom=False)
    sh.text("Column terminal: the 26-line IN and OUT jacks of one scrambler column (panel side, below the column).\n"
            "No 0 V on this board: pins 27-29 are tied on the common side only (decision 0003 5.2).", 40, 20, 1.6)
    return sh


def reflector_sheet(name: str) -> K.Sheet:
    R = reflector(name)
    ids = K.Ids(NS)
    proj = f"reflector_{name}"
    sh = K.Sheet(ids, proj, "A3", f"Reflector wiring card {name}",
                 ["Project Christopher - gear track; generated by gen_contact.py from data/wirings/enigma.txt",
                  "K1 = ribbon from the top station's ring 3 header (J3), K2 = ribbon to its ring 2 header (J2)",
                  f"Reflector {name}: contact j of ring 3 -> contact R[j] of ring 2"], inst_path=f"/{ids('sheetfile', proj)}")
    n1 = {str(j + 1): f"RF_{L(j)}{L(R[j])}" for j in range(N)}
    n2 = {str(R[j] + 1): f"RF_{L(j)}{L(R[j])}" for j in range(N)}
    sh.part(S_IDC13, 80.0, 100.0, "K1", "FROM_RING3", FP_IDC13, n1, "", "Ribbon from the top station's ring 3 header", length=5.08)
    sh.part(S_IDC13, 180.0, 100.0, "K2", "TO_RING2", FP_IDC13, n2, "", "Ribbon to the top station's ring 2 header", length=5.08)
    wiring = "".join(L(R[j]) for j in range(N))
    sh.text(f"Reflector {name}: {wiring}\nK1 pin j+1 (position j) is joined to K2 pin R[j]+1.", 40, 20, 1.8)
    return sh


# ----------------------------------------------------------------------------- main
def main(outdir: Path = HERE):
    write_footprint(outdir / "Christopher.pretty")
    K.write_fp_lib_table(outdir, {FP_LIB: "${KIPRJMOD}/Christopher.pretty"})
    for proj, sh in (("contact_board", station_sheet()), ("column_terminal", terminal_sheet()),
                     ("reflector_B", reflector_sheet("B")), ("reflector_C", reflector_sheet("C"))):
        K.write_project_and_sheet(outdir, proj, sh)
        print(f"wrote {proj}.kicad_sch")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
