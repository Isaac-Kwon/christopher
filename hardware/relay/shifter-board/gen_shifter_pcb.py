#!/usr/bin/env python3
"""Generate the KiCad 10 PCB of the relay shifter board: deterministic PLACEMENT only.

Scope (by decision of the project owner): no copper routing.  The board file contains the outline,
mounting holes, all footprints placed and oriented, the nets imported from the schematic netlist
(so the ratsnest is present) and the design rules.  Routing is done later by hand.

    python gen_shifter_pcb.py            # exports the netlist with kicad-cli, writes shifter_board.kicad_pcb
                                         # and merges the board design rules into shifter_board.kicad_pro

Standard library only.  Footprints are copied from the installed KiCad libraries
(env KICAD_SHARE overrides the default install location, env KICAD_CLI the kicad-cli path).

Placement scheme (see README.md): relays in a 5 (stage columns) x 26 (line rows, A at the top) grid,
rotated 90 degrees; per relay a flyback diode left of the coil pins; stage indicator R + LED below each
column; J1/J2/J5 on the left edge, J3/J4 on the right edge.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_shifter as vs  # noqa: E402  (netlist reader)

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
PROJECT = "shifter_board"
NS = uuid.UUID("6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0b02")

# ----------------------------------------------------------------------------- board geometry (mm)
N = 26
BOARD_W, BOARD_H = 250.0, 228.0
ROW_PITCH = 8.0              # G6S-2 courtyard is 7.9 mm across the rotated footprint
ROW0_Y = 14.0                # y of the outbound pad row (pins 1,3,4,5) of line A
COL_X0, COL_PITCH = 38.0, 38.0   # x of relay pin 1/12 column for stage 1; pitch leaves routing channels
CONN_DEPTH = 12.74           # D-sub footprint: board edge is 12.74 mm from the pin-1 row
HOLES = [(5.0, 5.0), (BOARD_W - 5.0, 5.0), (5.0, BOARD_H - 5.0), (BOARD_W - 5.0, BOARD_H - 5.0)]

FP_RELAY = "Relay_THT:Relay_DPDT_Omron_G6S-2"
FP_HOLE = "MountingHole:MountingHole_3.2mm_M3"

# ----------------------------------------------------------------------------- design rules
TRACK, CLEAR, VIA_D, VIA_DRILL = 0.15, 0.15, 0.5, 0.25
COIL_TRACK = 0.6


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


def place(libid: str, ref: str, value: str, x: float, y: float, rot: float,
          pin_net: dict[str, tuple[int, str]], hide_ref=False, flip=False) -> str:
    """Instantiate a library footprint on the board (F.Cu side)."""
    t = fp_text(libid).replace("\r\n", "\n")
    name = libid.split(":")[1]
    # header: drop library-only fields, add board fields
    t = re.sub(r'^\t\(version [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator_version [^)]*\)\n', "", t, flags=re.M)
    t = t.replace(f'(footprint "{name}"', f'(footprint "{libid}"', 1)
    head = f'\t(layer "F.Cu")\n\t(uuid "{uid("fp", ref)}")\n\t(at {fmt(x)} {fmt(y)}{" " + fmt(rot) if rot else ""})\n'
    t, n = re.subn(r'^\t\(layer "F\.Cu"\)\n', head, t, count=1, flags=re.M)
    assert n == 1, libid
    # reference / value / footprint properties
    t = re.sub(r'\(property "Reference" "[^"]*"', f'(property "Reference" "{ref}"', t, count=1)
    t = re.sub(r'\(property "Value" "[^"]*"', f'(property "Value" "{value}"', t, count=1)
    t = re.sub(r'\(property "Footprint" "[^"]*"', f'(property "Footprint" "{libid}"', t, count=1)

    def hide_prop(t, prop):
        i = t.find(f'(property "{prop}"')
        j = block_end(t, i)
        blk = t[i:j]
        if "(hide yes)" not in blk:
            blk = blk.replace("\n\t\t(layer", "\n\t\t(hide yes)\n\t\t(layer", 1) if False else re.sub(
                r'(\(layer "[^"]*"\))', r'\1\n\t\t(hide yes)', blk, count=1)
        return t[:i] + blk + t[j:]
    t = hide_prop(t, "Value")
    if hide_ref:
        t = hide_prop(t, "Reference")

    # absolute angles: pad / text angles in a board file include the footprint rotation
    if rot:
        def addrot(m):
            a = float(m.group(3)) if m.group(3) is not None else 0.0
            return f'{m.group(1)}(at {m.group(2)} {fmt((a + rot) % 360)})'
        t = re.sub(r'(\((?:pad|property|fp_text)[^\n]*\n\t\t)\(at ([-\d.]+ [-\d.]+)(?: ([-\d.]+))?\)', addrot, t)

    # nets on pads
    out, pos = [], 0
    for m in re.finditer(r'\(pad "([^"]*)"', t):
        if m.start() < pos:
            continue
        end = block_end(t, m.start())
        blk = t[m.start():end]
        pn = m.group(1)
        if pn in pin_net:
            nid, nname = pin_net[pn]
            blk = re.sub(r'(\n\t\t\(at [^\n]*\))', lambda mm: mm.group(1) + f'\n\t\t(net {nid} "{nname}")', blk, count=1)
        out.append((m.start(), end, blk))
        pos = end
    for s, e, blk in reversed(out):
        t = t[:s] + blk + t[e:]

    # unique, stable UUIDs for every item of this instance
    k = [0]

    def newuuid(m):
        k[0] += 1
        if k[0] == 1:
            return m.group(0)       # footprint uuid already set
        return f'(uuid "{uid("fp", ref, k[0])}")'
    t = re.sub(r'\(uuid "[^"]*"\)', newuuid, t)
    # indent one level (footprint is a child of kicad_pcb)
    return "\n".join(("\t" + ln if ln else ln) for ln in t.rstrip("\n").split("\n")) + "\n"


# ----------------------------------------------------------------------------- netlist
def export_netlist(path: Path):
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(path),
                    str(HERE / f"{PROJECT}.kicad_sch")], check=True, capture_output=True)


# ----------------------------------------------------------------------------- placement
def placements(nl: "vs.Netlist"):
    """Yield (ref, footprint libid, x, y, rot, hide_ref) for every schematic part."""
    P = {}
    for b in range(5):
        X = COL_X0 + b * COL_PITCH
        for i in range(N):
            Y = ROW0_Y + i * ROW_PITCH
            st = b + 1
            # relay: rotated 90 deg -> pins 1,3,4,5 on the row at Y (x = X, X+5.08, X+7.62, X+10.16),
            # pins 12,10,9,8 on the row Y-5.08
            P[f"K{st}{i + 1:02d}"] = (X, Y, 90, False)
            # flyback diode vertical left of the coil pins, cathode (pad 1) towards pin 1
            P[f"D{st}{i + 1:02d}"] = (X - 3.0, Y - 2.54, 90, True)
        # stage indicator below the column
        P[f"R{st}"] = (X + 1.0, BOARD_H - 7.0, 0, False)
        P[f"DL{st}"] = (X + 7.0, BOARD_H - 7.0, 0, False)
    # connectors: D-sub 37 = 70.4 mm along the edge.  Left edge faces -x (rot 270), right edge faces +x (rot 90).
    top = 12.0
    span = 70.41
    gap = 3.6
    P["J1"] = (CONN_DEPTH, top + 10.28, 270, False)
    P["J2"] = (CONN_DEPTH, top + span + gap + 10.28, 270, False)
    P["J3"] = (BOARD_W - CONN_DEPTH, top + 60.13, 90, False)
    P["J4"] = (BOARD_W - CONN_DEPTH, top + span + gap + 60.13, 90, False)
    # control connector (D-sub 9, 31.7 mm long) below J2 on the left edge
    P["J5"] = (CONN_DEPTH, top + 2 * (span + gap) + 10.28, 270, False)
    return P


def build(nl: "vs.Netlist") -> tuple[str, dict]:
    netid = {}
    node_net: dict[tuple[str, str], tuple[int, str]] = {}
    for name in sorted(nl.nets):
        netid[name] = len(netid) + 1
        for ref, pin in nl.nets[name]:
            node_net[(ref, pin)] = (netid[name], name)
    P = placements(nl)
    missing = set(nl.parts) - set(P)
    assert not missing, f"unplaced parts: {sorted(missing)}"

    o = ["(kicad_pcb\n\t(version 20241229)\n\t(generator \"gen_shifter_pcb.py\")\n\t(generator_version \"1.0\")\n"
         "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A2\")\n"
         "\t(title_block\n\t\t(title \"Relay shifter board (binary 5-stage shifter pair)\")\n\t\t(rev \"A\")\n"
         "\t\t(comment 1 \"Generated by gen_shifter_pcb.py - placement only, routing by hand\")\n"
         "\t\t(comment 2 \"Netlist from shifter_board.kicad_sch; 4 layers, G6S-2 THT relays\")\n\t)\n"]
    o.append(LAYERS)
    o.append(SETUP)
    o.append('\t(net 0 "")\n')
    for name, i in netid.items():
        o.append(f'\t(net {i} "{name}")\n')
    for ref in sorted(P):
        x, y, rot, hide = P[ref]
        part = nl.parts[ref]
        o.append(place(part["footprint"], ref, part["value"], x, y, rot,
                       {p: node_net[(ref, p)] for (r, p) in node_net if r == ref}, hide_ref=hide))
    for k, (x, y) in enumerate(HOLES, 1):
        o.append(place(FP_HOLE, f"H{k}", "MountingHole_3.2mm_M3", x, y, 0, {}, hide_ref=True))
    # outline
    corners = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        o.append(f'\t(gr_line\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n'
                 f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n'
                 f'\t\t(uuid "{uid("edge", k)}")\n\t)\n')
    # stage labels
    for b in range(5):
        X = COL_X0 + b * COL_PITCH
        o.append(f'\t(gr_text "STAGE {1 << b}"\n\t\t(at {fmt(X + 6)} {fmt(ROW0_Y - 9.5)} 0)\n\t\t(layer "F.SilkS")\n'
                 f'\t\t(uuid "{uid("lbl", b)}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.5 1.5)\n\t\t\t\t(thickness 0.2)\n'
                 f'\t\t\t)\n\t\t)\n\t)\n')
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
        "track_widths": [0.0, TRACK, 0.2, 0.3, COIL_TRACK],
        "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": VIA_D, "drill": VIA_DRILL}],
        "zones_allow_external_fillets": False}, "layer_pairs": []}

    def cls(name, track, via_d, via_drill):
        return {"name": name, "clearance": CLEAR, "track_width": track, "via_diameter": via_d,
                "via_drill": via_drill, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "wire_width": 6, "bus_width": 12, "line_style": 0,
                "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"}
    pro["net_settings"] = {"classes": [cls("Default", 0.2, VIA_D, VIA_DRILL), cls("Coil", COIL_TRACK, 0.8, 0.4)],
                           "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None,
                           "netclass_patterns": [{"netclass": "Coil", "pattern": "/CTRL*"},
                                                 {"netclass": "Coil", "pattern": "/GND"}]}
    pro.setdefault("sheets", [])
    pro.setdefault("text_variables", {})
    path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")


def main():
    with tempfile.TemporaryDirectory() as td:
        net = Path(td) / "shifter.net"
        export_netlist(net)
        nl = vs.Netlist(net.read_text(encoding="utf8"))
    text, P = build(nl)
    (HERE / f"{PROJECT}.kicad_pcb").write_text(text, encoding="utf8", newline="\n")
    merge_project(HERE / f"{PROJECT}.kicad_pro")
    print(f"wrote {PROJECT}.kicad_pcb ({len(P)} parts + {len(HOLES)} holes, {BOARD_W} x {BOARD_H} mm) and board rules in the .kicad_pro")


if __name__ == "__main__":
    main()
