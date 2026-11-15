#!/usr/bin/env python3
"""Generate manufacturable KiCad drum PCBs for Enigma rotors from data/wirings/enigma.txt.

Standard library only.  One board (.kicad_pcb + .kicad_pro) per rotor.
Conventions (see README.md):
  * 4 rings x 26 pads on F.Cu, ring 1 = innermost (r 22) ... ring 4 = outermost (r 43).
  * ring 4 / ring 1 = rotor ENTRY side, ring 3 / ring 2 = rotor REFLECTOR side.
  * copy A: ring4[i] <-> ring3[W[i]]    copy B: ring1[i] <-> ring2[W[i]]    (W = wiring table)
  * pad i sits at angle i*360/26 from the index mark (letter A, 12 o'clock), clockwise
    as seen from the pad (contact) side.
Routing: pad -> F.Cu stub -> via (in the pad-free band beside the ring) -> In1.Cu radial track
(inside a pad-free "gap column" between two pads) -> via -> In2.Cu arc on a private lane -> via ->
In1.Cu radial -> via -> F.Cu stub -> pad.  B.Cu carries silkscreen only.
"""
import argparse
import json
import math
import random
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WIRING_FILE = REPO / "data" / "wirings" / "enigma.txt"

# ----------------------------------------------------------------- geometry
N = 26
DELTA = 360.0 / N
CENTER = (150.0, 100.0)            # board centre in the KiCad sheet (mm)
R_BOARD = 48.0                     # 96 mm drum
RING_R = {1: 22.0, 2: 29.0, 3: 36.0, 4: 43.0}
PAD_L = 3.6                        # radial pad length
PAD_ARC_FRAC = 0.5                 # pad width = 50 % of ring pitch (concept.md section 2)
BORE_D = 8.0                       # centre bore (provisional)
HUB_HOLE_R, HUB_HOLE_D, HUB_N = 7.5, 3.2, 4   # M3 clearance holes, at 45+90k deg
DOWEL_D = 2.0                      # index (dowel) hole at letter A, same radius
NOTCH_R = 1.2                      # key notch radius on the rim
LETTER_R = 46.3

# fab rules (all mm)
TRACK = 0.15
CLEAR = 0.15
VIA_D, VIA_DRILL = 0.6, 0.3
EDGE_CLEAR = 0.3
COL_OFF = 0.45                     # tangential offset of the two tracks in a gap column
LANE_PITCH = 0.62
LANE_GAP_MM = 1.0                  # min distance between arc ends sharing a lane

# via-band radii (pad-free annuli next to the rings) and lane ranges per copy
COPIES = {
    "A": dict(entry=4, refl=3, band={4: 40.5, 3: 38.5}, lane_top=37.4, lane_bot=28.4),
    "B": dict(entry=1, refl=2, band={1: 24.5, 2: 26.5}, lane_top=22.8, lane_bot=11.4),
}

LAYER_RADIAL = "In1.Cu"
LAYER_ARC = "In2.Cu"


def fmt(v):
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def uid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "christopher-drum/" + "/".join(map(str, parts))))


# ----------------------------------------------------------------- wiring
def load_wiring(name):
    for line in WIRING_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if f[0] == "rotor" and f[1] == name:
            w = [ord(c) - 65 for c in f[2]]
            assert sorted(w) == list(range(N)), "wiring is not a permutation"
            return w, f[2], f[3]
    raise SystemExit(f"rotor {name} not found in {WIRING_FILE}")


def all_rotors():
    out = []
    for line in WIRING_FILE.read_text(encoding="utf-8").splitlines():
        f = line.split()
        if f and f[0] == "rotor":
            out.append(f[1])
    return out


