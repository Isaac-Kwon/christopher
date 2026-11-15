#!/usr/bin/env python3
"""Create control_board.kicad_pcb: outline, mounting holes, footprints placed by function, netlist assigned.  PLACEMENT ONLY
(no tracks, no vias, no zones: routing is left to the user).

Run with the Python that ships with KiCad (needs the `pcbnew` module):

    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_control_pcb.py            # write control_board.kicad_pcb + .kicad_pro rules
    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_control_pcb.py --check    # verify the placed board (see check())

Floor plan (mm, origin top-left, y down; board 240 x 170, 4 layers):
    top edge      9 photointerrupter connectors (JST-XH) with their RC parts, Pico module (USB towards the top)
    left          power input block (two terminals, PTC, P-MOSFET, TVS, TSR 1-2450)
    middle        24 V input optocouplers, the 74HC logic (gating, flip-flops, RC), Pico support parts
    lower middle  3 x 74HC595 + 4 x TBD62783A, driver pull-downs, 21 output fuses + pull-downs
    bottom edge   J_CTRL (D-sub 37 pins), J_SAFE, J_DRV, J_AUX terminals, test header
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
KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
KICAD_CLI = Path(os.environ.get("KICAD_CLI", "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"))
PROJECT = "control_board"
W, H = 240.0, 170.0


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def scratch() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "christopher" / "control-board"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ----------------------------------------------------------------------------- netlist
class NL:
    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        self.comp, self.paths = {}, {}
        for c in root.find("components"):
            ref = c.get("ref")
            self.comp[ref] = dict(value=c.findtext("value") or "", fp=c.findtext("footprint") or "",
                                  desc=c.findtext("description") or "", ds=c.findtext("datasheet") or "",
                                  dnp=any(p.get("name") == "dnp" for p in c.findall("property")))
            sp = c.find("sheetpath").get("tstamps") if c.find("sheetpath") is not None else "/"
            ts = (c.findtext("tstamps") or "").split()
            self.paths[ref] = (sp + (ts[0] if ts else "")).replace("//", "/")
        self.pin_net: dict[tuple[str, str], str] = {}
        for n in root.find("nets"):
            name = n.get("name")
            for nd in n:
                self.pin_net[(nd.get("ref"), nd.get("pin"))] = name

    def net(self, ref, pin):
        return self.pin_net.get((ref, str(pin)))


def load_netlist() -> NL:
    p = scratch() / "pcb_nl.xml"
    r = subprocess.run([str(KICAD_CLI), "sch", "export", "netlist", "--format", "kicadxml", "-o", str(p),
                        str(HERE / f"{PROJECT}.kicad_sch")], capture_output=True, text=True)
    if r.returncode != 0 or not p.exists():
        raise SystemExit("kicad-cli netlist export failed: " + r.stdout + r.stderr)
    return NL(p)


# ----------------------------------------------------------------------------- placement helpers
def load_fp(fpid: str):
    lib, name = fpid.split(":")
    fp = pcbnew.FootprintLoad(str(FPDIR / f"{lib}.pretty"), name)
    if fp is None:
        raise SystemExit(f"footprint {fpid} not found")
    return fp


def cy_bbox(fp):
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    bb = fp.GetBoundingBox(False) if cy.IsEmpty() else cy.BBox()
    return (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))


def rot_pt(x, y, a):
    r = math.radians(a)
    return (x * math.cos(r) + y * math.sin(r), -x * math.sin(r) + y * math.cos(r))


class Placer:
    def __init__(self, board, nl):
        self.board, self.nl = board, nl
        self.fps, self.angles, self.cyb, self.boxes, self.netinfo = {}, {}, {}, {}, {}

    def netitem(self, name):
        if name not in self.netinfo:
            it = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(it)
            self.netinfo[name] = it
        return self.netinfo[name]

    def make(self, ref):
        c = self.nl.comp[ref]
        fp = load_fp(c["fp"])
        self.cyb[ref] = cy_bbox(fp)
        fp.SetFPIDAsString(c["fp"])
        for fname, val in (("Description", c["desc"]), ("Datasheet", c["ds"])):
            fld = fp.GetField(fname)
            if fld is not None:
                fld.SetText(val)
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        fp.SetPath(pcbnew.KIID_PATH(self.nl.paths[ref]))
        for pad in fp.Pads():
            net = self.nl.net(ref, pad.GetNumber())
            if pad.GetNumber() == "SH" and ref == "J3":
                net = "CHASSIS"
            if net:
                pad.SetNet(self.netitem(net))
        fp.Reference().SetTextSize(V(0.8, 0.8))
        fp.Reference().SetTextThickness(mm(0.12))
        fp.Value().SetVisible(False)
        fp.Reference().SetLayer(pcbnew.F_Fab)          # dense board: designators on the fab layer
        if c["dnp"]:
            fp.SetDNP(True)
        self.board.Add(fp)
        self.fps[ref] = fp
        return fp

    def put(self, ref, cx, cy, angle=0):
        fp = self.fps[ref] if ref in self.fps else self.make(ref)
        l, t, r, b = self.cyb[ref]
        ox, oy = rot_pt((l + r) / 2, (t + b) / 2, angle)
        fp.SetOrientationDegrees(angle)
        fp.SetPosition(V(cx - ox, cy - oy))
        self.angles[ref] = angle
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

    def put_box(self, ref, x0, y0, angle=0):
        """place so that the courtyard box starts at (x0, y0); returns (w, h)"""
        w, h = self.size(ref, angle)
        self.put(ref, x0 + w / 2, y0 + h / 2, angle)
        return w, h


class Shelf:
    def __init__(self, pl, x0, y0, x1, y1, gap=0.5):
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


def refs_like(nl, pattern):
    return sorted((r for r in nl.comp if re.fullmatch(pattern, r)), key=lambda s: (re.sub(r"\d+", "", s), int(re.sub(r"\D", "", s) or 0)))


# ----------------------------------------------------------------------------- the floor plan
def build_board():
    nl = load_netlist()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    pl = Placer(board, nl)

    # ---- top edge: nine sensor connectors + their RC parts ----------------------------------------
    for i in range(9):
        pl.put_box(f"J{10 + i}", 12.0 + 11.6 * i, 3.0)
    sh = Shelf(pl, 12.0, 13.0, 116.0, 36.0)
    for i in range(9):
        for ref in (f"R{40 + i}", f"R{50 + i}", f"C{20 + i}"):
            sh.add(ref, 0)

    # ---- Pico module (USB to the top edge), reset button, supply parts --------------------------------
    pw, ph = pl.size("U1", 90)
    pl.put("U1", 236.0 - pw / 2, 3.0 + ph / 2, 90)
    pl.put_box("SW1", 134.0, 4.0)
    sh = Shelf(pl, 134.0, 14.0, 176.0, 36.0)
    for ref in ("C10", "C11", "R10", "R11", "R12"):
        sh.add(ref, 0)

    # ---- power block (left) --------------------------------------------------------------------------------
    sh = Shelf(pl, 4.0, 100.0, 62.0, 165.0)
    for ref in ("J1", "J2", "F1", "F2", "F3", "Q1", "Q2", "D1", "D2", "C1", "C2", "U80", "C3", "C4", "C5", "C6", "D4", "DZ1", "DZ2", "RG1",
                "RG2", "R1", "R2", "R3", "DL1", "DL2", "DL3", "RCH1", "CCH1"):
        sh.add(ref, 0)

    # ---- inputs: optocouplers and their parts -----------------------------------------------------------
    for k in range(2):
        pl.put_box(f"U{40 + k}", 4.0 + 14.0 * k, 40.0, 90)
    sh = Shelf(pl, 34.0, 40.0, 106.0, 62.0)
    for ref in refs_like(nl, r"R2[0-8]") + refs_like(nl, r"D2[0-7]") + refs_like(nl, r"R3[0-7]"):
        sh.add(ref, 0)

    # ---- logic ---------------------------------------------------------------------------------------------------
    x = 108.0
    for k, ref in enumerate(["U30", "U31", "U32", "U33", "U34", "U35", "U36", "U37"]):
        w, h = pl.put_box(ref, x, 44.0, 0)
        x += w + 1.0
    sh = Shelf(pl, 108.0, 62.0, 232.0, 80.0)
    for ref in ["C40", "C41", "C42", "C43", "C44", "C45", "C46", "C47", "C50", "C51", "R60", "R61"]:
        sh.add(ref, 0)

    # ---- outputs -----------------------------------------------------------------------------------------------
    x = 70.0
    for c in range(3):
        w, h = pl.put_box(f"U{50 + c}", x, 86.0, 0)
        pl.put_box(f"C{60 + c}", x, 86.0 + h + 0.8, 0)
        x += w + 1.5
    x = 70.0
    for d in range(4):
        w, h = pl.put_box(f"U{60 + d}", x, 112.0, 0)
        pl.put_box(f"C{70 + d}", x, 112.0 + h + 0.8, 0)
        x += max(w, 12.0) + 1.5
    sh = Shelf(pl, 125.0, 86.0, 236.0, 112.0)
    for ref in refs_like(nl, r"F(2\d|3\d|40)"):
        sh.add(ref, 0)
    sh = Shelf(pl, 125.0, 110.0, 236.0, 128.0)
    for ref in refs_like(nl, r"R(11\d|12\d|130)") + ["R150", "R151", "R152"]:
        sh.add(ref, 0)
    sh = Shelf(pl, 125.0, 126.0, 198.0, 148.0)
    for ref in refs_like(nl, r"R(7\d|8\d|9[0-5])"):
        sh.add(ref, 0)

    # ---- connectors on the bottom edge ----------------------------------------------------------------------
    fp = pl.make("J3")
    l, t, r, b = pl.cyb["J3"]
    cx = 150.0
    fp.SetOrientationDegrees(0)
    pl.angles["J3"] = 0
    ox = (l + r) / 2
    fp.SetPosition(V(cx - ox, H - 9.9))
    pl.boxes["J3"] = (cx - ox + l, H - 9.9 + t, cx - ox + r, H - 9.9 + b)
    pl.put_box("J4", 196.0, H - 16.0, 0)
    pl.put_box("J5", 196.0, H - 34.0, 0)
    pl.put_box("J6", 196.0, H - 52.0, 0)
    pl.put_box("J7", 226.0, H - 70.0, 90)
    sh = Shelf(pl, 125.0, 143.0, 140.0, 150.0)
    for ref in ("SJ1", "SJ2", "SJ3", "SJ4", "SJ5"):
        sh.add(ref, 0)

    # ---- mounting holes -----------------------------------------------------------------------------------------
    for ref, (x, y) in {"H1": (4.0, 4.0), "H2": (W - 4.0, 30.0), "H3": (4.0, H - 4.0), "H4": (W - 4.0, H - 4.0)}.items():
        pl.put(ref, x, y, 0)

    missing = [r_ for r_ in nl.comp if not r_.startswith("#") and r_ not in pl.fps]
    if missing:
        raise SystemExit(f"unplaced: {missing}")

    def seg(x1, y1, x2, y2):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1))
        s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)
    for a, b_, c_, d in ((0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0)):
        seg(a, b_, c_, d)
    for txt, x, y, size in (("Gear machine control board  rev A  (placement only, generated by gen_control_pcb.py)", 70.0, H - 3.0, 1.2),):
        t_ = pcbnew.PCB_TEXT(board)
        t_.SetText(txt)
        t_.SetPosition(V(x, y))
        t_.SetLayer(pcbnew.F_SilkS)
        t_.SetTextSize(V(size, size))
        t_.SetTextThickness(mm(0.15))
        board.Add(t_)
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(4)
    ds.m_MinClearance = mm(0.2)
    return board, nl, pl


def write_project():
    pro_path = HERE / f"{PROJECT}.kicad_pro"
    pro = json.loads(pro_path.read_text(encoding="utf8")) if pro_path.exists() else {}

    def cls(name, clearance, width, prio):
        return {"bus_width": 12, "clearance": clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "name": name, "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": prio,
                "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": width, "via_diameter": 0.8,
                "via_drill": 0.4, "wire_width": 6}
    pats = [{"netclass": "Power", "pattern": n} for n in ("+24V_L", "+24V_SW", "+24V_S", "VIN_L", "VIN_SW", "VF1", "VF2", "GND", "+5V", "VSYS", "+3V3")]
    pats += [{"netclass": "Output24", "pattern": n} for n in ("DO_*", "STROBE", "JUDGE", "CLEAR", "STOPPED", "AT_HOME", "END", "POS*", "DRV_*")]
    pro["net_settings"] = {"classes": [cls("Default", 0.2, 0.25, 2147483647), cls("Power", 0.3, 0.6, 1), cls("Output24", 0.3, 0.3, 2)],
                           "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None, "netclass_patterns": pats}
    pro.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rule_severities", {})["silk_edge_clearance"] = "ignore"
    pro.setdefault("meta", {"filename": f"{PROJECT}.kicad_pro", "version": 3})
    pro_path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")


def signature(board):
    sig = []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        pos = fp.GetPosition()
        sig.append((fp.GetReference(), fp.GetFPIDAsString(), pos.x, pos.y, round(fp.GetOrientationDegrees(), 3), int(fp.GetLayer()),
                    tuple(sorted((p.GetNumber(), p.GetNetname()) for p in fp.Pads()))))
    return sig


def check() -> int:
    nl = load_netlist()
    out = HERE / f"{PROJECT}.kicad_pcb"
    errs: list[str] = []

    def compare(board):
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
            if int(fp.GetLayer()) != int(pcbnew.F_Cu):
                e.append(f"{ref}: not on the front layer")
            for pad in fp.Pads():
                num = pad.GetNumber()
                exp = nl.net(ref, num)
                if num == "SH" and ref == "J3":
                    exp = "CHASSIS"
                if (exp or "") != pad.GetNetname():
                    e.append(f"{ref}.{num}: board net {pad.GetNetname()!r} != netlist {exp!r}")
        if len(list(board.GetTracks())) or len(list(board.Zones())):
            e.append("board has copper (placement only expected)")
        return e

    board = pcbnew.LoadBoard(str(out))
    e = compare(board)
    errs += e
    nfp = len(list(board.GetFootprints()))
    npad = sum(len(list(f.Pads())) for f in board.GetFootprints())
    print(f"footprints {nfp}, pads {npad}, pad-net comparison with the schematic netlist:", "OK" if not e else f"{len(e)} problems {e[:4]}")
    dups = 0
    for fp in board.GetFootprints():
        seen = set()
        for pad in fp.Pads():
            if pad.GetNetname() and pad.GetNumber() in seen:
                dups += 1
            seen.add(pad.GetNumber())
    nets = collections.defaultdict(set)
    for (ref, pin), net in nl.pin_net.items():
        nets[net].add((ref, pin))
    nets["CHASSIS"] |= {("J3", "SH")}
    expect = sum(len(v) - 1 for v in nets.values()) + dups
    got = board.GetConnectivity().GetUnconnectedCount(False)
    print(f"ratsnest: {got} edges, expected {expect} = sum(pads-1) over schematic nets + {dups} duplicate-numbered pads")
    if got != expect:
        errs.append("ratsnest edge count mismatch")
    b1, _, _ = build_board()
    b2, _, _ = build_board()
    same = signature(b1) == signature(b2) == signature(board)
    print("determinism (two builds and the saved board have identical footprint positions and pad nets):", "OK" if same else "DIFFERENT")
    if not same:
        errs.append("placement not deterministic / board file stale")
    dj = scratch() / "_drc.json"
    subprocess.run([str(KICAD_CLI), "pcb", "drc", "--severity-all", "--schematic-parity", "--format", "json", "-o", str(dj), str(out)],
                   capture_output=True, text=True)
    dr = json.loads(dj.read_text(encoding="utf8"))
    par = [x for x in dr.get("schematic_parity", []) if not (x["type"] == "net_conflict" and all(
        "pad SH " in i.get("description", "") for i in x.get("items", [])))]
    print(f"DRC: {len(dr['violations'])} violations; {len(dr['unconnected_items'])} unconnected items listed (capped at about 500); "
          f"schematic parity: {len(dr.get('schematic_parity', []))} items, {len(par)} real (the SH shell pads have no symbol pin)")
    if dr["violations"]:
        errs.append("DRC violations")
        for v_ in dr["violations"][:8]:
            print("  ", v_["type"], v_["description"])
    if par:
        errs.append("schematic parity problems")
        for v_ in par[:5]:
            print("  ", v_["type"], v_["description"])
    bad = pcbnew.LoadBoard(str(out))
    pads = [pd for pd in bad.FindFootprintByReference("U60").Pads()][:2]
    n0, n1 = pads[0].GetNet(), pads[1].GetNet()
    pads[0].SetNet(n1)
    pads[1].SetNet(n0)
    ok1 = bool(compare(bad))
    print("negative test (nets of two driver pads swapped):", "detected" if ok1 else "NOT DETECTED")
    bad = pcbnew.LoadBoard(str(out))
    for pd in bad.FindFootprintByReference("J3").Pads():
        if pd.GetNumber() == "SH":
            pd.SetNet(bad.FindNet("GND"))
    ok2 = bool(compare(bad))
    print("negative test (the D-sub shell pad on 0 V instead of CHASSIS):", "detected" if ok2 else "NOT DETECTED")
    bad = pcbnew.LoadBoard(str(out))
    bad.FindFootprintByReference("U33").Flip(bad.FindFootprintByReference("U33").GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    ok3 = bool(compare(bad))
    print("negative test (a footprint flipped to the back):", "detected" if ok3 else "NOT DETECTED")
    if not (ok1 and ok2 and ok3):
        errs.append("negative test failed")
    print("RESULT:", "PASS" if not errs else "FAIL")
    return 1 if errs else 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    board, nl, pl = build_board()
    out = HERE / f"{PROJECT}.kicad_pcb"
    pcbnew.SaveBoard(str(out), board)
    write_project()
    refs = sorted(r for r in pl.boxes if not r.startswith("H"))
    bad = []
    for i, a in enumerate(refs):
        ba = pl.boxes[a]
        for b in refs[i + 1:]:
            bb = pl.boxes[b]
            if ba[0] < bb[2] - 1e-3 and bb[0] < ba[2] - 1e-3 and ba[1] < bb[3] - 1e-3 and bb[1] < ba[3] - 1e-3:
                bad.append((a, b))
    offb = [r for r in refs if pl.boxes[r][0] < 0 or pl.boxes[r][1] < 0 or pl.boxes[r][2] > W + 0.5 or pl.boxes[r][3] > H + 0.5]
    print(f"wrote {out.name}: {len(pl.fps)} footprints, {len(pl.netinfo)} nets, board {W:g} x {H:g} mm")
    if bad:
        print("courtyard overlaps (own check):", bad[:40], len(bad))
    if offb:
        print("outside the board:", offb)


if __name__ == "__main__":
    main()
