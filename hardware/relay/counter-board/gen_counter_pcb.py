#!/usr/bin/env python3
"""Generate the KiCad 10 PCBs of the relay counter boards: deterministic PLACEMENT only.

Scope (by decision of the project owner): no copper routing.  Each board file contains the outline,
four M3 mounting holes, every footprint placed and oriented, the nets imported from the schematic
netlist (so the ratsnest is present) and the design rules (merged into the .kicad_pro).  Routing is
done later by hand.

    python gen_counter_pcb.py [OUTDIR]   # counter_common.kicad_pcb and counter_unit.kicad_pcb (+ rules in
                                         # the .kicad_pro) into OUTDIR (default: this directory)

Standard library only.  The netlists are exported from the schematics with kicad-cli (env KICAD_CLI
overrides its path); footprints are copied from the installed KiCad libraries (env KICAD_SHARE).

Placement scheme (see README.md section 9): both boards are 250 x 228 mm, the same card as the shifter
board.  Every base-26 digit is one repeated 98 x 84 mm block: five bit columns (16 mm pitch), each with
the slave relay KS on top, the master relay KM below it, the column's diodes in two sub-columns and the
indicator resistor + LED at the bottom; a sixth column holds the wrap relay KW with its diodes.  The
common board has four digit blocks (pL, pM, a3, pR; decision 0003 K3) with the POS connector on the
right edge and the bus + clock connectors of both clock buses on the left edge (bus B: J5 CLOCK_B, J6 UNIT_BUS_B; the
ten twin masters of the bus-B rail buffer sit in the gap under the pL and pM blocks, 0003 K7); the unit board has two digit blocks plus
the 10 buffer relays (bus + dial on the left edge, the four CTRL connectors on the right edge).
Both boards carry, in a strip along the bottom edge, the power input chain (terminal, PTC, series
P-MOSFET with gate zener + resistor, TVS, bulk capacitor; 0003 K1) and the CHASSIS pair R + C (K4);
the four plated mounting holes H1-H4 and the D-sub shells (pads SH) are on net CHASSIS.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_counter as vc  # noqa: E402  (netlist reader)

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
NS = uuid.UUID("b2f5c0de-7a31-4d6e-9e84-3c1a5f7d2b03")

# ----------------------------------------------------------------------------- board geometry (mm)
BOARD_W, BOARD_H = 250.0, 228.0       # = shifter board, so every card fits the same cage slot
HOLES = [(5.0, 5.0), (BOARD_W - 5.0, 5.0), (5.0, BOARD_H - 5.0), (BOARD_W - 5.0, BOARD_H - 5.0)]
CONN_DEPTH = 12.74                     # D-sub footprint: board edge is 12.74 mm from the pin-1 row
BITS = 5
COL_P = 16.0                           # bit column pitch inside a digit block
BLOCK_W, BLOCK_H = 98.0, 84.0          # drawn frame of a digit block
Y_KS, Y_KM = 8.0, 30.0                 # relay rows (pin 1 row) from the block top
Y_DIODE0, DIODE_P = 48.0, 3.8          # first diode row, row pitch
Y_IND = 78.0                           # indicator resistor / LED row
TWIN_DY = 86.0                         # twin-master row (bus B) below a top digit block, in the 24 mm gap

# ----------------------------------------------------------------------------- design rules (fab rules)
TRACK, CLEAR, VIA_D, VIA_DRILL = 0.15, 0.15, 0.5, 0.25     # absolute minima (standard 4-layer class)
SIG_TRACK = 0.2                                            # netclass Default
RAIL_TRACK = 0.6                                           # netclass Rail
RAIL_NETS = ["/+24V", "/GND", "/VIN", "/VF", "/M?", "/R?", "/CK", "/NCK", "/LOAD", "/A?B", "/Z?B", "/LC", "/ZL", "/NL", "/Z3I",
             "/L?", "/T?", "/C?_?", "/DIGIT_*/Q?", "/DIGIT_*/A?", "/DIGIT_*/Z?", "/DIGIT_*/AOUT",
             "/DIGIT_*/ZOUT", "/DIGIT_*/NOUT", "/DIGIT_*/Y?",
             # clock bus B (0003 K7): clock rails, bus-B rails, the twin-master carry chain
             "/CK_B", "/NCK_B", "/LOAD_B", "/LC_B", "/ZL_B", "/A?B_B", "/Z?B_B", "/DIGIT_*/AB?", "/DIGIT_*/YB?"]


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ----------------------------------------------------------------------------- footprint library
_fp_cache: dict[str, str] = {}


def fp_text(libid: str) -> str:
    if libid not in _fp_cache:
        lib, name = libid.split(":")
        _fp_cache[libid] = (FPDIR / f"{lib}.pretty" / f"{name}.kicad_mod").read_text(encoding="utf8")
    return _fp_cache[libid]


def block_end(t: str, i: int) -> int:
    """Index just after the s-expression block that starts at t[i] == '('."""
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
                return j + 1
        j += 1


def courtyard_x(libid: str) -> tuple[float, float]:
    """x extent (min, max) of the front courtyard, footprint coordinates."""
    xs = []
    for m in re.finditer(r'\((?:fp_line|fp_rect)\s*\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)'
                         r'(?:(?!\n\t\))[\s\S])*?\(layer "F.CrtYd"\)', fp_text(libid)):
        xs += [float(m.group(1)), float(m.group(3))]
    return min(xs), max(xs)


def place(project: str, libid: str, ref: str, value: str, x: float, y: float, rot: float,
          pin_net: dict[str, tuple[int, str]], hide_ref=False) -> str:
    """Instantiate a library footprint on the board (F.Cu side)."""
    t = fp_text(libid).replace("\r\n", "\n")
    name = libid.split(":")[1]
    t = re.sub(r'^\t\(version [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator_version [^)]*\)\n', "", t, flags=re.M)
    t = t.replace(f'(footprint "{name}"', f'(footprint "{libid}"', 1)
    head = (f'\t(layer "F.Cu")\n\t(uuid "{uid(project, "fp", ref)}")\n'
            f'\t(at {fmt(x)} {fmt(y)}{" " + fmt(rot) if rot else ""})\n')
    t, n = re.subn(r'^\t\(layer "F\.Cu"\)\n', head, t, count=1, flags=re.M)
    assert n == 1, libid
    t = re.sub(r'\(property "Reference" "[^"]*"', f'(property "Reference" "{ref}"', t, count=1)
    t = re.sub(r'\(property "Value" "[^"]*"', f'(property "Value" "{value}"', t, count=1)
    t = re.sub(r'\(property "Footprint" "[^"]*"', f'(property "Footprint" "{libid}"', t, count=1)

    def hide_prop(t, prop):
        i = t.find(f'(property "{prop}"')
        j = block_end(t, i)
        blk = t[i:j]
        if "(hide yes)" not in blk:
            blk = re.sub(r'(\(layer "[^"]*"\))', r'\1\n\t\t(hide yes)', blk, count=1)
        return t[:i] + blk + t[j:]
    t = hide_prop(t, "Value")
    if hide_ref:
        t = hide_prop(t, "Reference")

    if rot:   # pad / text angles in a board file include the footprint rotation
        def addrot(m):
            a = float(m.group(3)) if m.group(3) is not None else 0.0
            return f'{m.group(1)}(at {m.group(2)} {fmt((a + rot) % 360)})'
        t = re.sub(r'(\((?:pad|property|fp_text)[^\n]*\n\t\t)\(at ([-\d.]+ [-\d.]+)(?: ([-\d.]+))?\)', addrot, t)

    out, pos = [], 0
    for m in re.finditer(r'\(pad "([^"]*)"', t):
        if m.start() < pos:
            continue
        end = block_end(t, m.start())
        blk = t[m.start():end]
        if m.group(1) in pin_net:
            nid, nname = pin_net[m.group(1)]
            blk = re.sub(r'(\n\t\t\(at [^\n]*\))', lambda mm: mm.group(1) + f'\n\t\t(net {nid} "{nname}")', blk, count=1)
        out.append((m.start(), end, blk))
        pos = end
    for s, e, blk in reversed(out):
        t = t[:s] + blk + t[e:]

    k = [0]

    def newuuid(m):
        k[0] += 1
        return m.group(0) if k[0] == 1 else f'(uuid "{uid(project, "fp", ref, k[0])}")'
    t = re.sub(r'\(uuid "[^"]*"\)', newuuid, t)
    return "\n".join(("\t" + ln if ln else ln) for ln in t.rstrip("\n").split("\n")) + "\n"


# ----------------------------------------------------------------------------- netlist
def export_netlist(project: str, path: Path):
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(path),
                    str(HERE / f"{project}.kicad_sch")], check=True, capture_output=True)


def nets_of(nl: "vc.Netlist") -> dict[str, list[tuple[str, str]]]:
    nets: dict[str, list] = collections.defaultdict(list)
    for node, name in nl.node_net.items():
        nets[name].append(node)
    return {k: sorted(v) for k, v in nets.items()}


# ----------------------------------------------------------------------------- placement
def digit_diode_columns(d: int) -> list[list[str]]:
    """Diode references of digit d per bit column (column 5 = wrap relay KW).  The numbering follows
    gen_counter.digit_sheet: per bit 5 feed + 2 flyback diodes (35), rail Z_t OR diodes (t + 1 each for
    t = 1..5, 20), NOUT OR diodes (5), KW coil OR + flyback (4)."""
    cols: list[list[int]] = [[] for _ in range(BITS + 1)]
    for i in range(BITS):
        cols[i] += range(7 * i + 1, 7 * i + 8)
    n = 36
    for t in range(1, BITS + 1):
        cols[t - 1] += range(n, n + t + 1)
        n += t + 1
    for i in range(BITS):
        cols[i].append(n + i)
    cols[BITS] += range(n + BITS, n + BITS + 4)
    return [[f"D{d}{k:02d}" for k in c] for c in cols]


def digit_block(P: dict, d: int, bx: float, by: float):
    """Place one digit (11 relays, 64 diodes, 5 R, 5 LED) with its frame origin at (bx, by)."""
    for c, refs in enumerate(digit_diode_columns(d)):
        x = bx + 4.0 + c * COL_P
        if c < BITS:
            P[f"K{d}0{c}"] = (x, by + Y_KS, 0, False)
            P[f"K{d}1{c}"] = (x, by + Y_KM, 0, False)
            P[f"R{d}{c}"] = (x + 1.0, by + Y_IND, 0, True)
            P[f"DL{d}{c}"] = (x + 7.6, by + Y_IND, 0, True)
        else:
            P[f"K{d}20"] = (x, by + Y_KS, 0, False)
        for k, ref in enumerate(refs):
            P[ref] = (x + (1.0 if k % 2 == 0 else 7.6), by + Y_DIODE0 + (k // 2) * DIODE_P, 0, True)


def twin_row(P: dict, d: int, bx: float, by: float, ndiodes: int):
    """Bus-B rail buffer of digit d (0003 K7): the five twin masters K{d}30..K{d}34 in the gap under the digit block,
    each under its own bit column (= under the master whose coil it shares), the bus-B OR diodes D{d}65.. beside them
    (four per column, right of the relay)."""
    for i in range(BITS):
        x = bx + 4.0 + i * COL_P
        P[f"K{d}3{i}"] = (x, by + TWIN_DY, 0, False)
    for k in range(ndiodes):
        c, r = divmod(k, 4)
        P[f"D{d}{65 + k:02d}"] = (bx + 4.0 + c * COL_P + 10.5, by + TWIN_DY + 1.0 + r * DIODE_P, 0, True)


def power_strip(P: dict, term: str, x0: float, y: float = 217.0):
    """Power input chain + CHASSIS pair in a strip along the bottom edge (0003 K1, K4)."""
    P[term] = (x0, y, 0, False)
    xs = {"F1": 20.0, "Q1": 33.0, "DZ1": 46.0, "RG1": 54.0, "TVS1": 66.0, "CB1": 84.0, "RCH1": 100.0, "CCH1": 108.0}
    for ref, dx in xs.items():
        P[ref] = (x0 + dx, y, 0, ref in ("DZ1", "RG1", "RCH1", "CCH1"))
    for k, (x, yy) in enumerate(HOLES, 1):
        P[f"H{k}"] = (x, yy, 0, True)



def conn_pos(libid: str, side: str, top: float):
    """Footprint origin and rotation for a D-sub on the left/right edge whose courtyard starts at 'top'."""
    xmin, xmax = courtyard_x(libid)
    if side == "L":
        return CONN_DEPTH, top - xmin, 270, xmax - xmin
    return BOARD_W - CONN_DEPTH, top + xmax, 90, xmax - xmin


def placements(project: str, nl: "vc.Netlist"):
    """Return (placement dict ref -> (x, y, rot, hide_ref), frames, connector extents)."""
    P: dict = {}
    frames = []     # (x, y, w, h, label)
    if project == "counter_common":
        for d, (bx, by), lab in ((1, (26.0, 14.0), "pL (a4 = l)"), (2, (130.0, 14.0), "pM"),
                                 (3, (26.0, 122.0), "a3 = l - m"), (4, (130.0, 122.0), "pR (r), preset 0")):
            digit_block(P, d, bx, by)
            frames.append((bx, by, BLOCK_W, BLOCK_H, lab))
        # clock bus B rail buffer (0003 K7): twins of the pL / pM masters in the gap under those blocks
        twin_row(P, 1, 26.0, 14.0, 6)
        twin_row(P, 2, 130.0, 14.0, 11)
        frames.append((26.0, 14.0 + TWIN_DY - 1.5, BLOCK_W, 18.0, "BUS B: pL twins"))
        frames.append((130.0, 14.0 + TWIN_DY - 1.5, BLOCK_W, 18.0, "BUS B: pM twins"))
        bx, by = 26.0, 208.0
        for k, ref in enumerate(("D801", "D802", "D803", "D804", "D805", "D806")):
            P[ref] = (bx + 5.0 + k * 7.0, by + 2.0, 0, True)
        frames.append((bx, by, 46.0, 6.0, "RULE DIODES"))
        power_strip(P, "J3", 76.0)
        conns = [("J1", "L", 14.0), ("J2", "L", 60.0), ("J5", "L", 121.0), ("J6", "L", 158.0), ("J4", "R", 14.0)]
    else:
        digit_block(P, 1, 26.0, 14.0)
        frames.append((26.0, 14.0, BLOCK_W, BLOCK_H, "a2 = m - (r + k)"))
        digit_block(P, 2, 130.0, 14.0)
        frames.append((130.0, 14.0, BLOCK_W, BLOCK_H, "a1 = r + k"))
        bx, by = 26.0, 112.0
        for n in range(10):
            row, b = divmod(n, BITS)
            x, y = bx + 4.0 + b * 20.0, by + 10.0 + row * 36.0
            P[f"K9{n:02d}"] = (x, y, 0, False)
            P[f"D9{n + 1:02d}"] = (x + 2.5, y + 19.0, 0, True)
        frames.append((bx, by, 110.0, 80.0, "BUFFER RELAYS: a4 (K900-K904), a3 (K905-K909)"))
        power_strip(P, "J7", 30.0, 212.0)
        conns = [("J1", "L", 14.0), ("J2", "L", 76.0)]
        top = 14.0
        for ref in ("J3", "J4", "J5", "J6"):
            conns.append((ref, "R", top))
            top += 31.86 + 6.0
    ext = {}
    for ref, side, top in conns:
        libid = nl.parts[ref]["footprint"]
        x, y, rot, ln = conn_pos(libid, side, top)
        P[ref] = (x, y, rot, False)
        ext[ref] = (side, top, ln)
    return P, frames, ext


def build(project: str, nl: "vc.Netlist") -> tuple[str, dict]:
    nets = nets_of(nl)
    netid, node_net = {}, {}
    for name in sorted(nets):
        netid[name] = len(netid) + 1
        for node in nets[name]:
            node_net[node] = (netid[name], name)
    P, frames, _ = placements(project, nl)
    missing = set(nl.parts) - set(P)
    assert not missing, f"unplaced parts: {sorted(missing)}"
    assert not set(P) - set(nl.parts), f"placed but not in schematic: {sorted(set(P) - set(nl.parts))}"
    title = {"counter_common": "Relay counter board - COMMON (pL = a4, pM, pR, a3, POS)",
             "counter_unit": "Relay counter board - PER UNIT (a2, a1, a3/a4 buffers)"}[project]

    o = ['(kicad_pcb\n\t(version 20241229)\n\t(generator "gen_counter_pcb.py")\n\t(generator_version "1.0")\n'
         '\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper "A2")\n'
         f'\t(title_block\n\t\t(title "{title}")\n\t\t(rev "A")\n'
         '\t\t(comment 1 "Generated by gen_counter_pcb.py - placement only, routing by hand")\n'
         f'\t\t(comment 2 "Netlist from {project}.kicad_sch; 4 layers, G6S-2 THT relays, SMD diodes")\n\t)\n']
    o.append(LAYERS)
    o.append(SETUP)
    o.append('\t(net 0 "")\n')
    for name, i in netid.items():
        o.append(f'\t(net {i} "{name}")\n')
    for ref in sorted(P):
        x, y, rot, hide = P[ref]
        part = nl.parts[ref]
        o.append(place(project, part["footprint"], ref, part["value"], x, y, rot,
                       {p: node_net[(r, p)] for (r, p) in node_net if r == ref}, hide_ref=hide))
    corners = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        o.append(f'\t(gr_line\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n'
                 f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n'
                 f'\t\t(uuid "{uid(project, "edge", k)}")\n\t)\n')
    for k, (x, y, w, h, label) in enumerate(frames):     # block frames (drawing layer) and silk labels
        o.append(f'\t(gr_rect\n\t\t(start {fmt(x)} {fmt(y)})\n\t\t(end {fmt(x + w)} {fmt(y + h)})\n'
                 f'\t\t(stroke\n\t\t\t(width 0.15)\n\t\t\t(type dash)\n\t\t)\n\t\t(fill no)\n\t\t(layer "Dwgs.User")\n'
                 f'\t\t(uuid "{uid(project, "frame", k)}")\n\t)\n')
        o.append(f'\t(gr_text "{label}"\n\t\t(at {fmt(x + 2)} {fmt(y + 2.6)} 0)\n\t\t(layer "F.SilkS")\n'
                 f'\t\t(uuid "{uid(project, "lbl", k)}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.5 1.5)\n'
                 f'\t\t\t\t(thickness 0.2)\n\t\t\t)\n\t\t\t(justify left)\n\t\t)\n\t)\n')
    o.append(")\n")
    return "".join(o), P


LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" signal)
\t\t(6 "In2.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
"""

