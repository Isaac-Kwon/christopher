#!/usr/bin/env python3
"""Check the placed PCBs of the gear column wiring (placement only, no routing).

    python verify_contact_pcb.py [--build-dir DIR]

For contact_board, column_terminal, reflector_B, reflector_C:
  1. parts and pad nets equal the schematic (netlist via kicad-cli, IPC-D-356 of the board); only the mounting holes
     H1..H4 are board-only;
  2. kicad-cli DRC: 0 violations; the unconnected items are exactly the ratsnest (pcbnew count = sum(pads-1));
  3. contact_board geometry, read from the board file: all 104 Omniball footprints on the rings r = 22/29/36/43 mm (+-0.005),
     position j at -j x 13.846 deg (counter-clockwise from the index mark at 12 o'clock, +-0.005 deg), letter = position,
     ring r contact in header J<r>'s net; centre hole Ø38, outline 110 x 110, headers on the back side in the four
     corners with pin 1 at the board-edge end, nothing outside the outline;
  4. negative tests: two header pad nets swapped; one contact moved by 1 mm; one contact on the wrong radius.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import gen_contact as C
import gen_contact_pcb as GP
import ki_common as K
import verify_column as VC

HERE = Path(__file__).resolve().parent
PROJECTS = ["contact_board", "column_terminal", "reflector_B", "reflector_C"]

GEOM = r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
out = {}
for fp in b.GetFootprints():
    pads = {p.GetNumber(): (pcbnew.ToMM(p.GetX()), pcbnew.ToMM(p.GetY())) for p in fp.Pads()}
    lay = pcbnew.F_CrtYd if fp.GetLayer() == pcbnew.F_Cu else pcbnew.B_CrtYd
    bb = fp.GetCourtyard(lay).BBox()
    out[fp.GetReference()] = dict(layer=fp.GetLayerName(), pads={k: v for k, v in pads.items() if k in ("1", "2", "25", "26")},
                                  box=[pcbnew.ToMM(v) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())])
e = b.GetBoardEdgesBoundingBox()
out["_edge"] = [pcbnew.ToMM(v) for v in (e.GetX(), e.GetY(), e.GetRight(), e.GetBottom())]
print(json.dumps(out))
'''


def pcb_geometry(pcb: Path, work: Path):
    py = K.kicad_python()
    if not py:
        return None
    s = work / "geom_contact.py"
    s.write_text(GEOM, encoding="utf8")
    r = subprocess.run([py, str(s), str(pcb)], capture_output=True, text=True)
    import json
    return json.loads(r.stdout.strip().splitlines()[-1])


def expected_ratsnest(pcb: Path) -> int:
    txt = pcb.read_text(encoding="utf8")
    cnt = Counter()
    for pm in re.finditer(r'\(pad "[^"]*"', txt):
        e = K.block_end(txt, pm.start())
        nm = re.search(r'\(net \d+ "([^"]*)"\)', txt[pm.start():e])
        if nm and not nm[1].startswith("unconnected"):
            cnt[nm[1]] += 1
    return sum(c - 1 for c in cnt.values() if c > 1)


def check_station_geometry(pcb: Path, geo: dict | None) -> list[str]:
    errs = []
    b = VC.parse_board(pcb)
    cx, cy = C.CENTER
    n = 0
    for ring in (1, 2, 3, 4):
        for j in range(26):
            ref = C.contact_ref(ring, j)
            d = b.get(ref)
            if not d:
                errs.append(f"{ref} missing")
                continue
            dx, dy = d["x"] - cx, d["y"] - cy
            r = math.hypot(dx, dy)
            ang = math.degrees(math.atan2(-dx, -dy)) % 360        # counter-clockwise from 12 o'clock (front view)
            want = (j * C.DELTA) % 360
            da = (ang - want + 180) % 360 - 180
            if abs(r - C.RING_R[ring]) > 0.005:
                errs.append(f"{ref}: radius {r:.4f} != {C.RING_R[ring]}")
            if abs(da) > 0.005:
                errs.append(f"{ref}: angle {ang:.4f} != {want:.4f} (counter-clockwise)")
            if d["layer"] != "F.Cu":
                errs.append(f"{ref}: not on the front side")
            if d["fp"] != f"{C.FP_LIB}:{C.FP_CONTACT}":
                errs.append(f"{ref}: footprint {d['fp']}")
            net = d["pads"].get("1")
            hdr = b[f"J{ring}"]["pads"].get(str(j + 1))
            if net != hdr or net != f"/R{ring}_{C.L(j)}":
                errs.append(f"{ref}: net {net} / header J{ring} pin {j + 1} net {hdr}")
            n += 1
    if n != 104:
        errs.append(f"{n} contacts checked")
    if geo:
        e = geo["_edge"]
        if abs((e[2] - e[0]) - 110.0) > 0.2 or abs((e[3] - e[1]) - 110.0) > 0.2:
            errs.append(f"outline {e[2] - e[0]:.2f} x {e[3] - e[1]:.2f}")
        for ref, d in geo.items():
            if ref.startswith("_"):
                continue
            bx = d["box"]
            if bx[0] < e[0] - 0.01 or bx[2] > e[2] + 0.01 or bx[1] < e[1] - 0.01 or bx[3] > e[3] + 0.01:
                errs.append(f"{ref}: courtyard outside the outline")
        for h, corner in C.CORNER_ORDER.items():
            d = geo[h]
            if d["layer"] != "B.Cu":
                errs.append(f"{h} not on the back side")
            n_, u = GP.CORNERS[corner]
            p1, p25 = d["pads"]["1"], d["pads"]["25"]
            vx, vy = p25[0] - p1[0], p25[1] - p1[1]
            ln = math.hypot(vx, vy)
            if abs(ln - 30.48) > 0.02 or abs(vx / ln - u[0]) > 0.001 or abs(vy / ln - u[1]) > 0.001:
                errs.append(f"{h}: pin 1 -> pin 25 direction {vx / ln:.3f},{vy / ln:.3f} != {u}")
            # pin 1 end faces the board edge on the ribbon side (left for ring 1/2, right for ring 3/4)
            side = -1 if corner in ("TL", "BL") else 1
            if (p1[0] - cx) * side <= (p25[0] - cx) * side:
                errs.append(f"{h}: pin 1 is not at the outer-x end")
        for ref in ("H1", "H2", "H3", "H4"):
            if ref not in geo:
                errs.append(f"{ref} missing")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("contact-board")
    work.mkdir(parents=True, exist_ok=True)
    allerr = []
    for proj in PROJECTS:
        pcb = HERE / f"{proj}.kicad_pcb"
        nl = K.Netlist.export(HERE / f"{proj}.kicad_sch", work)
        pads = K.pcb_pad_nets(pcb, work)
        holes = lambda r: re.fullmatch(r"H[1-4]", r) is not None and r not in nl.parts
        errs = K.compare_pcb(nl, pads, pcb, allowed_extra=holes)
        d = K.run_drc(pcb, work)
        errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
        want = expected_ratsnest(pcb)
        rn = K.ratsnest_count(pcb)
        if rn is not None and rn != want:
            errs.append(f"ratsnest {rn} != {want}")
        geo = pcb_geometry(pcb, work)
        if proj == "contact_board":
            errs += check_station_geometry(pcb, geo)
        elif geo:
            e = geo["_edge"]
            for ref, dd in geo.items():
                if not ref.startswith("_") and (dd["box"][0] < e[0] - 0.01 or dd["box"][2] > e[2] + 0.01):
                    errs.append(f"{ref}: courtyard outside the outline")
        print(f"{proj}: {len(nl.parts)} schematic parts, {len(nl.nets)} nets, pads read {len(pads)}; DRC {len(d['violations'])} violations, "
              f"{len(d['unconnected'])} unconnected (ratsnest {rn}, expected {want}); size "
              + (f"{geo['_edge'][2] - geo['_edge'][0]:.1f} x {geo['_edge'][3] - geo['_edge'][1]:.1f} mm" if geo else "?"))
        for e in errs[:6]:
            print("   ", e)
        allerr += errs

    # negative tests on copies of contact_board.kicad_pcb
    pcb = HERE / "contact_board.kicad_pcb"
    text = pcb.read_text(encoding="utf8")
    nl = K.Netlist.export(HERE / "contact_board.kicad_sch", work)
    holes = lambda r: re.fullmatch(r"H[1-4]", r) is not None
    neg = work / "contact_negative.kicad_pcb"
    neg.write_text(K.swap_two_pad_nets(text, "J4", "1", "2"), encoding="utf8", newline="\n")
    bad = K.compare_pcb(nl, K.pcb_pad_nets(neg, work), neg, allowed_extra=holes)
    print(f"negative test (two nets of header J4 swapped): {'detected' if bad else 'NOT DETECTED'}")
    if not bad:
        allerr.append("negative test 1 failed")
    m = re.search(r'(\(property "Reference" "P4C".*?)', text, re.S)
    i = text.index('(property "Reference" "P4C"')
    s = text.rfind('(footprint "', 0, i)
    mm = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', text[s:i + 400])
    x, y = float(mm[1]), float(mm[2])
    for title, nx, ny in (("moved by 1 mm", x + 1.0, y), ("moved radially by 0.5 mm", x + 0.5 * (x - 150) / 43, y + 0.5 * (y - 100) / 43)):
        t2 = text[:s] + text[s:i + 400].replace(mm[0], f"(at {nx:.4f} {ny:.4f})", 1) + text[i + 400:]
        neg.write_text(t2, encoding="utf8", newline="\n")
        errs = check_station_geometry(neg, None)
        print(f"negative test (contact P4C {title}): {'detected' if errs else 'NOT DETECTED'}")
        if not errs:
            allerr.append(f"negative test {title} failed")
    neg.unlink()
    print("RESULT:", "PASS" if not allerr else "FAIL")
    return 1 if allerr else 0


if __name__ == "__main__":
    sys.exit(main())
