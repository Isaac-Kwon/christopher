#!/usr/bin/env python3
"""Check the placed patch panel PCB (patch_panel.kicad_pcb) against its schematic.

    python verify_patch_pcb.py [--build-dir DIR]

  1. parts and pad nets = schematic (netlist via kicad-cli, IPC-D-356); the flying plug PL1 is not on the board;
  2. kicad-cli DRC: 0 violations; unconnected items = ratsnest (pcbnew count);
  3. geometry (pcbnew): jacks on the 85 x 30 mm grid (rows 1-4 commons 1-4, row 5 JR1/JR2/JS1), hole field TS1 with
     pin 1 on the left, pins increasing to the right (odd pins on the top row) so that hole k = letter k - 1,
     courtyards inside the outline;
  4. negative test: two pad nets of TS1 swapped are detected.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import gen_patch_pcb as GP
import ki_common as K

HERE = Path(__file__).resolve().parent

GEOM = r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
out = {}
for fp in b.GetFootprints():
    pads = {p.GetNumber(): (pcbnew.ToMM(p.GetX()), pcbnew.ToMM(p.GetY())) for p in fp.Pads()}
    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
    out[fp.GetReference()] = dict(layer=fp.GetLayerName(), pads={k: v for k, v in pads.items() if k in ("1", "2", "3", "26")},
                                  box=[pcbnew.ToMM(v) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())])
e = b.GetBoardEdgesBoundingBox()
out["_edge"] = [pcbnew.ToMM(v) for v in (e.GetX(), e.GetY(), e.GetRight(), e.GetBottom())]
print(json.dumps(out))
'''


def geometry(pcb: Path, work: Path):
    py = K.kicad_python()
    if not py:
        return None
    s = work / "geom_patch.py"
    s.write_text(GEOM, encoding="utf8")
    r = subprocess.run([py, str(s), str(pcb)], capture_output=True, text=True)
    import json
    return json.loads(r.stdout.strip().splitlines()[-1])


def check_geometry(geo: dict) -> list[str]:
    errs = []
    e = geo["_edge"]
    for ref, d in geo.items():
        if ref.startswith("_"):
            continue
        b = d["box"]
        if b[0] < e[0] - 0.01 or b[2] > e[2] + 0.01 or b[1] < e[1] - 0.01 or b[3] > e[3] + 0.01:
            errs.append(f"{ref}: courtyard {b} outside the outline {e}")
    for g in range(1, 5):
        for n in range(1, 5):
            p = geo[f"CM{g}{n}"]["pads"]["1"]
            if abs(p[0] - (GP.X0 + 85.0 * (n - 1))) > 0.02 or abs(p[1] - (18.0 + 30.0 * (g - 1) - 1.42)) > 0.02:
                errs.append(f"CM{g}{n} pin 1 at {p}")
    for slot, ref in enumerate(("JR1", "JR2", "JS1")):
        p = geo[ref]["pads"]["1"]
        if abs(p[0] - (GP.X0 + 85.0 * slot)) > 0.02 or abs(p[1] - (18.0 + 120.0 - 1.42)) > 0.02:
            errs.append(f"{ref} pin 1 at {p}")
    ts = geo["TS1"]["pads"]
    p1, p2, p3 = ts["1"], ts["2"], ts["3"]
    if not (p3[0] > p1[0] and abs(p3[1] - p1[1]) < 0.01 and p2[1] > p1[1] and abs(p2[0] - p1[0]) < 0.01):
        errs.append(f"TS1 pin order: 1 {p1}, 2 {p2}, 3 {p3} (want 1 left, 3 to its right, 2 below 1)")
    if abs(ts["26"][0] - p1[0] - 12 * 2.54) > 0.02:
        errs.append("TS1 pin 26 not 12 pitches right of pin 1")
    return errs


def expected_ratsnest(pcb: Path) -> int:
    from collections import Counter
    txt = pcb.read_text(encoding="utf8")
    cnt = Counter()
    for pm in re.finditer(r'\(pad "[^"]*"', txt):
        e = K.block_end(txt, pm.start())
        nm = re.search(r'\(net \d+ "([^"]*)"\)', txt[pm.start():e])
        if nm and not nm[1].startswith("unconnected"):
            cnt[nm[1]] += 1
    return sum(c - 1 for c in cnt.values() if c > 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("patch-panel")
    work.mkdir(parents=True, exist_ok=True)
    pcb = HERE / "patch_panel.kicad_pcb"
    nl = K.Netlist.export(HERE / "patch_panel.kicad_sch", work)
    pads = K.pcb_pad_nets(pcb, work)
    errs = K.compare_pcb(nl, pads, pcb, skip=lambda r: r == "PL1")
    d = K.run_drc(pcb, work)
    errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
    want = expected_ratsnest(pcb)
    rn = K.ratsnest_count(pcb)
    if rn is not None and rn != want:
        errs.append(f"ratsnest {rn} != {want}")
    geo = geometry(pcb, work)
    if geo:
        errs += check_geometry(geo)
    e = geo["_edge"] if geo else None
    print(f"patch_panel: {len(nl.parts)} parts, {len(nl.nets)} nets, pads read {len(pads)}; DRC {len(d['violations'])} violations, "
          f"{len(d['unconnected'])} unconnected (ratsnest {rn}, expected {want}); "
          + (f"size {e[2] - e[0]:.1f} x {e[3] - e[1]:.1f} mm" if e else "geometry not checked"))
    neg = work / "patch_negative.kicad_pcb"
    neg.write_text(K.swap_two_pad_nets(pcb.read_text(encoding="utf8"), "TS1", "1", "2"), encoding="utf8", newline="\n")
    bad = K.compare_pcb(nl, K.pcb_pad_nets(neg, work), neg, skip=lambda r: r == "PL1")
    print(f"negative test (two pad nets of TS1 swapped): {'detected' if bad else 'NOT DETECTED'}")
    if not bad:
        errs.append("negative test failed")
    neg.unlink()
    print("RESULT:", "PASS" if not errs else "FAIL")
    for x in errs[:10]:
        print("  ", x)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
