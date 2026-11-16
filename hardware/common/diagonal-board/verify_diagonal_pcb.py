#!/usr/bin/env python3
"""Check the placed diagonal-board PCBs (d1..d5.kicad_pcb) against their schematics.

    python verify_diagonal_pcb.py [--build-dir DIR]

  1. ERC of the five schematics: 0 violations (all severities);
  2. schematic netlist (kicad-cli) vs board: every part present with the schematic footprint, nothing else,
     pad nets = schematic nets (IPC-D-356 export);
  3. kicad-cli DRC: 0 violations; unconnected items = ratsnest of the schematic (pcbnew count);
  4. geometry of each board (pcbnew): jack grid 110 x 30 mm, the two jacks of a letter one row apart, junction
     headers on the back side with pin 1 at the top, nothing outside the outline;
  5. the five boards on the panel (board positions from gen_diagonal_pcb.panel_offsets, pad positions from the
     boards): ONE jack grid, 13 columns at 110 mm also across the boundaries; boards do not overlap (gap >= 2 mm);
     everything inside the 1,440 mm bank span with >= 10 mm at each end; boards <= 350 mm long;
     the three ribbons of a boundary nest (XR1-XL1 innermost, no crossing), span <= 140 mm, same pin-1 height
     at both ends;
  6. determinism: regenerated schematics and boards are byte-identical to the files;
  7. negative tests: two jack pad nets swapped (per board); a board shifted by 1 mm, two headers of a block
     exchanged, a board too long (panel check).

kicad-cli and pcbnew run on copies in the build directory (gen_diagonal_pcb.stage), with a timeout.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
import gen_diagonal as G  # noqa: E402
import gen_diagonal_pcb as GP  # noqa: E402
import ki_common as K  # noqa: E402

HERE = Path(__file__).resolve().parent
MAX_LEN = 350.0             # hardware/gear/layout.md 3.10 (and well inside the assumed 4-layer fab limit 400-500)
END_MARGIN = 10.0           # minimum free space at each end of the 1,440 mm bank span
MAX_SPAN = 140.0            # ribbon: pin-1 to pin-1 distance of a boundary ribbon

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


def pcbnew_run(args: list[str], timeout=180):
    py = K.kicad_python()
    if not py:
        return None
    return subprocess.run([py, *args], capture_output=True, text=True, timeout=timeout)


def geometry(pcb: Path, work: Path):
    s = work / "geom.py"
    s.write_text(GEOM, encoding="utf8")
    r = pcbnew_run([str(s), str(pcb)])
    return json.loads(r.stdout.strip().splitlines()[-1]) if r else None


def ratsnest(pcb: Path) -> int | None:
    r = pcbnew_run(["-c", "import pcbnew,sys;b=pcbnew.LoadBoard(sys.argv[1]);"
                    "print(b.GetConnectivity().GetUnconnectedCount(False))", str(pcb)])
    return int(r.stdout.split()[-1]) if r else None


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


def check_panel(geos: list[dict], offsets: list[float]) -> tuple[list[str], dict]:
    """The five boards side by side on the panel (x from the left end of the bank span)."""
    errs, info = [], {}
    xs = []                                    # panel x of pin 1 of jack 1 of the upper letter, per column
    for k, (name, cols) in enumerate(G.BOARDS):
        for up, lo in cols:
            xs.append(offsets[k] + geos[k][G.jack_ref(up, 1)]["pads"]["1"][0])
    if len(xs) != 13:
        errs.append(f"{len(xs)} jack columns, expected 13")
    pitches = [b - a for a, b in zip(xs, xs[1:])]
    bad = [f"{i + 1}-{i + 2}: {p:.2f}" for i, p in enumerate(pitches) if abs(p - 110.0) > 0.02]
    if bad:
        errs.append(f"jack column pitch on the panel != 110 at {bad}")
    info["pitch"] = (min(pitches), max(pitches))
    edges = [(offsets[k] + g["_edge"][0], offsets[k] + g["_edge"][2]) for k, g in enumerate(geos)]
    for k, (a, b) in enumerate(edges):
        if b - a > MAX_LEN + 0.01:
            errs.append(f"D{k + 1}: {b - a:.2f} mm long > {MAX_LEN:g}")
    gaps = [edges[k + 1][0] - edges[k][1] for k in range(len(edges) - 1)]
    if min(gaps) < 2.0 - 0.01:
        errs.append(f"boards overlap or touch: gaps {[round(g, 2) for g in gaps]}")
    lo, hi = edges[0][0], edges[-1][1]
    if lo < END_MARGIN - 0.01 or hi > GP.BANK_SPAN - END_MARGIN + 0.01:
        errs.append(f"boards span {lo:.2f} ... {hi:.2f}, outside the {GP.BANK_SPAN:g} mm bank span less {END_MARGIN:g} mm at each end")
    centre0 = xs[0] + GP.JACK_CX
    info.update(span=(lo, hi), gaps=gaps, lengths=[b - a for a, b in edges], centre0=centre0)
    spans = []
    for b in range(len(geos) - 1):
        r = [offsets[b] + geos[b][f"XR{n}"]["pads"]["1"][0] for n in (1, 2, 3)]
        l = [offsets[b + 1] + geos[b + 1][f"XL{n}"]["pads"]["1"][0] for n in (1, 2, 3)]
        s = [l[i] - r[i] for i in range(3)]
        spans.append(s)
        # nested arches: XR3 < XR2 < XR1 < XL1 < XL2 < XL3 (no ribbon starts under another and ends outside it)
        if not (r[2] < r[1] < r[0] < l[0] < l[1] < l[2]):
            errs.append(f"ribbons D{b + 1}-D{b + 2} do not nest (XR {[round(v, 2) for v in r]}, XL {[round(v, 2) for v in l]})")
        if max(s) > MAX_SPAN:
            errs.append(f"ribbons D{b + 1}-D{b + 2}: span {max(s):.1f} > {MAX_SPAN:g} mm")
        for n in (1, 2, 3):
            ya = geos[b][f"XR{n}"]["pads"]["1"][1]
            yb = geos[b + 1][f"XL{n}"]["pads"]["1"][1]
            if abs(ya - yb) > 0.02:
                errs.append(f"ribbon D{b + 1}-D{b + 2} #{n}: pin 1 heights differ ({ya:.2f} vs {yb:.2f})")
    info["spans"] = spans
    return errs, info


def panel_negative_tests(geos: list[dict], offsets: list[float]) -> list[str]:
    """Mutated copies of the measured geometry must fail the panel check."""
    fails = []
    tests = []
    o2 = list(offsets)
    o2[2] += 1.0
    tests.append(("board D3 shifted by 1 mm on the panel", geos, o2))
    g2 = copy.deepcopy(geos)
    g2[1]["XL1"], g2[1]["XL3"] = g2[1]["XL3"], g2[1]["XL1"]
    tests.append(("headers XL1 and XL3 of D2 exchanged (crossing ribbons)", g2, offsets))
    g3 = copy.deepcopy(geos)
    g3[1]["_edge"][2] += 30.0
    tests.append(("board D2 30 mm longer (overlaps D3, > 350 mm)", g3, offsets))
    o4 = [x + 15.0 for x in offsets]
    tests.append(("all boards 15 mm to the right (end margin < 10 mm)", geos, o4))
    for title, g, o in tests:
        bad, _ = check_panel(g, o)
        print(f"  negative test ({title}): {'detected' if bad else 'NOT DETECTED'}")
        if not bad:
            fails.append(title)
    return fails


def determinism(work: Path, nls: list) -> list[str]:
    errs = []
    with tempfile.TemporaryDirectory(dir=work) as td:
        for k, (name, _) in enumerate(G.BOARDS):
            sh = G.build_sheet(k)
            K.write_project_and_sheet(Path(td), name, sh)
            if (Path(td) / f"{name}.kicad_sch").read_bytes() != (HERE / f"{name}.kicad_sch").read_bytes():
                errs.append(f"{name}.kicad_sch is not reproducible")
            text = GP.build(k, nls[k])
            if text != (HERE / f"{name}.kicad_pcb").read_text(encoding="utf8"):
                errs.append(f"{name}.kicad_pcb is not reproducible")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("diagonal-board")
    work.mkdir(parents=True, exist_ok=True)
    all_errs = []
    geos, nls = [], []
    for k, (name, cols) in enumerate(G.BOARDS):
        pcb = HERE / f"{name}.kicad_pcb"
        erc = GP.staged_erc(HERE / f"{name}.kicad_sch", work)
        if erc:
            all_errs.append(f"{name}: ERC {erc} violations")
        nl = GP.export_netlist(HERE / f"{name}.kicad_sch", work)
        nls.append(nl)
        pads = GP.staged_pad_nets(pcb, work)
        errs = K.compare_pcb(nl, pads, pcb)
        d = GP.staged_drc(pcb, work)
        errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
        expect = sum(len(n) - 1 for n in nl.nets.values()) + sum(1 for r in nl.parts if r.startswith('J') and len(r) == 3 and r[1].isalpha())  # 2nd shield pad of each D-sub
        rn = ratsnest(d["staged"])
        if rn is not None and rn != expect:
            errs.append(f"ratsnest {rn} != {expect}")
        geo = geometry(d["staged"], work)
        geos.append(geo)
        if geo:
            errs += check_geometry(k, geo)
        edge = geo["_edge"] if geo else None
        print(f"{name}: ERC {erc}; {len(nl.parts)} parts, {len(nl.nets)} nets, pads read {len(pads)}; DRC {len(d['violations'])} violations, "
              f"{len(d['unconnected'])} unconnected listed, ratsnest {rn} (expected {expect}); "
              f"size {edge[2] - edge[0]:.2f} x {edge[3] - edge[1]:.1f} mm" if edge else f"{name}: geometry not checked")
        all_errs += [f"{name}: {e}" for e in errs]
        # negative test: swap two jack pad nets
        first = G.jack_ref(G.board_letters(k)[0], 1)
        bad_text = K.swap_two_pad_nets(pcb.read_text(encoding="utf8"), first, "1", "2")
        neg = work / f"{name}_negative.kicad_pcb"
        neg.write_text(bad_text, encoding="utf8", newline="\n")
        nerr = K.compare_pcb(nl, GP.staged_pad_nets(neg, work), neg)
        print(f"  negative test (two pad nets of {first} swapped): {'detected' if nerr else 'NOT DETECTED'}")
        if not nerr:
            all_errs.append(f"{name}: negative test failed")
        neg.unlink()
    if all(geos):
        offsets = GP.panel_offsets()
        perr, info = check_panel(geos, offsets)
        all_errs += perr
        lo, hi = info["span"]
        print(f"panel: boards {lo:.2f} ... {hi:.2f} of the {GP.BANK_SPAN:g} mm bank span (free {lo:.2f} / {GP.BANK_SPAN - hi:.2f}), "
              f"length {hi - lo:.2f}; board lengths {', '.join(f'{v:.2f}' for v in info['lengths'])}; gaps {info['gaps'][0]:.2f}; "
              f"jack column pitch {info['pitch'][0]:.2f} ... {info['pitch'][1]:.2f} over all 13 columns, first column centre {info['centre0']:.2f}")
        for b, s in enumerate(info["spans"]):
            print(f"  ribbons D{b + 1}-D{b + 2}: nested, pin-1 spans {' / '.join(f'{v:.1f}' for v in s)} mm, same pin-1 height")
        all_errs += [f"panel negative test missed: {t}" for t in panel_negative_tests(geos, offsets)]
    else:
        all_errs.append("pcbnew not available: geometry not checked")
    det = determinism(work, nls)
    print(f"determinism: {'schematics and boards regenerate byte-identical' if not det else det}")
    all_errs += det
    print("RESULT:", "PASS" if not all_errs else "FAIL")
    for e in all_errs[:20]:
        print("  ", e)
    return 1 if all_errs else 0


if __name__ == "__main__":
    sys.exit(main())
