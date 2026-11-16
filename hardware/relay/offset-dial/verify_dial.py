#!/usr/bin/env python3
"""Verify the offset dial: copper geometry of the generated board x the rotor model x the counter's preset circuit.

    python verify_dial.py            # ERC, netlist structure, geometry at all 26 detents with tolerances, break-before-make
                                     # sweep, letter scale, preset of the counter unit board netlist for all 26 positions,
                                     # common + unit + dial runs, negative tests
    python verify_dial.py --quick    # shorter system runs

What is read from where (no knowledge of how the generator draws the tracks):
  * copper: the pads of SW1 from offset_dial.kicad_pcb (KiCad Python: the effective polygons incl. holes, the net of
    every pad; dummy islands have no net);
  * letters: the B.SilkS texts of SW1 in the board file;
  * rotor: dialmech.py (the mechanical spec): 11 comb tips on one radial line at R0 + j * PITCH, tip contact radius
    TIP_R, all tips one conductor; at rest the rotor may be off by +-DET_TOL_DEG and the tips by +-RAD_TOL radially;
  * expectation: the letter X under the pointer at a detent means crib offset k = X - 'A'.  The joined nets are wired into
    the counter UNIT board netlist (D-sub 15 cable 1:1 to its J2) and HOME is simulated with counter-board/verify_counter.py
    (switch level); the unit board's CTRL a1 / a2 must equal relay.rs Counters(k) at HOME, i.e. (k, -k mod 26).
"""
from __future__ import annotations

import argparse
import copy
import filecmp
import math
import random
import re
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "position-display"))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402
import dialmech as M  # noqa: E402

N, BITS = 26, 5


class Bad(Exception):
    pass


