#!/usr/bin/env python3
"""Generate the KiCad 10 PCB of the relay shifter board: deterministic PLACEMENT only.

Scope (by decision of the project owner): no copper routing.  The board file contains the outline,
mounting holes, all footprints placed and oriented, the nets imported from the schematic netlist
(so the ratsnest is present) and the design rules.  Routing is done later by hand.

    python gen_shifter_pcb.py            # exports the netlist with kicad-cli, writes shifter_board.kicad_pcb
                                         # and merges the board design rules into shifter_board.kicad_pro
    python gen_shifter_pcb.py --variant s4   # the front-facing a4 board shifter_board_s4.kicad_pcb (+ .kicad_pro)

Standard library only.  Footprints are copied from the installed KiCad libraries
(env KICAD_SHARE overrides the default install location, env KICAD_CLI the kicad-cli path).

Placement scheme (see README.md): relays in a 5 (stage columns) x 26 (line rows, A at the top) grid,
rotated 90 degrees; per relay a flyback diode left of the coil pins; stage indicator R + LED below each
column; J1/J2/J5 on the left edge, J3/J4 on the right edge.

S4 variant (--variant s4): same schematic/netlist, outline, holes and relay grid.  Only J1-J4 change: they are
placed on the BOTTOM side (B.Cu, relays stay on top), mirrored along the edge: the pin row and its span are the
same, but pin 1 sits at the other end of the connector (the y0 values of J1<->J3 and J2<->J4 are exchanged and
the rotations turned by 180 degrees).  Why: a card mounted facing the front is seen from the other side than the
cards facing back, so with the standard footprint pin 1 of its four jumper sockets would be at the wrong end of
every ribbon (wiring-cards/README.md); the mirror is the only placement that turns pin 1 around, because a
D-sub is keyed and cannot be rotated on its own side without pointing the body into the board.  Also the
sockets then face back like those of all other cards.  J5 and everything else is unchanged.
"""
from __future__ import annotations

import argparse
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


def _swap_side(t: str) -> str:
    return re.sub(r'"([FB])\.(\w+)"', lambda m: f'"{"B" if m[1] == "F" else "F"}.{m[2]}"', t)


def _mirror_text(t: str) -> str:
    def fix(m):
        blk = m.group(0)
        if "(justify" in blk:
            return re.sub(r'\(justify ([^)]*)\)', lambda k: f'(justify {k[1]} mirror)' if "mirror" not in k[1] else k[0], blk)
        return blk[:-1].rstrip() + "\n\t\t\t(justify mirror)\n\t\t)" if blk.endswith(")") else blk
    out, pos, res = 0, 0, []
    for m in re.finditer(r'\(effects', t):
        if m.start() < pos:
            continue
        e = block_end(t, m.start())
        res.append(t[pos:m.start()])
        blk = t[m.start():e]
        if "(justify" in blk:
            blk = re.sub(r'\(justify ([^)]*)\)',
                         lambda k: k[0] if "mirror" in k[1] else f'(justify {k[1]} mirror)', blk)
        else:
            blk = blk[:-1].rstrip() + "\n\t\t\t(justify mirror)\n\t\t)"
        res.append(blk)
        pos = e
    res.append(t[pos:])
    return "".join(res)


def _mirror_y(t: str) -> str:
    """Mirror every coordinate of a footprint body in y (what KiCad stores for a back-side footprint)."""
    def at(m):
        a = m.group(4)
        return f'(at {m.group(2)} {fmt(-float(m.group(3)))}' + (f' {fmt((-float(a)) % 360)}' if a else '') + ')'
    t = re.sub(r'\((at) ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', at, t)
    t = re.sub(r'\((start|end|center|mid|xy) ([-\d.]+) ([-\d.]+)\)',
               lambda m: f'({m.group(1)} {m.group(2)} {fmt(-float(m.group(3)))})', t)
    return t


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
    head = (f'\t(layer "{"B.Cu" if flip else "F.Cu"}")\n\t(uuid "{uid("fp", ref)}")\n'
            f'\t(at {fmt(x)} {fmt(y)}{" " + fmt(rot) if rot else ""})\n')
    t, n = re.subn(r'^\t\(layer "F\.Cu"\)\n', head, t, count=1, flags=re.M)
    assert n == 1, libid
    if flip:    # back-side footprint: layers swapped, body mirrored in the library y (pcbnew's Flip), texts mirrored
        body_start = t.index(head) + len(head)
        t = t[:body_start] + _mirror_text(_mirror_y(_swap_side(t[body_start:])))
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
VARIANTS = {"std": PROJECT, "s4": PROJECT + "_s4"}      # variant -> output board name
S4_TEXT = "S4 / a4 - FRONT"