# ----------------------------------------------------------------- coordinate helpers
class Frame:
    """Maps (radius, letter-angle psi) to sheet coordinates.  psi grows with the letter index;
    direction +1 means clockwise as seen from the pad side (KiCad top view)."""

    def __init__(self, direction=1):
        self.dir = direction

    def phi(self, psi):
        return math.radians(self.dir * psi)

    def polar(self, r, psi):
        p = self.phi(psi)
        return (CENTER[0] + r * math.sin(p), CENTER[1] - r * math.cos(p))

    def local(self, col_psi, u, v):
        """Point at tangential offset u (towards growing psi) and axis distance v from the
        board centre, along the column whose axis is at letter-angle col_psi."""
        p = self.phi(col_psi)
        er = (math.sin(p), -math.cos(p))
        et = (math.cos(p) * self.dir, math.sin(p) * self.dir)
        return (CENTER[0] + v * er[0] + u * et[0], CENTER[1] + v * er[1] + u * et[1])

    def kicad_rot(self, psi):
        return -self.dir * psi          # long axis / text-up pointing radially outwards


def pad_width(ring):
    return PAD_ARC_FRAC * 2 * math.pi * RING_R[ring] / N


# ----------------------------------------------------------------- lane packing
class Net:
    def __init__(self, nid, name, copy, i, j):
        self.id, self.name, self.copy = nid, name, copy
        self.i, self.j = i, j           # entry pad index, reflector pad index
        self.entry = dict(col=i % N, sign=-1)                # right-hand gap column of pad i
        self.refl = dict(col=(j - 1) % N, sign=+1)           # left-hand gap column of pad j
        self.lane_r = None
        self.cw = None


def end_psi(end, rho):
    v = math.sqrt(rho * rho - COL_OFF * COL_OFF)
    return (end["col"] + 0.5) * DELTA + math.degrees(math.atan2(end["sign"] * COL_OFF, v))


def interval(net, rho, cw):
    a, b = end_psi(net.entry, rho), end_psi(net.refl, rho)
    if cw:
        return a % 360, (b - a) % 360
    return b % 360, (a - b) % 360


def clash(i1, i2, gap):
    """Circular intervals (start, length) conflict unless separated by >= gap degrees both ways."""
    s1, l1 = i1
    s2, l2 = i2
    return not (((s2 - s1) % 360) >= l1 + gap and ((s1 - s2) % 360) >= l2 + gap)


def pack_lanes(nets, lanes, seed):
    """First-fit assignment of nets (each in either direction) to lane radii."""
    order = sorted(nets, key=lambda n: -min(interval(n, lanes[0], True)[1], interval(n, lanes[0], False)[1]))
    if seed:
        random.Random(seed).shuffle(order)
    used = [[] for _ in lanes]
    assign = {}
    for n in order:
        placed = False
        for li, rho in enumerate(lanes):
            gap = math.degrees(LANE_GAP_MM / rho)
            opts = [True, False]
            if interval(n, rho, False)[1] < interval(n, rho, True)[1]:
                opts = [False, True]
            for cw in opts:
                iv = interval(n, rho, cw)
                if all(not clash(iv, o, gap) for o in used[li]):
                    used[li].append(iv)
                    assign[n.id] = (li, cw)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            return None
    return assign


def assign_lanes(nets, cfg):
    n_lanes = int((cfg["lane_top"] - cfg["lane_bot"]) / LANE_PITCH) + 1
    lanes = [cfg["lane_top"] - k * LANE_PITCH for k in range(n_lanes)]
    best = None
    for seed in range(200):
        a = pack_lanes(nets, lanes, seed)
        if a is None:
            continue
        k = max(li for li, _ in a.values()) + 1
        if best is None or k < best[0]:
            best = (k, a)
    if best is None:
        raise SystemExit("lane packing failed: not enough lanes")
    for n in nets:
        li, cw = best[1][n.id]
        n.lane_r, n.cw = lanes[li], cw
    return best[0], n_lanes


