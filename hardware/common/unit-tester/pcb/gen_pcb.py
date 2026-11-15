#!/usr/bin/env python3
"""Create unit_tester.kicad_pcb: outline, mounting holes, footprints placed by function, netlist assigned.

Run with the Python that ships with KiCad (needs the `pcbnew` module):

    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_pcb.py            # write unit_tester.kicad_pcb
    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_pcb.py --check    # verify the placed board (see check())

Steps: export the netlist of unit_tester.kicad_sch with kicad-cli, load every footprint from the installed
KiCad footprint libraries (env KICAD_SHARE overrides), place them, assign pad nets, write the board, then
update unit_tester.kicad_pro (net classes FIELD / MCU) and unit_tester.kicad_dru (isolation clearance rule).
No routing is done (unrouted board).

Floor plan (mm, origin top-left, y down):
    y < YB-5   MCU domain (USB, 3.3 V): Pico (USB at the left edge), 74HC165 chain, opto-output pull-downs
    YB         isolation barrier row: ISO7340 x2, 6N137, 19 x TLP291-4, all straddling a copper-free band
    y > YB+5   FIELD domain (24 V adapter): sense R/D, 1k drive resistors, TBD62783A + 74HC595 per node
               group, power block at the left, J_A / J_B / J_C D-sub 37 at the bottom edge
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_netlist as chk  # noqa: E402

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
PROJECT = "unit_tester"

# ----------------------------------------------------------------------------- floor plan constants
W, H = 290.0, 116.0          # board size
YB = 50.0                    # barrier row centre line
X0 = 14.0                    # first barrier slot left edge
PITCH = 11.4                 # barrier device pitch (TLP291-4 courtyard 10.9 + 0.5)
CH = 2.85                    # channel column pitch under / over an optocoupler
N_NODES = 52
BARRIER_GAP_MM = 4.5         # DRC rule FIELD <-> MCU clearance (TLP291-4 pad gap 4.9, ISO7340 7.25, DIP-8 6.0)


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def slot_cx(s: int) -> float:
    return X0 + PITCH * s + PITCH / 2


# ----------------------------------------------------------------------------- netlist
def load_netlist():
    nlp = HERE / "unit_tester_nl.xml"
    chk.export_netlist(nlp)
    nl = chk.Netlist(nlp)
    root = ET.parse(nlp).getroot()
    paths = {}
    for c in root.find("components"):
        sp = c.find("sheetpath").get("tstamps") if c.find("sheetpath") is not None else "/"
        ts = (c.findtext("tstamps") or "").split()
        paths[c.get("ref")] = (sp + (ts[0] if ts else "")).replace("//", "/")
    nlp.unlink()
    return nl, paths


# ----------------------------------------------------------------------------- footprint helpers
_courtyard: dict[str, tuple[float, float, float, float]] = {}


def load_fp(fpid: str) -> pcbnew.FOOTPRINT:
    lib, name = fpid.split(":")
    fp = pcbnew.FootprintLoad(str(FPDIR / f"{lib}.pretty"), name)
    if fp is None:
        raise SystemExit(f"footprint {fpid} not found")
    return fp


def cy_bbox(fp) -> tuple[float, float, float, float]:
    """courtyard bbox (l, t, r, b) in mm relative to the footprint origin (unrotated)."""
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    if cy.IsEmpty():
        bb = fp.GetBoundingBox(False)
    else:
        bb = cy.BBox()
    return (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))


def rot_pt(x, y, a):
    """KiCad footprint rotation (positive = counter-clockwise on screen, y down)."""
    r = math.radians(a)
    return (x * math.cos(r) + y * math.sin(r), -x * math.sin(r) + y * math.cos(r))


class Placer:
    def __init__(self, board, nl, paths):
        self.board, self.nl, self.paths = board, nl, paths
        self.fps: dict[str, pcbnew.FOOTPRINT] = {}
        self.angles: dict[str, float] = {}
        self.cyb: dict[str, tuple] = {}
        self.boxes: dict[str, tuple[float, float, float, float]] = {}   # absolute courtyard boxes
        self.netinfo: dict[str, pcbnew.NETINFO_ITEM] = {}

    def netitem(self, name):
        if name not in self.netinfo:
            it = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(it)
            self.netinfo[name] = it
        return self.netinfo[name]

    def make(self, ref):
        c = self.nl.comp[ref]
        fp = load_fp(c["fp"])
        self.cyb[ref] = cy_bbox(fp)          # pristine footprint: origin at 0, unrotated
        fp.SetFPIDAsString(c["fp"])
        for fname, val in (("Description", c["desc"]), ("Datasheet", c["ds"])):
            fld = fp.GetField(fname)
            if fld is not None:
                fld.SetText(val)
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        p = self.paths.get(ref)
        if p:
            fp.SetPath(pcbnew.KIID_PATH(p))
        for pad in fp.Pads():
            net = self.nl.net(ref, pad.GetNumber())
            if pad.GetNumber() == "SH" and ref in ("J1", "J2", "J3"):
                net = "CHASSIS_F"          # D-sub shells (pads SH have no schematic pin) = CHASSIS_F (0003 T7)
            if net:
                pad.SetNet(self.netitem(net))
        # small reference text
        fp.Reference().SetTextSize(V(0.8, 0.8))
        fp.Reference().SetTextThickness(mm(0.12))
        fp.Value().SetVisible(False)
        if ref not in ("U1", "U80", "J1", "J2", "J3", "J4", "J5", "J6"):   # dense board: designators on the fab layer
            fp.Reference().SetLayer(pcbnew.F_Fab)
        if c["dnp"]:
            fp.SetDNP(True)
        self.board.Add(fp)
        self.fps[ref] = fp
        return fp

    def put(self, ref, cx, cy, angle=0, anchor="court"):
        """place footprint so that its courtyard centre is at (cx, cy)."""
        fp = self.fps[ref] if ref in self.fps else self.make(ref)
        l, t, r, b = self.cyb[ref]
        ox, oy = rot_pt((l + r) / 2, (t + b) / 2, angle)
        fp.SetOrientationDegrees(angle)
        fp.SetPosition(V(cx - ox, cy - oy))
        self.angles[ref] = angle
        # absolute courtyard box (axis-aligned rotation)
        pts = [rot_pt(x, y, angle) for x in (l, r) for y in (t, b)]
        px, py = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
        self.boxes[ref] = (px + min(p[0] for p in pts), py + min(p[1] for p in pts),
                           px + max(p[0] for p in pts), py + max(p[1] for p in pts))
        return fp

    def size(self, ref, angle):
        if ref not in self.fps:
            self.make(ref)
        l, t, r, b = self.cyb[ref]
        pts = [rot_pt(x, y, angle) for x in (l, r) for y in (t, b)]
        return (max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))

    def pad_rel(self, ref, number):
        """pad position relative to the courtyard centre (mm), from footprint-local pad coordinates."""
        fp = self.fps[ref]
        bx = self.boxes[ref]
        cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        a = self.angles[ref]
        for p in fp.Pads():
            if p.GetNumber() == number:
                lp = p.GetFPRelativePosition()
                rx, ry = rot_pt(lp.x / 1e6, lp.y / 1e6, a)
                return (fp.GetPosition().x / 1e6 + rx - cx, fp.GetPosition().y / 1e6 + ry - cy)
        raise KeyError(number)

    def put_oriented(self, ref, cx, cy, pad, sign):
        """rotate by 90/270 so that pad `pad` lies on side `sign` (+1 = larger y) of the courtyard centre."""
        for a in (90, 270):
            self.put(ref, cx, cy, a)
            d = self.pad_rel(ref, pad)
            if (d[1] > 0) == (sign > 0) and abs(d[1]) > 1.0:
                return a
        raise SystemExit(f"no orientation for {ref}")


class Shelf:
    """tiny shelf packer for the free-form parts"""

    def __init__(self, pl: Placer, x0, y0, x1, y1, gap=0.6):
        self.pl, self.x0, self.y0, self.x1, self.y1, self.gap = pl, x0, y0, x1, y1, gap
        self.x, self.y, self.rowh = x0, y0, 0.0

    def add(self, ref, angle=0):
        w, h = self.pl.size(ref, angle)
        if self.x + w > self.x1 + 1e-6:
            self.x, self.y, self.rowh = self.x0, self.y + self.rowh + self.gap, 0.0
        if self.y + h > self.y1 + 1e-6:
            raise SystemExit(f"shelf overflow placing {ref} (region {self.x0},{self.y0}-{self.x1},{self.y1})")
        self.pl.put(ref, self.x + w / 2, self.y + h / 2, angle)
        self.x += w + self.gap
        self.rowh = max(self.rowh, h)


# ----------------------------------------------------------------------------- the floor plan
def build_board() -> pcbnew.BOARD:
    nl, paths = load_netlist()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    pl = Placer(board, nl, paths)

    # ---- barrier row ------------------------------------------------------------------------
    pl.put_oriented("U2", slot_cx(0), YB, "9", +1)      # ISO7340C: pins 9-16 = field side (down)
    pl.put_oriented("U3", slot_cx(1), YB, "9", +1)
    pl.put_oriented("U4", slot_cx(2), YB, "1", +1)      # 6N137: pins 1-4 = LED (field, down)
    for p in range(19):
        pl.put_oriented(f"U{50 + p}", slot_cx(3 + p), YB, "1", +1)   # TLP: pins 1-8 = LED (field, down)

    # ---- per-channel parts at the opto ---------------------------------------------------------
    for b in range(75):       # 74 = STOP_LATCH (0003 T4)
        p, u = divmod(b, 4)
        xc = slot_cx(3 + p) + (u - 1.5) * CH
        pl.put(f"R{201 + b}", xc, YB - 6.3, 90)
        pl.put(f"R{101 + b}", xc, YB + 6.3, 90)
        pl.put(f"D{101 + b}", xc, YB + 10.8, 90)

    # ---- node groups ----------------------------------------------------------------------------
    for g in range(7):
        x0 = X0 + PITCH * (3 + 2 * g)
        ids = list(range(8 * g, min(8 * g + 8, N_NODES)))
        refs1k = [f"R{n + 1}" for n in ids]
        if g == 6:
            refs1k += ["F3", "F4"]          # RUN / HOME output fuses (0003 T2)
        for i, ref in enumerate(refs1k):
            col, row = divmod(i, 4)
            pl.put(ref, x0 + PITCH + (col - 0.5) * 8.1, YB + 15.8 + 4.25 * row, 0)
        ydrv = YB + 37.0
        pl.put(f"U{20 + g}", x0 + 6.1, ydrv, 0)
        pl.put(f"U{10 + g}", x0 + 16.6, ydrv, 0)
        pds = [f"R{301 + n}" for n in range(8 * g, min(8 * g + 8, 54))]
        for i, ref in enumerate(pds):
            col, row = i % 4, i // 4
            pl.put(ref, x0 + 1.6 + 3.15 * col, ydrv + 6.9 + 1.9 * row, 0)
        pl.put(f"C{201 + g}", x0 + 15.3, ydrv + 7.9, 0)
        pl.put(f"C{211 + g}", x0 + 19.2, ydrv + 7.9, 0)

    # ---- aux slots: ESTOP / ILOCK source resistors ---------------------------------------------
    pl.put("R404", slot_cx(18), YB + 16, 0)
    pl.put("R405", slot_cx(19), YB + 16, 0)
    # ESTOP / ILOCK loop jumpers (0003 T3): return jumpers JP2 / JP3 (closed), optional feed jumpers JP4 / JP5 (open)
    pl.put("JP2", slot_cx(20), YB + 16, 0)
    pl.put("JP3", slot_cx(21), YB + 16, 0)
    pl.put("JP4", slot_cx(20), YB + 21, 0)
    pl.put("JP5", slot_cx(21), YB + 21, 0)

    # ---- isolator support parts --------------------------------------------------------------------
    pl.put("C231", slot_cx(0), YB - 9.5, 0)
    pl.put("C233", slot_cx(1), YB - 9.5, 0)
    pl.put("C232", slot_cx(0), YB + 9.5, 0)
    pl.put("C234", slot_cx(1), YB + 9.5, 0)
    pl.put("R401", slot_cx(0) + 3.5, YB + 12.5, 90)
    pl.put("R402", slot_cx(2) - 3.0, YB + 9.5, 90)
    pl.put("D180", slot_cx(2) + 3.0, YB + 9.5, 90)
    pl.put("R403", slot_cx(2) - 3.0, YB - 9.5, 90)
    pl.put("C235", slot_cx(2) + 3.0, YB - 9.5, 90)
    pl.put("R409", slot_cx(0) - 3.0, YB - 13.0, 90)
    pl.put("R410", slot_cx(1) - 3.0, YB - 13.0, 90)
    pl.put("R411", slot_cx(1) + 3.0, YB - 13.0, 90)
    pl.put("U5", slot_cx(2), YB - 14.5, 0)
    pl.put("C236", slot_cx(2) + 5.0, YB - 14.5, 0)

    # ---- MCU zone ---------------------------------------------------------------------------------------
    # Pico: USB towards the left edge (rotate 90 degrees: the USB end moves from top to left)
    pw, ph = pl.size("U1", 90)
    pl.put("U1", 0.5 + pw / 2, 3.0 + ph / 2, 90)
    for i in range(10):
        pl.put(f"U{30 + i}", 66.0 + 12.0 * i, 19.0, 90)
        pl.put(f"C{221 + i}", 66.0 + 12.0 * i, 25.2, 0)
    pl.put("SW1", 13.0, 31.5, 0)
    pl.put("C240", 20.0, 31.0, 0)
    pl.put("C241", 26.0, 31.0, 0)
    pl.put("C242", 32.0, 31.0, 0)
    pl.put("J5", W - 10.0, 20.0, 0)

    # ---- connectors J1 J2 J3 at the bottom edge --------------------------------------------------------
    for ref, cx in (("J1", 65.0), ("J2", 140.0), ("J3", 215.0)):
        fp = pl.fps[ref] if ref in pl.fps else pl.make(ref)
        l, t, r, b = pl.cyb[ref]
        fp.SetOrientationDegrees(0)
        pl.angles[ref] = 0
        ox = (l + r) / 2
        fp.SetPosition(V(cx - ox, H - 9.9))
        pl.boxes[ref] = (cx - ox + l, H - 9.9 + t, cx - ox + r, H - 9.9 + b)
    # ADC option header J6 (field taps) along the aux slots
    pl.put("J6", slot_cx(20), YB + 28.0, 90)

    # ---- power block (field side, left) ------------------------------------------------------------------
    sh = Shelf(pl, 4.0, YB + 17.0, 47.0, H - 14.5)
    for ref in ("J4", "C1", "U80", "F2", "F1", "D1", "Q1", "C2", "C3", "C4", "JP1", "DL1", "DL2", "R406", "R407",
                "DZ1", "R412", "R408", "C237"):
        sh.add(ref, 0)

    # ---- mounting holes ------------------------------------------------------------------------------------
    holes = {"H1": (5.0, 38.0), "H2": (W - 5.0, 5.0), "H3": (W - 5.0, 38.0), "H4": (W - 5.0, H - 5.0),
             "H5": (5.0, H - 5.0), "H6": (W / 2, 5.0)}
    for ref, (x, y) in holes.items():
        pl.put(ref, x, y, 0)

    # anything not yet placed?
    missing = [r for r in nl.comp if not r.startswith("#") and r not in pl.fps]
    if missing:
        raise SystemExit(f"unplaced: {missing}")
    for ref, fp in pl.fps.items():
        if ref not in pl.boxes:
            raise SystemExit(f"no box for {ref}")

    # ---- outline + slots -------------------------------------------------------------------------------------
    def seg(x1, y1, x2, y2):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1))
        s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)

    def rect(x1, y1, x2, y2):
        seg(x1, y1, x2, y1)
        seg(x2, y1, x2, y2)
        seg(x2, y2, x1, y2)
        seg(x1, y2, x1, y1)
    rect(0, 0, W, H)
    # milled isolation slots in the free ends of the barrier row (1 mm wide)
    lx1 = X0 - 3.0
    rx0 = X0 + PITCH * 22 + 3.0
    rect(3.0, YB - 0.5, lx1, YB + 0.5)
    rect(rx0, YB - 0.5, W - 3.0, YB + 0.5)

    # board text
    for txt, x, y, size in (("Scrambler unit tester  rev A  (generated by gen_pcb.py)", 100.0, H - 17.0, 1.2),
                            ("MCU / USB domain", 150.0, YB - 20.0, 1.5),
                            ("FIELD 24 V domain", 150.0, YB + 52.0, 1.5),
                            ("ISOLATION BARRIER - keep copper-free band, creepage >= 4.5 mm", 190.0, YB - 14.0, 1.0)):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(txt)
        t.SetPosition(V(x, y))
        t.SetLayer(pcbnew.F_SilkS)
        t.SetTextSize(V(size, size))
        t.SetTextThickness(mm(0.15))
        board.Add(t)

    # design settings
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(4)
    ds.m_MinClearance = mm(0.2)
    return board, nl, pl


# ----------------------------------------------------------------------------- project + rules
def write_project(nl, dom):
    pro_path = HERE / f"{PROJECT}.kicad_pro"
    pro = json.loads(pro_path.read_text(encoding="utf8")) if pro_path.exists() else {}

    def cls(name, clearance, width, prio):
        return {"bus_width": 12, "clearance": clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "name": name, "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": prio,
                "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": width, "via_diameter": 0.8,
                "via_drill": 0.4, "wire_width": 6}
    patterns = []
    for net, d in sorted(dom.items()):
        if d in ("FIELD", "MCU") and not net.startswith("unconnected"):
            patterns.append({"netclass": d, "pattern": net})
    pro["net_settings"] = {
        "classes": [cls("Default", 0.2, 0.25, 2147483647), cls("FIELD", 0.3, 0.3, 1), cls("MCU", 0.2, 0.25, 0)],
        "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None, "netclass_patterns": patterns}
    # connector bodies (D-sub, USB) overhang the board edge by design: their silkscreen outline crosses the edge
    pro.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rule_severities", {})["silk_edge_clearance"] = "ignore"
    pro.setdefault("meta", {"filename": f"{PROJECT}.kicad_pro", "version": 3})
    pro_path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")
    dru = (HERE / f"{PROJECT}.kicad_dru")
    dru.write_text(
        "(version 1)\n"
        "# Isolation barrier: any copper of the FIELD domain (24 V adapter) must stay this far from any copper of\n"
        "# the MCU domain (USB).  4.5 mm is the pad gap of the TLP291-4 footprint (4.9 mm) minus margin; the wide\n"
        "# ISO7340 (7.25 mm) and the DIP-8 6N137 (6.0 mm) are larger.\n"
        f"(rule \"domain_isolation\"\n  (constraint clearance (min {BARRIER_GAP_MM}mm))\n"
        "  (condition \"(A.NetClass == 'FIELD' && B.NetClass == 'MCU') || (A.NetClass == 'MCU' && B.NetClass == 'FIELD')\"))\n",
        encoding="utf8", newline="\n")


def signature(board) -> list:
    """Placement + pad nets of every footprint (the board file itself is not byte-stable: pcbnew assigns random item ids)."""
    sig = []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        pos = fp.GetPosition()
        sig.append((fp.GetReference(), fp.GetFPIDAsString(), pos.x, pos.y, round(fp.GetOrientationDegrees(), 3), int(fp.GetLayer()),
                    tuple(sorted((p.GetNumber(), p.GetNetname()) for p in fp.Pads()))))
    return sig


def check() -> int:
    """Checks of the placed board: footprints and pad nets == schematic netlist (the D-sub shell pads SH, which have no
    symbol pin, must be on CHASSIS_F); no copper; ratsnest == sum(pads - 1); placement deterministic (two builds give the
    same footprint positions and pad nets); kicad-cli DRC 0 violations (incl. the 4.5 mm FIELD/MCU rule) and schematic
    parity (only the SH pads may differ); negative tests."""
    nl, _ = load_netlist()
    out = HERE / f"{PROJECT}.kicad_pcb"
    cli = os.environ.get("KICAD_CLI", "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe")
    errs: list[str] = []

    def compare(board) -> list[str]:
        e = []
        have = {f.GetReference(): f for f in board.GetFootprints()}
        want = {r for r in nl.comp if not r.startswith("#")}
        if set(have) != want:
            e.append(f"footprints differ: missing {sorted(want - set(have))[:5]} extra {sorted(set(have) - want)[:5]}")
        for ref, fp in have.items():
            if ref not in nl.comp:
                continue
            if fp.GetFPIDAsString() != nl.comp[ref]["fp"]:
                e.append(f"{ref}: footprint {fp.GetFPIDAsString()} != {nl.comp[ref]['fp']}")
            for pad in fp.Pads():
                num = pad.GetNumber()
                exp = nl.net(ref, num)
                if num == "SH" and ref in ("J1", "J2", "J3"):
                    exp = "CHASSIS_F"
                if (exp or "") != pad.GetNetname():
                    e.append(f"{ref}.{num}: board net {pad.GetNetname()!r} != netlist {exp!r}")
        if len(list(board.GetTracks())) or len(list(board.Zones())):
            e.append("board has copper (placement only expected)")
        return e

    board = pcbnew.LoadBoard(str(out))
    e = compare(board)
    errs += e
    print(f"footprints {len(list(board.GetFootprints()))}, pad-net comparison with the schematic netlist:",
          "OK" if not e else f"{len(e)} problems {e[:4]}")
    dups = 0
    for fp in board.GetFootprints():
        seen = set()
        for pad in fp.Pads():
            if pad.GetNetname() and pad.GetNumber() in seen:
                dups += 1
            seen.add(pad.GetNumber())
    nets = collections.defaultdict(set)
    for ref in nl.comp:
        pass
    for (ref, pin), net in nl.pin_net.items():
        nets[net].add((ref, pin))
    nets["CHASSIS_F"] |= {(r, "SH") for r in ("J1", "J2", "J3")}
    expect = sum(len(v) - 1 for v in nets.values()) + dups
    got = board.GetConnectivity().GetUnconnectedCount(False)
    print(f"ratsnest: {got} edges, expected {expect} = sum(pads-1) over schematic nets (+ shell pads) + {dups} duplicate-numbered pads")
    if got != expect:
        errs.append("ratsnest edge count mismatch")
    b1, _, _ = build_board()
    b2, _, _ = build_board()
    same = signature(b1) == signature(b2) == signature(board)
    print("determinism (two builds and the saved board have identical footprint positions and pad nets):", "OK" if same else "DIFFERENT")
    if not same:
        errs.append("placement not deterministic / board file stale")
    r = subprocess.run([cli, "pcb", "drc", "--severity-all", "--schematic-parity", "--format", "json", "-o",
                        str(HERE / "_drc.json"), str(out)], capture_output=True, text=True)
    dr = json.loads((HERE / "_drc.json").read_text(encoding="utf8"))
    (HERE / "_drc.json").unlink()
    par = [x for x in dr.get("schematic_parity", []) if not (x["type"] == "net_conflict" and all(
        "pad SH " in i.get("description", "") for i in x.get("items", [])))]
    print(f"DRC: {len(dr['violations'])} violations; {len(dr['unconnected_items'])} unconnected items listed (capped at about 500); "
          f"schematic parity: {len(dr.get('schematic_parity', []))} items, {len(par)} real (SH shell pads have no symbol pin)")
    if dr["violations"]:
        errs.append("DRC violations")
        for v in dr["violations"][:6]:
            print("  ", v["type"], v["description"])
    if par:
        errs.append("schematic parity problems")
    # negative tests: two pad nets swapped; a shell pad off CHASSIS_F
    bad = pcbnew.LoadBoard(str(out))
    pads = [pd for pd in bad.FindFootprintByReference("U20").Pads()][:2]
    n0, n1 = pads[0].GetNet(), pads[1].GetNet()
    pads[0].SetNet(n1)
    pads[1].SetNet(n0)
    ok1 = bool(compare(bad))
    print("negative test (nets of two driver pads swapped):", "detected" if ok1 else "NOT DETECTED")
    bad = pcbnew.LoadBoard(str(out))
    for pd in bad.FindFootprintByReference("J2").Pads():
        if pd.GetNumber() == "SH":
            pd.SetNet(bad.FindNet("GND_F"))
    ok2 = bool(compare(bad))
    print("negative test (a D-sub shell pad on GND_F instead of CHASSIS_F):", "detected" if ok2 else "NOT DETECTED")
    if not (ok1 and ok2):
        errs.append("negative test failed")
    print("RESULT:", "PASS" if not errs else "FAIL")
    return 1 if errs else 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    board, nl, pl = build_board()
    rep, dom = chk.run(nl)
    if rep.fail:
        raise SystemExit("netlist check failed:\n" + "\n".join(rep.fail[:10]))
    out = HERE / f"{PROJECT}.kicad_pcb"
    pcbnew.SaveBoard(str(out), board)      # (may rewrite the .kicad_pro with defaults, so update it afterwards)
    write_project(nl, dom)
    # overlap self-check on courtyard boxes
    refs = sorted(r for r in pl.boxes if not r.startswith("H"))
    bad = []
    for i, a in enumerate(refs):
        ba = pl.boxes[a]
        for b in refs[i + 1:]:
            bb = pl.boxes[b]
            if ba[0] < bb[2] - 1e-3 and bb[0] < ba[2] - 1e-3 and ba[1] < bb[3] - 1e-3 and bb[1] < ba[3] - 1e-3:
                bad.append((a, b))
    print(f"wrote {out.name}: {len(pl.fps)} footprints, {len(pl.netinfo)} nets, board {W} x {H} mm")
    if bad:
        print("courtyard overlaps (own check):", bad[:40], len(bad))


if __name__ == "__main__":
    main()