def placements(nl: "vs.Netlist", variant: str = "std"):
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
    if variant == "s4":
        # mirrored along the edge, on B.Cu: same pin-row span (centre), pin 1 at the other end, body still outward
        for a, b in (("J1", "J3"), ("J2", "J4")):
            (xa, ya, ra, _), (xb, yb, rb, _) = P[a], P[b]
            P[a] = (xa, yb, (ra + 180) % 360, False, True)
            P[b] = (xb, ya, (rb + 180) % 360, False, True)
    # mounting holes (plated, net CHASSIS) and the CHASSIS-0 V pair (0003 S2)
    for k, (x, y) in enumerate(HOLES, 1):
        P[f"H{k}"] = (x, y, 0, True)
    P["R6"] = (BOARD_W - 35.0, BOARD_H - 7.0, 0, False)
    P["C1"] = (BOARD_W - 25.0, BOARD_H - 7.0, 0, False)
    # control connector (D-sub 9, 31.7 mm long) below J2 on the left edge
    P["J5"] = (CONN_DEPTH, top + 2 * (span + gap) + 10.28, 270, False)
    return P


def build(nl: "vs.Netlist", variant: str = "std") -> tuple[str, dict]:
    netid = {}
    node_net: dict[tuple[str, str], tuple[int, str]] = {}
    for name in sorted(nl.nets):
        netid[name] = len(netid) + 1
        for ref, pin in nl.nets[name]:
            node_net[(ref, pin)] = (netid[name], name)
    # D-sub shells (footprint pads "SH", not a schematic pin) are CHASSIS (0003 7.1)
    for ref in nl.parts:
        if re.fullmatch(r"J\d", ref):
            node_net[(ref, "SH")] = (netid["/CHASSIS"], "/CHASSIS")
    P = placements(nl, variant)
    missing = set(nl.parts) - set(P)
    assert not missing, f"unplaced parts: {sorted(missing)}"

    o = ["(kicad_pcb\n\t(version 20241229)\n\t(generator \"gen_shifter_pcb.py\")\n\t(generator_version \"1.0\")\n"
         "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A2\")\n"
         "\t(title_block\n\t\t(title \"Relay shifter board (binary 5-stage shifter pair)"
         + (" - S4 variant (a4, front-facing)" if variant == "s4" else "") + "\")\n\t\t(rev \"A\")\n"
         "\t\t(comment 1 \"Generated by gen_shifter_pcb.py - placement only, routing by hand\")\n"
         "\t\t(comment 2 \"Netlist from shifter_board.kicad_sch; 4 layers, G6S-2 THT relays\")\n"
         + ('\t\t(comment 3 \"S4 variant: J1-J4 mirrored on B.Cu so that pin 1 of every stack jumper is aligned\")\n'
            if variant == "s4" else "") + "\t)\n"]
    o.append(LAYERS)
    o.append(SETUP)
    o.append('\t(net 0 "")\n')
    for name, i in netid.items():
        o.append(f'\t(net {i} "{name}")\n')
    for ref in sorted(P):
        x, y, rot, hide, *fl = P[ref]
        part = nl.parts[ref]
        o.append(place(part["footprint"], ref, part["value"], x, y, rot,
                       {p: node_net[(ref, p)] for (r, p) in node_net if r == ref}, hide_ref=hide, flip=bool(fl and fl[0])))
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
    if variant == "s4":     # unmistakable marking on the component side, in the free strip right of stage 16
        for k, (txt, size, thick, cx) in enumerate(((S4_TEXT, 4.0, 0.5, 218.0),
                                                    ("J1-J4 MIRRORED ON B.Cu: PIN 1 AT THE OTHER END", 1.5, 0.2, 226.0))):
            o.append(f'\t(gr_text "{txt}"\n\t\t(at {fmt(cx)} {fmt(BOARD_H / 2)} 90)\n\t\t(layer "F.SilkS")\n'
                     f'\t\t(uuid "{uid("s4txt", k)}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {fmt(size)} {fmt(size)})\n'
                     f'\t\t\t\t(thickness {fmt(thick)})\n\t\t\t)\n\t\t)\n\t)\n')
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="std",
                    help="std: shifter_board.kicad_pcb (S1-S3); s4: shifter_board_s4.kicad_pcb (front-facing a4)")
    a = ap.parse_args()
    name = VARIANTS[a.variant]
    with tempfile.TemporaryDirectory() as td:
        net = Path(td) / "shifter.net"
        export_netlist(net)
        nl = vs.Netlist(net.read_text(encoding="utf8"))
    text, P = build(nl, a.variant)
    (HERE / f"{name}.kicad_pcb").write_text(text, encoding="utf8", newline="\n")
    merge_project(HERE / f"{name}.kicad_pro")
    print(f"wrote {name}.kicad_pcb ({len(P)} parts incl. {len(HOLES)} plated mounting holes, {BOARD_W} x {BOARD_H} mm) and board rules in the .kicad_pro")


if __name__ == "__main__":
    main()