SETUP = """\t(setup
\t\t(pad_to_mask_clearance 0.05)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
"""


def merge_project(path: Path):
    """Add the board design rules and netclasses to the project file (other keys are kept)."""
    pro = json.loads(path.read_text(encoding="utf8")) if path.exists() else {"meta": {"filename": path.name, "version": 3}}
    rules = {"min_clearance": CLEAR, "min_connection": 0.0, "min_copper_edge_clearance": 0.3,
             "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2,
             "min_microvia_drill": 0.1, "min_resolved_spokes": 2, "min_silk_clearance": 0.0,
             "min_text_height": 0.8, "min_text_thickness": 0.12, "min_through_hole_diameter": VIA_DRILL,
             "min_track_width": TRACK, "min_via_annular_width": 0.125, "min_via_diameter": VIA_D,
             "solder_mask_clearance": 0.05, "solder_mask_min_width": 0.0, "use_height_for_length_calcs": True}
    sev = {k: "error" for k in ("clearance", "copper_edge_clearance", "hole_clearance", "hole_to_hole",
                                "track_width", "via_diameter", "annular_width", "through_hole_diameter",
                                "shorting_items", "unconnected_items", "invalid_outline", "malformed_courtyard",
                                "courtyards_overlap", "text_height", "text_thickness")}
    sev.update({"silk_over_copper": "ignore", "silk_overlap": "ignore", "silk_edge_clearance": "ignore",
                "missing_courtyard": "ignore", "footprint": "ignore", "footprint_type_mismatch": "ignore",
                "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
                "solder_mask_bridge": "ignore", "padstack": "warning"})
    pro["board"] = {"design_settings": {
        "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "silk_line_width": 0.15,
                     "silk_text_size_h": 1.0, "silk_text_size_v": 1.0, "silk_text_thickness": 0.15},
        "diff_pair_dimensions": [], "drc_exclusions": [], "rules": rules, "rule_severities": sev,
        "track_widths": [0.0, TRACK, SIG_TRACK, 0.3, RAIL_TRACK],
        "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": VIA_D, "drill": VIA_DRILL}],
        "zones_allow_external_fillets": False}, "layer_pairs": []}

    def cls(name, track, via_d, via_drill):
        return {"name": name, "clearance": CLEAR, "track_width": track, "via_diameter": via_d,
                "via_drill": via_drill, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "wire_width": 6, "bus_width": 12, "line_style": 0,
                "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"}
    pro["net_settings"] = {"classes": [cls("Default", SIG_TRACK, VIA_D, VIA_DRILL), cls("Rail", RAIL_TRACK, 0.8, 0.4)],
                           "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None,
                           "netclass_patterns": [{"netclass": "Rail", "pattern": p} for p in RAIL_NETS]}
    pro.setdefault("sheets", [])
    pro.setdefault("text_variables", {})
    path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")


def generate(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    for project in ("counter_common", "counter_unit"):
        with tempfile.TemporaryDirectory() as td:
            net = Path(td) / f"{project}.net"
            export_netlist(project, net)
            nl = vc.Netlist(net.read_text(encoding="utf8"))
        text, P = build(project, nl)
        (outdir / f"{project}.kicad_pcb").write_text(text, encoding="utf8", newline="\n")
        merge_project(outdir / f"{project}.kicad_pro")
        print(f"wrote {project}.kicad_pcb ({len(P)} parts incl. {len(HOLES)} plated mounting holes, {BOARD_W:g} x {BOARD_H:g} mm) "
              f"and board rules in {project}.kicad_pro")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
