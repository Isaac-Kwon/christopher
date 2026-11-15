#!/usr/bin/env python3
"""Verify the menu patch panel board schematic: ERC, connectivity, test-plug simulation, negative tests.

    python verify_patch.py [--build-dir DIR]

From the exported netlist only (kicad-cli):
  * ERC: 0 violations;  19 male D-sub 37 jacks (CM11..CM44, JR1, JR2, JS1);
  * commons: pin k of the four jacks of a group are one net (4 pads), no connection between groups or to any other
    signal; register jacks JR1/JR2 pin k on one net (2 pads); searching jack JS1 pin k <-> hole k of TS1;
  * 0 V: pins 27-29 of all 19 jacks, J_TEST pin 2, J0V (both pins), R1 and C1 on ONE net, no signal pin on it;
    shells + mounting holes = CHASSIS (separate from 0 V, joined only through R1/C1 which are not unioned);
  * test plug: TEST (J_TEST pin 1; the flying plug PL1 is not in the netlist) is isolated with the plug out; with PL1 pushed into hole k (k = 1..26) TEST reaches
    exactly JS1 pin k (and hole k) and nothing else (no 0 V, no other wire, no common, no register line);
  * negative tests: swapped pins on a common jack, on JS1, in the hole field, TEST/0 V terminal swapped must be detected.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ki_common as K

HERE = Path(__file__).resolve().parent
COMMONS = [f"CM{g}{n}" for g in range(1, 5) for n in range(1, 5)]
JACKS = COMMONS + ["JR1", "JR2", "JS1"]


def build_uf(nl: K.Netlist) -> K.UF:
    uf = K.UF()
    for nodes in nl.nets.values():
        uf.find(nodes[0])
        for nd in nodes[1:]:
            uf.union(nodes[0], nd)
    return uf


def check(nl: K.Netlist) -> list[str]:
    errs = []
    jacks = sorted(r for r in nl.parts if r.startswith(("CM", "JR", "JS")))
    if jacks != sorted(JACKS):
        errs.append(f"jacks {jacks} != {sorted(JACKS)}")
    for r in JACKS:
        if "DSUB-37_Pins" not in nl.parts.get(r, {}).get("footprint", ""):
            errs.append(f"{r}: not a male D-sub 37 footprint")
    uf = build_uf(nl)
    groups = uf.groups()
    sig = {(r, str(k)) for r in JACKS for k in range(1, 27)}

    def sigmembers(node):
        return {m for m in groups[uf.find(node)] if m in sig}
    ts = {("TS1", str(k)) for k in range(1, 27)}
    for k in range(1, 27):
        for g in range(1, 5):
            want = {(f"CM{g}{n}", str(k)) for n in range(1, 5)}
            got = sigmembers((f"CM{g}1", str(k)))
            if got != want:
                errs.append(f"common {g} wire {k}: {sorted(got)} != {sorted(want)}")
            extra = {m for m in groups[uf.find((f"CM{g}1", str(k)))] if m not in sig}
            if extra:
                errs.append(f"common {g} wire {k} touches {sorted(extra)[:3]}")
        got = sigmembers(("JR1", str(k)))
        if got != {("JR1", str(k)), ("JR2", str(k))}:
            errs.append(f"register wire {k}: {sorted(got)}")
        if {m for m in groups[uf.find(("JR1", str(k)))] if m not in sig}:
            errs.append(f"register wire {k} touches other parts")
        grp = groups[uf.find(("JS1", str(k)))]
        if grp != {("JS1", str(k)), ("TS1", str(k))}:
            errs.append(f"search wire {k}: {sorted(grp)} != JS1+TS1 pin {k}")
    # 0 V
    gnd_nodes = {(r, str(p)) for r in JACKS for p in (27, 28, 29)} | {("J_TEST", "2"), ("J0V", "1"), ("J0V", "2")}
    roots = {uf.find(x) for x in gnd_nodes}
    if len(roots) != 1:
        errs.append(f"0 V pins on {len(roots)} nets")
    else:
        gm = groups[next(iter(roots))]
        if gm & sig or gm & ts:
            errs.append("0 V net touches a signal pin")
        if not any(m[0] in ("R1", "C1") for m in gm):
            errs.append("no CHASSIS RC on the 0 V net")
    sh = {uf.find((r, "SH")) for r in JACKS} | {uf.find((f"H{h}", "1")) for h in range(1, 7)}
    if len(sh) != 1:
        errs.append(f"shells/mounting holes on {len(sh)} nets")
    elif roots and next(iter(sh)) == next(iter(roots)):
        errs.append("CHASSIS shorted to 0 V")
    # test plug
    t0 = ("J_TEST", "1")
    # PL1 (flying plug, on_board = no) is not in the exported netlist: the plug is simulated by joining TEST to hole k
    if groups[uf.find(t0)] != {t0}:
        errs.append(f"TEST net with the plug out: {sorted(groups[uf.find(t0)])} (must be J_TEST pin 1 alone)")
    for k in range(1, 27):
        u2 = K.UF()
        u2.p = dict(uf.p)
        u2.union(t0, ("TS1", str(k)))
        g2 = u2.groups()[u2.find(t0)]
        want = {t0, ("TS1", str(k)), ("JS1", str(k))}
        if g2 != want:
            errs.append(f"plug in hole {k}: TEST reaches {sorted(g2 - want)[:4]} / lacks {sorted(want - g2)[:4]}")
    return errs


NEG = [("swap two wires on a common jack (CM21 pins 5, 6)", "CM21", "5", "6"),
       ("swap two wires on the searching jack (JS1 pins 1, 2)", "JS1", "1", "2"),
       ("swap two holes of the hole field (TS1 pins 7, 8)", "TS1", "7", "8"),
       ("swap TEST and 0 V on the test lead terminal (J_TEST 1, 2)", "J_TEST", "1", "2"),
       ("swap a wire and a 0 V pin on the register jack (JR2 pins 26, 27)", "JR2", "26", "27")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("patch-panel")
    work.mkdir(parents=True, exist_ok=True)
    sch = HERE / "patch_panel.kicad_sch"
    erc = K.run_erc(sch, work)
    nl = K.Netlist.export(sch, work)
    print(f"patch_panel: ERC {erc['count']} violations; {len(nl.parts)} parts, {len(nl.nets)} nets")
    errs = [f"ERC {erc['count']}"] if erc["count"] else []
    errs += check(nl)
    if errs:
        print(f"FAIL: {len(errs)} problems, first: {errs[:5]}")
        return 1
    print("PASS: commons, register jacks, searching set, test-plug simulation (26 holes), 0 V and CHASSIS as specified")
    text = (work / "patch_panel.net").read_text(encoding="utf8")
    ok = True
    print("negative tests (netlist copies must fail):")
    for name, ref, pa, pb in NEG:
        bad = check(K.Netlist(K.swap_pins(text, ref, pa, pb)))
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} problems)")
        ok &= bool(bad)
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