# ----------------------------------------------------------------- S-expression emitters
def sexp_segment(a, b, layer, net, key):
    return (f"\t(segment\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n"
            f"\t\t(width {fmt(TRACK)})\n\t\t(layer \"{layer}\")\n\t\t(net {net})\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


def sexp_arc(a, m, b, layer, net, key):
    return (f"\t(arc\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(mid {fmt(m[0])} {fmt(m[1])})\n"
            f"\t\t(end {fmt(b[0])} {fmt(b[1])})\n\t\t(width {fmt(TRACK)})\n\t\t(layer \"{layer}\")\n"
            f"\t\t(net {net})\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


def sexp_via(p, net, key):
    return (f"\t(via\n\t\t(at {fmt(p[0])} {fmt(p[1])})\n\t\t(size {fmt(VIA_D)})\n\t\t(drill {fmt(VIA_DRILL)})\n"
            f"\t\t(layers \"F.Cu\" \"B.Cu\")\n\t\t(net {net})\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


def sexp_text(text, at, rot, layer, size, thick, key, mirror=False):
    j = "\n\t\t\t(justify mirror)" if mirror else ""
    return (f"\t(gr_text \"{text}\"\n\t\t(at {fmt(at[0])} {fmt(at[1])} {fmt(rot)})\n\t\t(layer \"{layer}\")\n"
            f"\t\t(uuid \"{uid(*key)}\")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {fmt(size)} {fmt(size)})\n"
            f"\t\t\t\t(thickness {fmt(thick)})\n\t\t\t){j}\n\t\t)\n\t)\n")


def sexp_poly(pts, layer, key):
    p = " ".join(f"(xy {fmt(x)} {fmt(y)})" for x, y in pts)
    return (f"\t(gr_poly\n\t\t(pts {p})\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type solid)\n\t\t)\n"
            f"\t\t(fill yes)\n\t\t(layer \"{layer}\")\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


def sexp_edge_arc(a, m, b, key):
    return (f"\t(gr_arc\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(mid {fmt(m[0])} {fmt(m[1])})\n"
            f"\t\t(end {fmt(b[0])} {fmt(b[1])})\n\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n"
            f"\t\t(layer \"Edge.Cuts\")\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


def sexp_edge_circle(c, r, key):
    return (f"\t(gr_circle\n\t\t(center {fmt(c[0])} {fmt(c[1])})\n\t\t(end {fmt(c[0] + r)} {fmt(c[1])})\n"
            f"\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(fill no)\n"
            f"\t\t(layer \"Edge.Cuts\")\n\t\t(uuid \"{uid(*key)}\")\n\t)\n")


HEADER_LAYERS = """\t(layers
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
\t\t(stackup
\t\t\t(layer "F.SilkS"
\t\t\t\t(type "Top Silk Screen")
\t\t\t\t(color "White")
\t\t\t)
\t\t\t(layer "F.Paste"
\t\t\t\t(type "Top Solder Paste")
\t\t\t)
\t\t\t(layer "F.Mask"
\t\t\t\t(type "Top Solder Mask")
\t\t\t\t(color "Black")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2104)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In1.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.0175)
\t\t\t)
\t\t\t(layer "dielectric 2"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.065)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In2.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.0175)
\t\t\t)
\t\t\t(layer "dielectric 3"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2104)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "B.Mask"
\t\t\t\t(type "Bottom Solder Mask")
\t\t\t\t(color "Black")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "B.Paste"
\t\t\t\t(type "Bottom Solder Paste")
\t\t\t)
\t\t\t(layer "B.SilkS"
\t\t\t\t(type "Bottom Silk Screen")
\t\t\t\t(color "White")
\t\t\t)
\t\t\t(copper_finish "Hard Gold")
\t\t\t(dielectric_constraints no)
\t\t)
\t\t(pad_to_mask_clearance 0.05)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
"""


def project_json(rotor):
    rules = {
        "min_clearance": CLEAR, "min_connection": 0.0, "min_copper_edge_clearance": EDGE_CLEAR,
        "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2,
        "min_microvia_drill": 0.1, "min_resolved_spokes": 2, "min_silk_clearance": 0.0,
        "min_text_height": 0.8, "min_text_thickness": 0.12, "min_through_hole_diameter": VIA_DRILL,
        "min_track_width": TRACK, "min_via_annular_width": 0.125, "min_via_diameter": VIA_D,
        "solder_mask_clearance": 0.05, "solder_mask_min_width": 0.0, "use_height_for_length_calcs": True,
    }
    sev = {k: "error" for k in (
        "clearance", "copper_edge_clearance", "hole_clearance", "hole_to_hole", "track_width",
        "via_diameter", "annular_width", "through_hole_diameter", "shorting_items", "unconnected_items",
        "invalid_outline", "malformed_courtyard", "text_height", "text_thickness", "min_resolved_spokes")}
    sev.update({"silk_over_copper": "warning", "silk_overlap": "warning", "silk_edge_clearance": "warning",
                "courtyards_overlap": "ignore", "missing_courtyard": "ignore", "footprint": "ignore",
                "footprint_type_mismatch": "ignore", "lib_footprint_issues": "ignore",
                "lib_footprint_mismatch": "ignore", "padstack": "warning", "solder_mask_bridge": "ignore"})
    return {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": TRACK,
                         "silk_line_width": 0.15, "silk_text_size_h": 1.0, "silk_text_size_v": 1.0,
                         "silk_text_thickness": 0.15},
            "diff_pair_dimensions": [], "drc_exclusions": [], "rules": rules, "rule_severities": sev,
            "track_widths": [0.0, TRACK], "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                                              {"diameter": VIA_D, "drill": VIA_DRILL}],
            "zones_allow_external_fillets": False},
            "layer_pairs": []},
        "meta": {"filename": f"drum_{rotor}.kicad_pro", "version": 3},
        "net_settings": {"classes": [{"name": "Default", "clearance": CLEAR, "track_width": TRACK,
                                      "via_diameter": VIA_D, "via_drill": VIA_DRILL,
                                      "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                                      "diff_pair_width": 0.2, "microvia_diameter": 0.3,
                                      "microvia_drill": 0.1, "wire_width": 6, "bus_width": 12,
                                      "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
                                      "schematic_color": "rgba(0, 0, 0, 0.000)"}],
                         "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None,
                         "netclass_patterns": []},
        "sheets": [], "text_variables": {},
    }


# ----------------------------------------------------------------- board assembly
def build_board(rotor, direction=1):
    W, wiring_str, turnover = load_wiring(rotor)
    fr = Frame(direction)
    out = []
    nets = []
    netid = 0
    pad_net = {}                           # (ring, idx) -> Net
    lane_info = {}
    for copy, cfg in COPIES.items():
        cn = []
        for i in range(N):
            netid += 1
            n = Net(netid, f"{cfg['entry']}{chr(65 + i)}-{cfg['refl']}{chr(65 + W[i])}", copy, i, W[i])
            cn.append(n)
            pad_net[(cfg["entry"], i)] = n
            pad_net[(cfg["refl"], W[i])] = n
        lane_info[copy] = assign_lanes(cn, cfg)
        nets += cn

    out.append("(kicad_pcb\n\t(version 20241229)\n\t(generator \"gen_drum.py\")\n\t(generator_version \"1.0\")\n"
               "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A4\")\n")
    out.append(f"\t(title_block\n\t\t(title \"Bombe drum - Enigma rotor {rotor}\")\n"
               f"\t\t(comment 1 \"Generated by gen_drum.py from data/wirings/enigma.txt - do not edit by hand\")\n"
               f"\t\t(comment 2 \"Wiring {wiring_str}\")\n\t)\n")
    out.append(HEADER_LAYERS)
    out.append(SETUP)
    out.append("\t(net 0 \"\")\n")
    for n in nets:
        out.append(f"\t(net {n.id} \"{n.name}\")\n")

    # ---- pads footprint
    eff = "(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)"
    fp = [f"\t(footprint \"Christopher:DRUM_PADS\"\n\t\t(layer \"F.Cu\")\n\t\t(uuid \"{uid(rotor, 'fp')}\")\n"
          f"\t\t(at {fmt(CENTER[0])} {fmt(CENTER[1])})\n"
          f"\t\t(property \"Reference\" \"DRUM1\"\n\t\t\t(at 0 0 0)\n\t\t\t(layer \"F.Fab\")\n\t\t\t(hide yes)\n"
          f"\t\t\t(uuid \"{uid(rotor, 'ref')}\")\n\t\t\t{eff}\n\t\t)\n"
          f"\t\t(property \"Value\" \"ROTOR-{rotor}\"\n\t\t\t(at 0 0 0)\n\t\t\t(layer \"F.Fab\")\n\t\t\t(hide yes)\n"
          f"\t\t\t(uuid \"{uid(rotor, 'val')}\")\n\t\t\t{eff}\n\t\t)\n"
          f"\t\t(attr smd board_only exclude_from_pos_files exclude_from_bom)\n"]
    for ring in (1, 2, 3, 4):
        w = pad_width(ring)
        rr = min(0.3 / min(w, PAD_L), 0.25)
        for i in range(N):
            x, y = fr.polar(RING_R[ring], i * DELTA)
            nt = pad_net[(ring, i)]
            fp.append(f"\t\t(pad \"{ring}{chr(65 + i)}\" smd roundrect\n"
                      f"\t\t\t(at {fmt(x - CENTER[0])} {fmt(y - CENTER[1])} {fmt(fr.kicad_rot(i * DELTA))})\n"
                      f"\t\t\t(size {fmt(w)} {fmt(PAD_L)})\n\t\t\t(layers \"F.Cu\" \"F.Mask\")\n"
                      f"\t\t\t(roundrect_rratio {fmt(rr)})\n"
                      f"\t\t\t(net {nt.id} \"{nt.name}\")\n\t\t\t(uuid \"{uid(rotor, 'pad', ring, i)}\")\n\t\t)\n")
    holes = []
    for k in range(HUB_N):
        ang = 45 + 90 * k
        holes.append((CENTER[0] + HUB_HOLE_R * math.sin(math.radians(ang)),
                      CENTER[1] - HUB_HOLE_R * math.cos(math.radians(ang)), HUB_HOLE_D, f"hub{k}"))
    x, y = fr.polar(HUB_HOLE_R, 0.0)
    holes.append((x, y, DOWEL_D, "dowel"))
    for x, y, d, name in holes:
        fp.append(f"\t\t(pad \"\" np_thru_hole circle\n\t\t\t(at {fmt(x - CENTER[0])} {fmt(y - CENTER[1])})\n"
                  f"\t\t\t(size {fmt(d)} {fmt(d)})\n\t\t\t(drill {fmt(d)})\n"
                  f"\t\t\t(layers \"*.Cu\" \"*.Mask\")\n\t\t\t(uuid \"{uid(rotor, name)}\")\n\t\t)\n")
    fp.append("\t)\n")
    out.append("".join(fp))

    # ---- tracks and vias
    s = COL_OFF
    stats = {"via": 0, "seg": 0, "arc": 0}

    def end_geometry(n, which, cfg):
        e = n.entry if which == "entry" else n.refl
        ring = cfg["entry"] if which == "entry" else cfg["refl"]
        idx = n.i if which == "entry" else n.j
        rb = cfg["band"][ring]
        col_psi = (e["col"] + 0.5) * DELTA
        vb = math.sqrt(rb * rb - s * s)
        vl = math.sqrt(n.lane_r ** 2 - s * s)
        P0 = fr.polar(RING_R[ring], idx * DELTA)
        P1 = fr.polar(rb, idx * DELTA)
        V1 = fr.local(col_psi, e["sign"] * s, vb)
        psi_v1 = col_psi + math.degrees(math.atan2(e["sign"] * s, vb))
        d = (psi_v1 - idx * DELTA + 180) % 360 - 180
        Pm = fr.polar(rb, idx * DELTA + d / 2)
        V2 = fr.local(col_psi, e["sign"] * s, vl)
        psi_v2 = col_psi + math.degrees(math.atan2(e["sign"] * s, vl))
        return dict(P0=P0, P1=P1, Pm=Pm, V1=V1, V2=V2, psi_v2=psi_v2)

    for n in nets:
        cfg = COPIES[n.copy]
        g = {w: end_geometry(n, w, cfg) for w in ("entry", "refl")}
        for w, gg in g.items():
            key = (rotor, "net", n.id, w)
            out.append(sexp_segment(gg["P0"], gg["P1"], "F.Cu", n.id, key + ("stub",)))
            out.append(sexp_arc(gg["P1"], gg["Pm"], gg["V1"], "F.Cu", n.id, key + ("stubarc",)))
            out.append(sexp_via(gg["V1"], n.id, key + ("via1",)))
            out.append(sexp_segment(gg["V1"], gg["V2"], LAYER_RADIAL, n.id, key + ("rad",)))
            out.append(sexp_via(gg["V2"], n.id, key + ("via2",)))
            stats["seg"] += 2
            stats["arc"] += 1
            stats["via"] += 2
        pa, pb = g["entry"]["psi_v2"], g["refl"]["psi_v2"]
        span = ((pb - pa) % 360) if n.cw else ((pa - pb) % 360)
        mid = pa + span / 2 if n.cw else pa - span / 2
        out.append(sexp_arc(g["entry"]["V2"], fr.polar(n.lane_r, mid), g["refl"]["V2"], LAYER_ARC, n.id,
                            (rotor, "net", n.id, "lane")))
        stats["arc"] += 1

    # ---- outline: rim with key notch between letters Z and A, centre bore
    notch_psi = -DELTA / 2
    ang_off = math.degrees(math.acos(1 - NOTCH_R ** 2 / (2 * R_BOARD ** 2)))
    Pa = fr.polar(R_BOARD, notch_psi - ang_off)
    Pb = fr.polar(R_BOARD, notch_psi + ang_off)
    Pn = fr.polar(R_BOARD - NOTCH_R, notch_psi)
    Pfar = fr.polar(R_BOARD, notch_psi + 180)
    out.append(sexp_edge_arc(Pb, Pfar, Pa, (rotor, "edge", "rim")))
    out.append(sexp_edge_arc(Pa, Pn, Pb, (rotor, "edge", "notch")))
    out.append(sexp_edge_circle(CENTER, BORE_D / 2, (rotor, "edge", "bore")))

    # ---- silkscreen: letters A..Z on the pad side, index triangle, names
    for i in range(N):
        x, y = fr.polar(LETTER_R, i * DELTA)
        out.append(sexp_text(chr(65 + i), (x, y), fr.kicad_rot(i * DELTA), "F.SilkS", 1.6, 0.2, (rotor, "letter", i)))
    tri = [fr.local(0, -1.0, 16.4), fr.local(0, 1.0, 16.4), fr.local(0, 0, 19.4)]
    out.append(sexp_poly(tri, "F.SilkS", (rotor, "tri")))
    out.append(sexp_text(f"ROTOR {rotor}", (CENTER[0], CENTER[1] + 14.0), 0, "F.SilkS", 2.0, 0.25, (rotor, "name")))
    back = [f"ROTOR {rotor}  (BACK - NO CONTACTS)",
            f"WIRING {wiring_str}", f"TURNOVER {turnover}",
            "PADS ON FRONT. A=INDEX, LETTERS CW SEEN FROM PAD SIDE",
            "RINGS 1-4 = R22/29/36/43. 4-3 AND 1-2 = ROTOR WIRING"]
    for k, t in enumerate(back):
        out.append(sexp_text(t, (CENTER[0], CENTER[1] + 21.0 + 3.6 * k), 0, "B.SilkS", 1.2, 0.18,
                             (rotor, "back", k), mirror=True))
    out.append(")\n")
    info = {"nets": len(nets), "vias": stats["via"], "segments": stats["seg"], "arcs": stats["arc"],
            "lanes_used/avail": lane_info}
    return "".join(out), info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rotors", nargs="*", help="rotor names (default I II III IV V)")
    ap.add_argument("--all", action="store_true", help="all rotors in enigma.txt (I..VIII)")
    ap.add_argument("--out", default=str(HERE / "boards"), help="output directory")
    ap.add_argument("--letters-ccw", action="store_true",
                    help="letters increase counter-clockwise seen from the pad side (mirror image)")
    a = ap.parse_args(argv)
    rotors = all_rotors() if a.all else (a.rotors or ["I", "II", "III", "IV", "V"])
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for r in rotors:
        text, info = build_board(r, -1 if a.letters_ccw else 1)
        (outdir / f"drum_{r}.kicad_pcb").write_text(text, encoding="utf-8", newline="\n")
        (outdir / f"drum_{r}.kicad_pro").write_text(json.dumps(project_json(r), indent=2) + "\n",
                                                   encoding="utf-8", newline="\n")
        print(f"rotor {r}: {info}")


if __name__ == "__main__":
    main()
