#!/usr/bin/env python3
"""Create sequencer.kicad_pcb: outline, mounting holes, footprints placed by function, netlist assigned.  PLACEMENT ONLY
(no tracks, no vias, no zones: routing is left to the user).

Run with the Python that ships with KiCad (needs the `pcbnew` module):

    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_sequencer_pcb.py            # write sequencer.kicad_pcb + .kicad_pro rules
    "C:/Program Files/KiCad/10.0/bin/python.exe" gen_sequencer_pcb.py --check    # verify the placed board (see check())

Floor plan (mm, origin top-left, y down; board 250 x 228 = the counter / shifter card size, 4 layers, 40 mm height budget):
    top band      power input (J1, fuse, P-MOSFET, TVS, 5 V), time base (LMC555, 74HC4017, ring lamps, windows), 2 x TBD62783A
    middle        50 G6S-2 relays in three rows of 17 (pitch 12 mm), each with its coil clamp diode / zener / series diode / lamp below it
    lower band    diode-OR feeds, output fuses, solder jumpers
    right side    terminals J7 (safety loops), J8 (idle timer), J9 (thermostat), J10 (dial-strip controls)
    bottom edge   D-sub: J2 control (37), J5 POS in (25), J6 UNIT_BUS tap (25), J3 / J4 CLOCK buses A / B (9)
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
PROJECT = "sequencer"
W, H = 250.0, 228.0


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def scratch() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "christopher" / "sequencer"
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
            if pad.GetNumber() == "SH" and ref in DSUBS:
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
DSUBS = ("J2", "J3", "J4", "J5", "J6")


def assoc_parts(nl):
    """small parts that belong to a relay: those that touch its coil net (clamp, series diode, lamp)"""
    coil = {}
    for ref, c in nl.comp.items():
        if c["value"].startswith("G6S-2 "):
            coil[ref] = nl.net(ref, "1")
    by_net = collections.defaultdict(list)
    for ref in nl.comp:
        if re.fullmatch(r"(D|DZ|DL|R)\d+", ref):
            for pin in ("1", "2"):
                n = nl.net(ref, pin)
                if n:
                    by_net[n].append(ref)
    out, used, seen_coil = {}, set(), set()
    for rref, cn in sorted(coil.items(), key=lambda kv: int(kv[0][1:])):
        if cn in seen_coil:
            out[rref] = []
            continue                                   # twin relays share the parts of the first one
        seen_coil.add(cn)
        mine = []
        for n in (cn, f"X_{cn}"):
            for p in by_net.get(n, []):
                if p in used:
                    continue
                if p.startswith("R") and nl.net(p, "1") != cn:
                    continue
                mine.append(p)
                used.add(p)
        for p in list(mine):
            if p.startswith("R"):
                for q in by_net.get(nl.net(p, "2"), []):
                    if q.startswith("DL") and q not in used:
                        mine.append(q)
                        used.add(q)
        out[rref] = mine
    return out


def build_board():
    import seqdesign
    sheet_of = {p.ref: p.sheet for p in seqdesign.build().parts}
    nl = load_netlist()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    pl = Placer(board, nl)
    assoc = assoc_parts(nl)
    placed: set[str] = set()

    def put(ref, cx, cy, angle=0):
        pl.put(ref, cx, cy, angle)
        placed.add(ref)

    def key(s):
        return (re.sub(r"\d+", "", s), int(re.sub(r"\D", "", s) or 0))

    # ---- relays: two rows, each with its clamp diodes / lamp parts below ------------------------------------
    relays = sorted((r for r in nl.comp if nl.comp[r]["value"].startswith("G6S-2 ")), key=lambda s: int(s[1:]))
    x0 = 6.0
    pitch = 12.0
    per_row = 17
    ys = [54.0, 98.0, 142.0]
    for i, r in enumerate(relays):
        row, col = divmod(i, per_row)
        cx = x0 + pitch * col + 4.0
        put(r, cx, ys[row] + 7.7)
        yy = ys[row] + 16.8
        for p in assoc.get(r, []):
            w, h = pl.size(p, 0)
            put(p, cx, yy + h / 2)
            yy += h + 0.6
    # ---- D-subs: J2 / J5 / J6 on the bottom edge (pin row 9.9 mm above the edge), J3 / J4 (9 pin) on the right edge ----
    cx = 16.0
    for ref in ("J2", "J5", "J6"):
        fp = pl.make(ref)
        l, t, r_, b = pl.cyb[ref]
        fp.SetOrientationDegrees(0)
        pl.angles[ref] = 0
        fp.SetPosition(V(cx - l, H - 9.9))
        pl.boxes[ref] = (cx, H - 9.9 + t, cx + (r_ - l), H - 9.9 + b)
        placed.add(ref)
        cx += (r_ - l) + 6.0
    for ref, yc in (("J3", 40.0), ("J4", 80.0)):
        fp = pl.make(ref)
        l, t, r_, b = pl.cyb[ref]
        fp.SetOrientationDegrees(90)
        pl.angles[ref] = 90
        pts = [rot_pt(x, y, 90) for x in (l, r_) for y in (t, b)]
        miny, maxy = min(p_[1] for p_ in pts), max(p_[1] for p_ in pts)
        minx, maxx = min(p_[0] for p_ in pts), max(p_[0] for p_ in pts)
        ox, oy = W - 9.9, yc - (miny + maxy) / 2
        fp.SetPosition(V(ox, oy))
        pl.boxes[ref] = (ox + minx, oy + miny, ox + maxx, oy + maxy)
        placed.add(ref)
    # ---- top band: power / time base / drivers ------------------------------------------------------------------------
    regions = {"power": Shelf(pl, 14.0, 4.0, 98.0, 48.0), "timebase": Shelf(pl, 102.0, 4.0, 170.0, 48.0),
               "drivers": Shelf(pl, 174.0, 4.0, 222.0, 48.0)}
    first = {"power": ["J1", "F1", "Q1", "U80", "D1"], "drivers": ["U3", "U4"], "timebase": ["U1", "U2", "RV1"]}
    for rk, sh in regions.items():
        for ref in first[rk]:
            if ref in nl.comp and ref not in placed:
                sh.add(ref, 0)
                placed.add(ref)
    for ref in sorted(nl.comp, key=key):
        if ref in placed or ref in DSUBS:
            continue
        if sheet_of.get(ref) in regions:
            regions[sheet_of[ref]].add(ref, 0)
            placed.add(ref)
    # ---- the rest (diode-OR networks, fuses, jumpers, terminals) in the lower band --------------------------------------
    big = Shelf(pl, 4.0, 182.0, 226.0, 200.0)
    term = Shelf(pl, 210.0, 100.0, W - 4.0, 170.0)
    for ref in sorted(nl.comp, key=key):
        if ref in placed or ref in DSUBS or ref.startswith("H"):
            continue
        (term if ref in ("J7", "J8", "J9", "J10") else big).add(ref, 0)
        placed.add(ref)
    # ---- mounting holes ----------------------------------------------------------------------------------------------------
    for ref, (x, y) in {"H1": (5.0, 5.0), "H2": (W - 5.0, 5.0), "H3": (5.0, H - 5.0), "H4": (W - 5.0, H - 5.0)}.items():
        put(ref, x, y)
    missing = [r_ for r_ in nl.comp if not r_.startswith("#") and r_ not in pl.fps]
    if missing:
        raise SystemExit(f"unplaced: {missing}")

    def seg(x1_, y1_, x2_, y2_):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1_, y1_))
        s.SetEnd(V(x2_, y2_))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)
    for a_, b_, c_, d_ in ((0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0)):
        seg(a_, b_, c_, d_)
    for txt, x, y, size in (("Relay machine clock sequencer  rev A  (placement only, generated by gen_sequencer_pcb.py)", 40.0, 51.0, 1.2),):
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
    pats = [{"netclass": "Power", "pattern": n} for n in ("SW", "VIN", "VF", "GND", "V5", "KENO_A", "KENO_B", "KENA_P", "KENB_P", "CK_A", "NCK_A", "CK_B", "NCK_B", "CK_C", "EN_C")]
    pats += [{"netclass": "Output24", "pattern": n} for n in ("STROBE", "JUDGE", "CLEAR", "STOPPED", "AT_HOME", "END", "POS*", "LOAD_*", "*_O", "*_OUT")]
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
                if num == "SH" and ref in DSUBS:
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
    nets["CHASSIS"] |= {(r_, "SH") for r_ in DSUBS}
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
    pads = [pd for pd in bad.FindFootprintByReference("U3").Pads()][:2]
    n0, n1 = pads[0].GetNet(), pads[1].GetNet()
    pads[0].SetNet(n1)
    pads[1].SetNet(n0)
    ok1 = bool(compare(bad))
    print("negative test (nets of two driver pads swapped):", "detected" if ok1 else "NOT DETECTED")
    bad = pcbnew.LoadBoard(str(out))
    for pd in bad.FindFootprintByReference("J2").Pads():
        if pd.GetNumber() == "SH":
            pd.SetNet(bad.FindNet("GND"))
    ok2 = bool(compare(bad))
    print("negative test (the D-sub shell pad on 0 V instead of CHASSIS):", "detected" if ok2 else "NOT DETECTED")
    bad = pcbnew.LoadBoard(str(out))
    bad.FindFootprintByReference("U2").Flip(bad.FindFootprintByReference("U2").GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
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
