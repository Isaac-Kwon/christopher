#!/usr/bin/env python3
"""Check the placed diagonal-board PCBs (d1..d5.kicad_pcb) against their schematics.

    python verify_diagonal_pcb.py [--build-dir DIR]

  1. schematic netlist (kicad-cli) vs board: every part present with the schematic footprint, nothing else,
     pad nets = schematic nets (IPC-D-356 export);
  2. kicad-cli DRC: 0 violations; unconnected items = ratsnest of the schematic (pcbnew count);
  3. geometry (pcbnew): jack grid 110 x 30 mm, the two jacks of a letter one row apart, junction headers on the
     back side with pin 1 at the top, same pin-1 height on both boards of a boundary (flat cable 1:1), nothing
     outside the outline;
  4. negative test: swapping the nets of two jack pads in a copy of a board is detected.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import gen_diagonal as G
import ki_common as K

HERE = Path(__file__).resolve().parent

GEOM = r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
out = {}
for fp in b.GetFootprints():
    r = fp.GetReference()
    pads = {p.GetNumber(): (pcbnew.ToMM(p.GetX()), pcbnew.ToMM(p.GetY())) for p in fp.Pads()}
    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox() if fp.GetLayer() == pcbnew.F_Cu else fp.GetCourtyard(pcbnew.B_CrtYd).BBox()
    out[r] = dict(layer=fp.GetLayerName(), pads={k: v for k, v in pads.items() if k in ("1", "2", "3", "63", "64", "SH")},
                  box=[pcbnew.ToMM(v) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())])
e = b.GetBoardEdgesBoundingBox()
out["_edge"] = [pcbnew.ToMM(v) for v in (e.GetX(), e.GetY(), e.GetRight(), e.GetBottom())]
print(json.dumps(out))
'''


def geometry(pcb: Path, work: Path):
    py = K.kicad_python()
    if not py:
        return None
    s = work / "geom.py"
    s.write_text(GEOM, encoding="utf8")
    r = subprocess.run([py, str(s), str(pcb)], capture_output=True, text=True)
    import json
    return json.loads(r.stdout.strip().splitlines()[-1])


def check_geometry(k: int, geo: dict) -> list[str]:
    errs = []
    name, cols = G.BOARDS[k]
    edge = geo["_edge"]
    if abs(edge[1]) > 0.2 or abs(edge[3] - 130.0) > 0.2:
        errs.append(f"{name}: board height {edge[3] - edge[1]:.2f} != 130")
    for ref, d in geo.items():
        if ref.startswith("_"):
            continue
        bx = d["box"]
        if bx[0] < edge[0] - 0.01 or bx[2] > edge[2] + 0.01 or bx[1] < edge[1] - 0.01 or bx[3] > edge[3] + 0.01:
            errs.append(f"{name}: {ref} courtyard {bx} sticks out of the outline {edge}")
    x1 = None
    for ci, (up, lo) in enumerate(cols):
        for r, c in enumerate((up, lo)):
            ys = []
            for n in (1, 2):
                p1 = geo[G.jack_ref(c, n)]["pads"]["1"]
                row = 2 * r + (n - 1)
                ys.append(p1[1])
                if abs(p1[1] - (18.0 + 30.0 * row - 1.42)) > 0.02:
                    errs.append(f"{name}: jack {c}{n} pin 1 y {p1[1]:.2f} not on row {row}")
                if ci > 0:
                    prev = geo[G.jack_ref(cols[ci - 1][r], n)]["pads"]["1"]
                    if abs((p1[0] - prev[0]) - 110.0) > 0.02:
                        errs.append(f"{name}: column pitch {p1[0] - prev[0]:.2f} != 110")
            if abs((ys[1] - ys[0]) - 30.0) > 0.02:
                errs.append(f"{name}: jacks of letter {c} not one row (30 mm) apart")
    for tag in ("XL", "XR"):
        for n in (1, 2, 3):
            ref = f"{tag}{n}"
            if ref in geo:
                d = geo[ref]
                if d["layer"] != "B.Cu":
                    errs.append(f"{name}: {ref} not on the back side")
                p1, p3, p63 = d["pads"]["1"], d["pads"]["3"], d["pads"]["63"]
                if not (p1[1] < p3[1] < p63[1]) or abs(p63[1] - p1[1] - 78.74) > 0.02:
                    errs.append(f"{name}: {ref} pin 1 is not at the top")
                if d["pads"]["2"][0] > p1[0]:
                    errs.append(f"{name}: {ref} odd pins not on the right column (orientation differs from the others)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("diagonal-board")
    work.mkdir(parents=True, exist_ok=True)
    all_errs = []
    geos = []
    for k, (name, cols) in enumerate(G.BOARDS):
        pcb = HERE / f"{name}.kicad_pcb"
        nl = K.Netlist.export(HERE / f"{name}.kicad_sch", work)
        pads = K.pcb_pad_nets(pcb, work)
        errs = K.compare_pcb(nl, pads, pcb)
        d = K.run_drc(pcb, work)
        errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
        expect = sum(len(n) - 1 for n in nl.nets.values()) + sum(1 for r in nl.parts if r.startswith('J') and len(r) == 3 and r[1].isalpha())  # 2nd shield pad of each D-sub
        rn = K.ratsnest_count(pcb)
        if rn is not None and rn != expect:
            errs.append(f"ratsnest {rn} != {expect}")
        geo = geometry(pcb, work)
        geos.append(geo)
        if geo:
            errs += check_geometry(k, geo)
        edge = geo["_edge"] if geo else None
        print(f"{name}: {len(nl.parts)} parts, {len(nl.nets)} nets, pads read {len(pads)}; DRC {len(d['violations'])} violations, "
              f"{len(d['unconnected'])} unconnected listed, ratsnest {rn} (expected {expect}); "
              f"size {edge[2] - edge[0]:.1f} x {edge[3] - edge[1]:.1f} mm" if edge else f"{name}: geometry not checked")
        all_errs += errs
        # negative test: swap two jack pad nets
        first = G.jack_ref(G.board_letters(k)[0], 1)
        bad_text = K.swap_two_pad_nets(pcb.read_text(encoding="utf8"), first, "1", "2")
        neg = work / f"{name}_negative.kicad_pcb"
        neg.write_text(bad_text, encoding="utf8", newline="\n")
        nerr = K.compare_pcb(nl, K.pcb_pad_nets(neg, work), neg)
        print(f"  negative test (two pad nets of {first} swapped): {'detected' if nerr else 'NOT DETECTED'}")
        if not nerr:
            all_errs.append(f"{name}: negative test failed")
        neg.unlink()
    # ribbon alignment between boards
    for b in range(4):
        if geos[b] and geos[b + 1]:
            for n in (1, 2, 3):
                ya = geos[b][f"XR{n}"]["pads"]["1"][1]
                yb = geos[b + 1][f"XL{n}"]["pads"]["1"][1]
                if abs(ya - yb) > 0.02:
                    all_errs.append(f"ribbon D{b + 1}-D{b + 2} #{n}: pin 1 heights differ ({ya:.2f} vs {yb:.2f})")
    print("RESULT:", "PASS" if not all_errs else "FAIL")
    for e in all_errs[:20]:
        print("  ", e)
    return 1 if all_errs else 0


if __name__ == "__main__":
    sys.exit(main())
