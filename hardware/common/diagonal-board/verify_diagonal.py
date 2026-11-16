#!/usr/bin/env python3
"""Verify that the union of the five diagonal-board schematics realises exactly Welchman's diagonal board.

    python verify_diagonal.py [--build-dir DIR]

Independent of the generator's net names: the five netlists are exported with kicad-cli, composed through the
junction ribbons (board k connector XRn pin p  <->  board k+1 connector XLn pin p), and the connectivity of the
jack pins is compared with the definition

    wire w of letter c  <->  wire c of letter w    (325 links, c != w),  wire c of letter c: no link,
    both jacks of a letter in parallel, pins 27-29 of every jack = 0 V (one net), shells = CHASSIS.

Checks: letters 6/6/6/4/4; exactly 325 link nets with exactly four jack pins (2 jacks of each letter) and 26
self nets with two pins; no other jack-pin connections; one GND net containing pins 27-29 of all 52 jacks and
nothing else on a jack; CHASSIS separate from GND; junction use per boundary k*(26-k) (120/168/144/88) with at most
3 x 64 pins and a spare reserve.  Negative tests mutate netlist copies or drop a ribbon and must fail.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import ki_common as K  # noqa: E402
from gen_diagonal_pcb import export_netlist_text  # noqa: E402  (kicad-cli helper only; net names are not used)

HERE = Path(__file__).resolve().parent
NAMES = ["d1", "d2", "d3", "d4", "d5"]
LETTERS = ["ABCNOP", "DEFQRS", "GHITUV", "JKWX", "LMYZ"]       # decision 0002 section 9 (hard-coded, independent)
BOUNDARY_NETS = [120, 168, 144, 88]
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def compose(nls: list[K.Netlist], skip_ribbons=frozenset()) -> K.UF:
    """Union-find over (board, ref, pin) nodes: nets of each board + ribbons between boards."""
    uf = K.UF()
    for k, nl in enumerate(nls):
        for nodes in nl.nets.values():
            a = (k,) + nodes[0]
            uf.find(a)
            for nd in nodes[1:]:
                uf.union(a, (k,) + nd)
    for b in range(len(nls) - 1):
        for n in (1, 2, 3):
            if (b, n) in skip_ribbons:
                continue
            for p in range(1, 65):
                uf.union((b, f"XR{n}", str(p)), (b + 1, f"XL{n}", str(p)))
    return uf


def check(nls: list[K.Netlist], skip_ribbons=frozenset(), quiet=True, stats=None) -> list[str]:
    errs: list[str] = []
    log = (lambda *a: None) if quiet else print
    # --- letters and parts
    for k, nl in enumerate(nls):
        have = sorted({r[1] for r in nl.parts if r.startswith("J") and len(r) == 3 and r[1] in ALPHA})
        if have != sorted(LETTERS[k]):
            errs.append(f"board {k + 1}: letters {''.join(have)} != {LETTERS[k]}")
        for c in LETTERS[k]:
            for n in (1, 2):
                if f"J{c}{n}" not in nl.parts:
                    errs.append(f"board {k + 1}: jack J{c}{n} missing")
        hdr = sorted(r for r in nl.parts if r.startswith(("XL", "XR")))
        want = (["XL1", "XL2", "XL3"] if k > 0 else []) + (["XR1", "XR2", "XR3"] if k < 4 else [])
        if hdr != sorted(want):
            errs.append(f"board {k + 1}: junction headers {hdr} != {want}")
    uf = compose(nls, skip_ribbons)
    groups = uf.groups()

    jack_sig: dict[tuple, tuple[str, int, int]] = {}   # node -> (letter, jack, wire)
    jack_ref: dict[tuple, str] = {}
    for k in range(5):
        for c in LETTERS[k]:
            for n in (1, 2):
                for w in range(26):
                    jack_sig[(k, f"J{c}{n}", str(w + 1))] = (c, n, w)

    # --- signal nets: connectivity restricted to jack signal pins
    seen = set()
    nlinks = nself = 0
    for node, (c, n, w) in jack_sig.items():
        root = uf.find(node)
        if root in seen:
            continue
        seen.add(root)
        members = {jack_sig[m] for m in groups[root] if m in jack_sig}
        letters_involved = sorted({(m[0], m[2]) for m in members})
        # expected: wire w of letter c  with  wire c of letter w   (both jacks each)
        wl = ALPHA[w]
        if wl == c:
            want = {(c, 1, w), (c, 2, w)}
            nself += 1
        else:
            want = {(c, 1, w), (c, 2, w), (wl, 1, ALPHA.index(c)), (wl, 2, ALPHA.index(c))}
            nlinks += 1
        if members != want:
            errs.append(f"jack pin {c}{n}.{w + 1} (wire {wl} of letter {c}): connected to "
                        f"{sorted(f'{a}{b}.{x + 1}' for a, b, x in members)}, expected {sorted(f'{a}{b}.{x + 1}' for a, b, x in want)}")
    if nlinks != 325:
        errs.append(f"{nlinks} link nets, expected 325")
    if nself != 26:
        errs.append(f"{nself} self nets, expected 26")

    # --- GND and CHASSIS
    gnd_nodes = {(k, f"J{c}{n}", str(p)) for k in range(5) for c in LETTERS[k] for n in (1, 2) for p in (27, 28, 29)}
    roots = {uf.find(x) for x in gnd_nodes}
    if len(roots) != 1:
        errs.append(f"pins 27-29 of the jacks are on {len(roots)} nets, expected 1 (0 V reference)")
    else:
        gm = groups[next(iter(roots))]
        extra = {m for m in gm if m in jack_sig}
        if extra:
            errs.append(f"0 V net touches jack signal pins {sorted(extra)[:4]}")
        if not any(m[1] == "J0V" for m in gm):
            errs.append("0 V net has no power reference terminal J0V")
        for k in range(5):
            if not any(m[0] == k and m[1].startswith(("R", "C")) for m in gm):
                errs.append(f"board {k + 1}: no CHASSIS RC on the 0 V net")
        gnd_root = next(iter(roots))
    sh_nodes = {(k, f"J{c}{n}", "SH") for k in range(5) for c in LETTERS[k] for n in (1, 2)}
    sh_roots = {uf.find(x) for x in sh_nodes}
    if len(sh_roots) != 5:           # CHASSIS is per board (ribbons carry no chassis)
        errs.append(f"shells on {len(sh_roots)} nets, expected 5 (one CHASSIS net per board)")
    if len(roots) == 1 and gnd_root in sh_roots:
        errs.append("CHASSIS is shorted to 0 V")

    # --- junction use
    for b in range(4):
        used = set()
        for n in (1, 2, 3):
            for p in range(1, 65):
                node = (b, f"XR{n}", str(p))
                if node in uf.p and len(groups[uf.find(node)]) > 2:
                    used.add((n, p))
        sig = {u for u in used if not (u[0] == 3 and u[1] >= 61)}
        if len(sig) != BOUNDARY_NETS[b]:
            errs.append(f"boundary {b + 1}: {len(sig)} junction pins in use, expected {BOUNDARY_NETS[b]}")
        if stats is not None:
            stats[f"boundary D{b + 1}-D{b + 2}"] = (len(sig), 192 - len(sig) - 4)
    log(f"  links {nlinks}, self nets {nself}, GND nets {len(roots)}")
    return errs


NEG = [
    ("swap two wires on a letter jack (JA1 pins 2 and 3)", 0, "JA1", "2", "3"),
    ("swap a wire and a 0 V pin on a letter jack (JZ2 pins 26 and 27)", 4, "JZ2", "26", "27"),
    ("swap two junction pins (D2 XR1 pins 5 and 6)", 1, "XR1", "5", "6"),
    ("swap two junction pins on the far side of a boundary (D4 XL2 pins 1 and 40)", 3, "XL2", "1", "40"),
    ("swap a signal and a 0 V pin of a junction (D3 XL3 pins 40 and 61)", 2, "XL3", "40", "61"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("diagonal-board")
    work.mkdir(parents=True, exist_ok=True)
    texts = [export_netlist_text(HERE / f"{n}.kicad_sch", work) for n in NAMES]   # kicad-cli on a staged copy, timeout
    nls = [K.Netlist(t) for t in texts]
    print("netlists:", ", ".join(f"{n}: {len(nl.parts)} parts / {len(nl.nets)} nets" for n, nl in zip(NAMES, nls)))
    stats: dict = {}
    errs = check(nls, stats=stats, quiet=False)
    for k, (used, spare) in stats.items():
        print(f"  {k}: {used} junction pins in use, {spare} spare (of 3 x 64, 4 reserved for 0 V)")
    if errs:
        print(f"FAIL: {len(errs)} discrepancies, first: {errs[:5]}")
        return 1
    print("PASS: 325 links + 26 self nets, 4 jack pins each, 0 V and CHASSIS as specified")
    ok = True
    print("negative tests (mutated copies must fail):")
    for name, k, ref, pa, pb in NEG:
        t2 = list(texts)
        t2[k] = K.swap_pins(texts[k], ref, pa, pb)
        bad = check([K.Netlist(t) for t in t2])
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} discrepancies)")
        ok &= bool(bad)
    for name, skip in [("missing ribbon D2-D3 connector 2", {(1, 2)}), ("missing ribbon D4-D5 connector 1", {(3, 1)}),
                       ("missing 0 V ribbon D1-D2 connector 3", {(0, 3)})]:
        bad = check(nls, skip_ribbons=skip)
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} discrepancies)")
        ok &= bool(bad)
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