# ----------------------------------------------------------------------------- geometry
def pip(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def seg_dist(pt, a, b) -> float:
    px, py = pt
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class Pad:
    def __init__(self, d: dict, center):
        self.num, self.net = d["num"], d["net"]
        self.outlines, self.holes = d["outlines"], d["holes"]
        pts = [p for o in self.outlines for p in o]
        self.bbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        rs = [math.hypot(p[0] - center[0], p[1] - center[1]) for p in pts]
        hr = [math.hypot(p[0] - center[0], p[1] - center[1]) for h in self.holes for p in h]
        self.r_mid = (min(rs + hr) + max(rs)) / 2 if hr else (min(rs) + max(rs)) / 2
        self.r_span = (max(hr) if hr else min(rs), max(rs))
        self.r_lim = (min(rs + hr), max(rs))         # radial extent of all copper of the pad (prefilter)

    def touches(self, pt, rad) -> bool:
        x, y = pt
        if x < self.bbox[0] - rad or x > self.bbox[2] + rad or y < self.bbox[1] - rad or y > self.bbox[3] + rad:
            return False
        inside = any(pip(pt, o) for o in self.outlines) and not any(pip(pt, h) for h in self.holes)
        if inside:
            return True
        for ring in self.outlines + self.holes:
            for i in range(len(ring)):
                if seg_dist(pt, ring[i], ring[(i + 1) % len(ring)]) <= rad:
                    return True
        return False


class Stator:
    def __init__(self, dump: dict, board_text: str):
        fp = dump["fps"]["SW1"]
        self.center = (fp["x"], fp["y"])
        pads = [Pad(p, self.center) for p in dump["polys"] if p["layer"] == "F.Cu"]
        # the shaft hole (NPTH, no copper) is a pad too: keep only the copper of the contact band
        self.pads = [p for p in pads if p.r_span[1] > M.R0 - M.PITCH]
        self.ring_of = {}
        for p in self.pads:
            j = round((p.r_mid - M.R0) / M.PITCH)
            self.ring_of[id(p)] = j
        # letters of SW1 on B.SilkS (footprint-relative positions, footprint rotation 0)
        i = board_text.index('(property "Reference" "SW1"')
        start = board_text.rfind("(footprint ", 0, i)
        end = kigen.block_end(board_text, start)
        blk = board_text[start:end]
        self.letters = []
        for m in re.finditer(r'\(fp_text user "([A-Z])"\s*\(at ([-\d.]+) ([-\d.]+)[^)]*\)\s*\(layer "B.SilkS"\)', blk):
            self.letters.append((m[1], self.center[0] + float(m[2]), self.center[1] + float(m[3])))

    def tips(self, phi, dr=0.0):
        return [(j, M.xy(phi, M.track_radius(j) + dr, self.center)) for j in range(M.NTIPS)]

    def read(self, phi, dr=0.0, tip_r=M.TIP_R):
        """(nets joined to the comb, cross-ring contacts, rings whose tip touches nothing)."""
        nets, cross, dry = set(), [], []
        for j, pt in self.tips(phi, dr):
            rt = M.track_radius(j) + dr
            hit = [p for p in self.pads if p.r_lim[0] - tip_r - 0.01 <= rt <= p.r_lim[1] + tip_r + 0.01 and p.touches(pt, tip_r)]
            if not hit:
                dry.append(j)
            for p in hit:
                if self.ring_of[id(p)] != j:
                    cross.append((j, self.ring_of[id(p)]))
                if p.net:
                    nets.add(p.net)
        return frozenset(nets), cross, dry

    def letter_at(self, phi):
        x, y = M.xy(phi, M.R_SCALE, self.center)
        near = [(math.hypot(x - lx, y - ly), s) for s, lx, ly in self.letters]
        d, s = min(near) if near else (99, "?")
        return s if d < 1.0 else "?"


def geometry_check(st: Stator, sweep_step=0.05) -> tuple[dict, dict, list[str]]:
    """Per detent p: the joined net set (identical over all tolerance corners), letter; BBM sweep; margins."""
    errs, joined, info = [], {}, {}
    rings = {}
    for p in st.pads:
        rings.setdefault(st.ring_of[id(p)], set()).add(p.net)
    for j in range(M.NTIPS):
        nets = {n for n in rings.get(j, set()) if n}
        if len(nets) != 1:
            errs.append(f"ring {j}: carries nets {sorted(nets)} (want exactly one)")
    info["ring_nets"] = {j: sorted(n for n in rings.get(j, set()) if n) for j in sorted(rings)}
    com = {n for n in rings.get(0, set()) if n}
    corners = [(e, dr) for e in (-M.DET_TOL_DEG, 0.0, M.DET_TOL_DEG) for dr in (-M.RAD_TOL, 0.0, M.RAD_TOL)]
    for p in range(N):
        phi = p * M.ALPHA
        reads = {}
        for e, dr in corners:
            nets, cross, dry = st.read(phi + e, dr)
            if cross:
                errs.append(f"detent {p} (err {e:+.1f} deg, {dr:+.1f} mm): tip touches another ring {cross[:2]}")
            if dry:
                errs.append(f"detent {p} (err {e:+.1f} deg, {dr:+.1f} mm): tips {dry} touch no copper (dummy islands missing)")
            reads[(e, dr)] = nets
        if len(set(reads.values())) != 1:
            errs.append(f"detent {p}: the code changes within the tolerance (+-{M.DET_TOL_DEG} deg, +-{M.RAD_TOL} mm)")
        joined[p] = reads[(0.0, 0.0)]
        if not com or not com <= joined[p]:
            errs.append(f"detent {p}: COM not joined")
    # break-before-make sweep between neighbouring detents
    n_sweep = 0
    for p in range(N):
        q = (p + 1) % N
        allowed = joined[p] | joined[q]
        steps = int(M.ALPHA / sweep_step)
        for s in range(steps + 1):
            nets, cross, _dry = st.read(p * M.ALPHA + s * sweep_step)
            n_sweep += 1
            if cross:
                errs.append(f"sweep {p}->{q} at +{s * sweep_step:.2f} deg: cross-ring contact {cross[:2]}")
                break
            if not nets <= allowed:
                errs.append(f"sweep {p}->{q} at +{s * sweep_step:.2f} deg: joins {sorted(nets - allowed)} (in neither neighbour code)")
                break
            if com and not com <= nets:
                errs.append(f"sweep {p}->{q} at +{s * sweep_step:.2f} deg: COM lost")
                break
    info["sweep_points"] = n_sweep
    # angular margin: smallest rotor error (0.1 deg steps, any radial corner) at which some detent reads differently
    margin = 99.0
    for p in range(N):
        for sign in (-1, 1):
            for dr in (-M.RAD_TOL, 0.0, M.RAD_TOL):
                e = 0.0
                while e < M.ALPHA / 2:
                    e += 0.1
                    nets, cross, _ = st.read(p * M.ALPHA + sign * e, dr)
                    if nets != joined[p] or cross:
                        break
                margin = min(margin, e - 0.1)
    info["margin_deg"] = margin
    # radial margin at the detents: largest eccentricity (0.05 mm steps) with no cross-ring contact or dry tip
    rmargin = 99.0
    for p in range(0, N, 1):
        for sign in (-1, 1):
            dr = 0.0
            while dr < M.PITCH:
                dr += 0.05
                nets, cross, dry = st.read(p * M.ALPHA, sign * dr)
                if cross or dry or nets != joined[p]:
                    break
            rmargin = min(rmargin, dr - 0.05)
    info["radial_margin_mm"] = rmargin
    letters = [st.letter_at(p * M.ALPHA) for p in range(N)]
    info["letters"] = "".join(letters)
    if "?" in letters or len(set(letters)) != N:
        errs.append(f"letter scale: {''.join(letters)}")
    return joined, info, errs


# ----------------------------------------------------------------------------- counter preset simulation
def node_of(nl: vc.Netlist, net: str, ref="SW1"):
    for (r, p), n in sorted(nl.node_net.items()):
        if r == ref and n == net:
            return p
    raise Bad(f"net {net} is not on {ref}")


def dial_wires(dial: vc.Netlist, joined, tag="D"):
    com = dial.node_net[("SW1", "1")]
    return [((tag, "SW1", node_of(dial, com)), (tag, "SW1", node_of(dial, n))) for n in sorted(joined) if n != com]


def cable(dial: vc.Netlist, unit: vc.Netlist, dtag, utag):
    return [((dtag, "J1", str(p)), (utag, "J2", str(p))) for p in range(1, 16)
            if ("J1", str(p)) in dial.node_net and ("J2", str(p)) in unit.node_net]


def preset_unit(unit, dial, joined, k, rng_state, rng_mode=None):
    wires = cable(dial, unit, "D", "U") + dial_wires(dial, joined)
    drives = {"A2B": [("U", "J1", "1")], "Z2B": [("U", "J1", "2")], "A1B": [("U", "J1", "3")], "Z1B": [("U", "J1", "4")],
              "LOAD": [("U", "J1", "15")]}
    s = vc.System({"U": unit, "D": dial}, wires, [("U", "Q1", "3")], ("U", "J1", "19"), drives)
    vc.randomise(s, rng_state)
    s.settle([], rng_mode); s.settle(["LOAD"], rng_mode); s.settle(["LOAD", "Z2B", "Z1B"], rng_mode)
    seen = s.settle(["Z2B", "Z1B"], rng_mode)
    a1 = sum(1 << b for b in range(BITS) if seen[s.nid("U", "J3", str(b + 1))])
    a2 = sum(1 << b for b in range(BITS) if seen[s.nid("U", "J4", str(b + 1))])
    want = vc.Counters(k).controls()[:2]
    if (a1, a2) != want:
        raise Bad(f"letter {chr(65 + k)} (k = {k}): counter unit preset (a1, a2) = ({a1}, {a2}), expected {want}")
    return a1, a2


def system_run(common, unit, dial, joined, k, clocks):
    """Common + one unit (bus A) + this dial; HOME, then clocks: every CTRL / POS / internal digit vs Counters(k)."""
    boards = {"C": common, "U0": unit, "D0": dial}
    wires = [(("C", "J2", str(p)), ("U0", "J1", str(p))) for p in range(1, 26)
             if ("J2", str(p)) in common.node_net and ("J1", str(p)) in unit.node_net]
    wires += cable(dial, unit, "D0", "U0") + dial_wires(dial, joined, "D0")
    drives = {}
    for name, pin in (("CK", "1"), ("NCK", "2"), ("LOAD", "3")):
        drives[f"{name}_A"] = [("C", "J1", pin)]
        drives[f"{name}_B"] = [("C", "J5", pin)]
        drives[name] = drives[f"{name}_A"] + drives[f"{name}_B"]
    s = vc.System(boards, wires, [("C", "Q1", "3"), ("U0", "Q1", "3")], ("C", "J1", "6"), drives)
    s.ks = [k]
    vc.randomise(s, random.Random(k))
    refs = [vc.Counters(k)]
    seen = vc.do_home(s, None)
    vc.check_state(s, seen, refs, f"k={k} after HOME")
    for step in range(1, clocks + 1):
        refs[0].clock()
        seen1, seen2 = vc.do_clock(s, None)
        vc.check_state(s, seen1, refs, f"k={k} clock {step} (commit)", internal=False)
        vc.check_state(s, seen2, refs, f"k={k} clock {step} (release)")
    return clocks


def structure_errors(dial: vc.Netlist) -> list[str]:
    errs, net = [], dial.node_net.get
    if set(dial.parts) != {"SW1", "J1", "H1", "H2", "H3", "H4"}:
        errs.append(f"parts: {sorted(dial.parts)}")
    for p in range(1, 12):
        if net(("SW1", str(p))) is None or net(("SW1", str(p))) != net(("J1", str(p))):
            errs.append(f"SW1 pin {p} and J1 pin {p} are not one net")
    if len({net(("J1", str(p))) for p in range(1, 12)}) != 11:
        errs.append("J1 pins 1-11 are not 11 distinct nets")
    for p in range(12, 16):
        n = net(("J1", str(p)))
        if n is None or sum(1 for m in dial.node_net.values() if m == n) != 1:
            errs.append(f"J1 pin {p} is connected (0 V / reserved pins stay open on the dial)")
    ch = net(("H1", "1"))
    members = {nd for nd, n in dial.node_net.items() if n == ch}
    if members != {(f"H{k}", "1") for k in range(1, 5)} | {("J1", "SH")}:
        errs.append(f"CHASSIS members: {sorted(members)}")
    return errs


# ----------------------------------------------------------------------------- one full check of a (board, netlist)
def check_board(pcb_text: str, dial: vc.Netlist, unit: vc.Netlist, work: Path, tag: str, trials=1, verbose=False):
    pcb = work / f"{tag}.kicad_pcb"
    pcb.write_text(pcb_text, encoding="utf8", newline="\n")
    d = kigen.dump_board(pcb, "SW1")
    pcb.unlink()
    st = Stator(d, pcb_text)
    joined, info, errs = geometry_check(st)
    if errs:
        return info, joined, errs
    rng = random.Random(3)
    codes = {}
    try:
        for p in range(N):
            k = ord(st.letter_at(p * M.ALPHA)) - 65
            for t in range(trials):
                codes[p] = preset_unit(unit, dial, joined[p], k, rng, random.Random(p * 7 + t) if t % 2 else None)
    except (Bad, vc.Fault, KeyError) as e:
        errs.append(f"counter preset: {e}")
    info["codes"] = codes
    return info, joined, errs


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    work = kigen.build_dir("offset-dial")
    fails = []
    viol = kigen.run_erc(HERE / "offset_dial.kicad_sch", work / "erc.json")
    print(f"ERC (kicad-cli, all severities): {len(viol)} violations")
    if viol:
        fails.append("ERC")
    dial = vc.Netlist(kigen.export_netlist(HERE / "offset_dial.kicad_sch", work / "offset_dial.net").read_text(encoding="utf8"))
    unit = vc.Netlist(kigen.export_netlist(HERE.parent / "counter-board" / "counter_unit.kicad_sch", work / "counter_unit.net").read_text(encoding="utf8"))
    common = vc.Netlist(kigen.export_netlist(HERE.parent / "counter-board" / "counter_common.kicad_sch", work / "counter_common.net").read_text(encoding="utf8"))
    e = structure_errors(dial)
    print(f"structure: SW1 pins 1-11 = J1 pins 1-11 (DIAL_COM, a1 bits 0-4, a2 bits 0-4), J1 12-15 open, CHASSIS = H1-H4 + shell:",
          "OK" if not e else e)
    if e:
        return 1
    vc.selfcheck_reference()
    t0 = time.time()
    text = (HERE / "offset_dial.kicad_pcb").read_text(encoding="utf8")
    info, joined, errs = check_board(text, dial, unit, work, "geom", trials=4)
    if errs:
        print("FAIL:", errs[:6])
        return 1
    st_n = len(info["ring_nets"])
    print(f"copper (pcbnew polygons of SW1): {st_n} rings, one net each (inside out): "
          + ", ".join(f"{j}:{info['ring_nets'][j][0]}" for j in sorted(info["ring_nets"])))
    print(f"scale (B.SilkS under the pointer at detents 0..25): {info['letters']}")
    print(f"detents: 26 positions x 9 tolerance corners (rotor +-{M.DET_TOL_DEG} deg, tips +-{M.RAD_TOL} mm radially, tip contact "
          f"radius {M.TIP_R} mm): same code in every corner, every tip on its own ring, never on a neighbour ring, never dry")
    print(f"margins: the code first changes at a rotor error of {info['margin_deg']:.1f} deg (detent pitch {M.ALPHA:.2f} deg; "
          f"required {M.DET_TOL_DEG}) and a radial error of {info['radial_margin_mm']:.2f} mm (required {M.RAD_TOL})")
    print(f"break-before-make sweep: {info['sweep_points']} angles between neighbouring detents (0.05 deg): every joined pin is 1 in "
          f"one of the two neighbour codes, COM never lost, no cross-ring contact")
    tab = "  ".join(f"{info['letters'][p]}:{c[0]},{c[1]}" for p, c in sorted(info["codes"].items()))
    print(f"counter unit preset (netlist, HOME from random relay states, 4 trials each incl. asynchronous settle): all 26 letters "
          f"-> (a1, a2) == relay.rs Counters(k) at HOME ({time.time() - t0:.0f} s)\n  {tab}")
    t0 = time.time()
    clocks = 700 if a.quick else 2 * 676 + 30
    try:
        for p in (0, 9, 25) if not a.quick else (0, 25):
            k = ord(info["letters"][p]) - 65
            system_run(common, unit, dial, joined[p], k, clocks)
    except (vc.Mismatch, vc.Fault) as ex:
        print("FAIL system run:", ex)
        return 1
    print(f"system: counter common + unit + this dial, letters A / J / Z: HOME, then {clocks} clocks ({clocks // 676} m wrap(s): r and a1 advance): "
          f"CTRL a1..a4, POS and all digits == Counters(k) at every clock ({time.time() - t0:.0f} s)")

    # negative tests: broken variants of the stator (regenerated footprint) and of the wiring
    import gen_dial
    import gen_dial_pcb
    def swap_a1_01(k):
        a1, a2 = gen_dial.home_codes(k)
        b0, b1 = a1 & 1, a1 >> 1 & 1
        return (a1 & ~3) | (b0 << 1) | b1, a2
    variants = [
        ("code: a1 bits 0 and 1 on each other's ring", dict(code=swap_a1_01)),
        ("code: a2 = +k instead of -k", dict(code=lambda k: (k, k))),
        ("scale: letters shifted by one position", dict(letter_shift=1)),
        ("arc ends moved by 0.42 detent pitch (towards the detent)", dict(edge_shift=0.42 * M.ALPHA)),
        ("rings drawn for a comb of 1.5 mm pitch (tips 1.7 mm)", dict(pitch=1.5)),
        ("COM ring broken (20 deg gap at A)", dict(com_gap_deg=20.0)),
    ]
    print("negative tests (must fail):")
    ok = True
    for name, kw in variants:
        fpt = gen_dial.footprint_text(**kw)
        bt, _ = gen_dial_pcb.build(dial, fpt)
        _i, _j, errs = check_board(bt, dial, unit, work, "neg")
        print(f"  {'detected' if errs else 'MISSED  '}: {name} -> {errs[0][:110] if errs else 'NOT DETECTED'}")
        ok &= bool(errs)
    d2 = copy.deepcopy(dial)
    d2.node_net[("J1", "3")], d2.node_net[("J1", "4")] = dial.node_net[("J1", "4")], dial.node_net[("J1", "3")]
    e2 = structure_errors(d2)
    if not e2:
        _i, _j, e2 = check_board(text, d2, unit, work, "neg")
    print(f"  {'detected' if e2 else 'MISSED  '}: wiring: D-sub pins 3/4 (a1 bits 1/2) exchanged -> {e2[0][:110] if e2 else 'NOT DETECTED'}")
    ok &= bool(e2)
    u2 = copy.deepcopy(unit)
    u2.node_net[("J2", "1")] = "/__dial_com_open__"
    try:
        preset_unit(u2, dial, joined[5], 5, random.Random(1))
        e3 = []
    except (Bad, vc.Fault) as ex:
        e3 = [str(ex)]
    print(f"  {'detected' if e3 else 'MISSED  '}: counter side: DIAL_COM not on LOAD (unit J2.1 open) -> {e3[0][:110] if e3 else 'NOT DETECTED'}")
    ok &= bool(e3)
    if not ok:
        fails.append("negative test")

    with tempfile.TemporaryDirectory() as td:
        gen_dial.main(Path(td))
        names = ["offset_dial.kicad_sch", "dial.kicad_sch", "christopher_dial.kicad_sym", "sym-lib-table", "fp-lib-table",
                 "christopher_dial.pretty/OffsetDial26_CodeTracks.kicad_mod"]
        same = all(filecmp.cmp(Path(td) / n_, HERE / n_, shallow=False) for n_ in names)
    print(f"determinism: regenerated schematic, symbol / footprint library and lib tables identical to the repository files: {same}")
    if not same:
        fails.append("schematic / library not deterministic or stale")
    print("RESULT:", "PASS" if not fails else f"FAIL {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
