#!/usr/bin/env python3
"""Compose one scrambler column from the generated boards and check it against the Enigma scrambler.

    python verify_column.py [--positions N] [--exhaustive ORDER] [--seed S]
    "%LOCALAPPDATA%/christopher/venv/Scripts/python.exe" hardware/gear/contact-board/verify_column.py

Model (built from the board FILES, not from the generator):
  * three station boards (contact_board.kicad_pcb): the contact at ring r (from its radius) and position g (from its
    angle, counter-clockwise from the index mark at 12 o'clock) is wired to the pad of the ring header it is on;
  * column terminal and reflector card: their pad nets;
  * eight 1:1 ribbons (pin p <-> pin p): lower R4 - IN, lower R1 - OUT, lower R3 - middle R4, middle R3 - upper R4,
    upper R1 - middle R2, middle R1 - lower R2, upper R3 - reflector in, upper R2 - reflector out;
  * drums from data/wirings/enigma.txt with the drum-PCB conventions (hardware/gear/drum-pcb/README.md): the drum
    showing letter o makes contact position g touch pad index (g + o) mod 26 and
        copy A:  pad 4<i> <-> pad 3<W[i]>        copy B:  pad 1<i> <-> pad 2<W[i]>;
    lower station = Enigma right rotor, middle = middle, upper = left rotor, reflector B.
For every wheel order (rotors I-V, 60 orders, reflector B) and a sample of drum positions the connectivity of the
column must be exactly: IN wire x  <->  OUT wire S(x), nothing else, where S = the scrambler computed from the same
wiring table AND by christopher's Bombe.trace on a one-edge menu (Rust core).  Negative tests (mirrored board, ribbon
pins swapped, wrong reflector, drum offset reversed, ribbons crossed) must fail.
"""
from __future__ import annotations

import argparse
import itertools
import math
import random
import re
import sys
from pathlib import Path

import ki_common as K

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
N = 26
DELTA = 360.0 / N
RING_R = {1: 22.0, 2: 29.0, 3: 36.0, 4: 43.0}
CENTER = (150.0, 100.0)
WIRING = REPO / "data" / "wirings" / "enigma.txt"


# ----------------------------------------------------------------------------- data
def load_wirings():
    rot, refl = {}, {}
    for line in WIRING.read_text(encoding="utf-8").splitlines():
        f = line.split()
        if len(f) >= 3 and f[0] == "rotor":
            rot[f[1]] = [ord(c) - 65 for c in f[2]]
        elif len(f) >= 3 and f[0] == "reflector":
            refl[f[1]] = [ord(c) - 65 for c in f[2]]
    return rot, refl


ROT, REFL = load_wirings()


def scrambler_model(reflector: str, left: str, middle: str, right: str, o_l: int, o_m: int, o_r: int) -> list[int]:
    """S(x): current enters the right drum at wire x, leaves the same cable at S(x) (offsets = drum letters)."""
    def fwd(W, o, c):
        return (W[(c + o) % N] - o) % N

    def bwd(W, o, c):
        return (W.index((c + o) % N) - o) % N
    out = []
    for x in range(N):
        c = fwd(ROT[right], o_r, x)
        c = fwd(ROT[middle], o_m, c)
        c = fwd(ROT[left], o_l, c)
        c = REFL[reflector][c]
        c = bwd(ROT[left], o_l, c)
        c = bwd(ROT[middle], o_m, c)
        c = bwd(ROT[right], o_r, c)
        out.append(c)
    return out


# ----------------------------------------------------------------------------- board file reader
def parse_board(path: Path) -> dict:
    """ref -> dict(x, y, layer, pads={number: net name})."""
    txt = path.read_text(encoding="utf8")
    res = {}
    for m in re.finditer(r'\n\t\(footprint "([^"]+)"', txt):
        s = m.start() + 1
        e = K.block_end(txt, s)
        blk = txt[s:e]
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)[1]
        layer = re.search(r'\(layer "([^"]+)"\)', blk)[1]
        at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)', blk)
        pads = {}
        for pm in re.finditer(r'\(pad "([^"]*)"', blk):
            pe = K.block_end(blk, pm.start())
            nm = re.search(r'\(net \d+ "([^"]*)"\)', blk[pm.start():pe])
            if nm:
                pads[pm[1]] = nm[1]
        res[ref] = dict(fp=m[1], x=float(at[1]), y=float(at[2]), layer=layer, pads=pads)
    return res


class Column:
    """Static part of the column netlist (nets, ribbons), drums added per position."""

    RIBBONS = [("L", "J3", "M", "J4"), ("M", "J3", "U", "J4"), ("U", "J1", "M", "J2"), ("M", "J1", "L", "J2"),
               ("L", "J4", "T", "K1"), ("L", "J1", "T", "K2"), ("U", "J3", "R", "K1"), ("U", "J2", "R", "K2")]

    def __init__(self, station: dict, terminal: dict, card: dict, mirror=False, ribbon_swap=None, ribbons=None):
        self.uf = K.UF()
        self.contact_at: dict[tuple, tuple] = {}           # (ring, g) -> contact ref
        cx, cy = CENTER
        geo = {}
        for ref, d in station.items():
            if re.fullmatch(r"P[1-4][A-Z]", ref):
                dx, dy = d["x"] - cx, d["y"] - cy
                r = math.hypot(dx, dy)
                ring = min(RING_R, key=lambda k: abs(RING_R[k] - r))
                assert abs(RING_R[ring] - r) < 0.01, f"{ref}: radius {r:.3f} is on no ring"
                g = round(math.degrees(math.atan2(-dx, -dy)) / DELTA)
                assert abs(g * DELTA - math.degrees(math.atan2(-dx, -dy))) < 0.01 or abs(abs(g * DELTA - math.degrees(math.atan2(-dx, -dy))) - 360) < 0.01, f"{ref}: angle off the 13.846 deg grid"
                g %= N
                if mirror:
                    g = (-g) % N
                assert ring == int(ref[1])
                geo[ref] = (ring, g)
        assert len(geo) == 104 and len(set(geo.values())) == 104, "104 distinct contact positions expected"
        self.geo = geo
        for s in "LMU":                                    # three identical station boards
            for ref, d in station.items():
                for pad, net in d["pads"].items():
                    self.uf.union((s, ref, pad), (s, "net", net))
        for ref, d in terminal.items():
            for pad, net in d["pads"].items():
                self.uf.union(("T", ref, pad), ("T", "net", net))
        for ref, d in card.items():
            for pad, net in d["pads"].items():
                self.uf.union(("R", ref, pad), ("R", "net", net))
        rib = ribbons or self.RIBBONS
        for a, ra, b, rb in rib:
            for p in range(1, 27):
                q = p
                if ribbon_swap and (a, ra, b, rb) == ribbon_swap[0] and p in ribbon_swap[1]:
                    q = ribbon_swap[1][1] if p == ribbon_swap[1][0] else ribbon_swap[1][0]
                self.uf.union((a, ra, str(p)), (b, rb, str(q)))
        self.inv = {v: k for k, v in geo.items()}      # (ring, g) -> contact ref

    def contact(self, s: str, ring: int, g: int):
        return (s, self.inv[(ring, g % N)], "1")


def connectivity(col: Column, rotors: dict, reflector_expected=None, offsets=None, sign=1) -> list[int | None]:
    """Drums added; returns, for each IN wire x, the OUT wire it reaches (None / -1 if wrong)."""
    uf = K.UF()
    uf.p = dict(col.uf.p)
    for s, rname in rotors.items():
        W = ROT[rname]
        o = offsets[s] * sign
        for g in range(N):
            i = (g + o) % N
            gp = (W[i] - o) % N
            uf.union(col.contact(s, 4, g), col.contact(s, 3, gp))       # copy A
            uf.union(col.contact(s, 1, g), col.contact(s, 2, gp))       # copy B
    groups = uf.groups()
    res = []
    for x in range(N):
        node = ("T", "J1", str(x + 1))
        g = groups[uf.find(node)]
        outs = sorted(m for m in g if m[0] == "T" and m[1] in ("J1", "J2") and m != node)
        if len(outs) == 1 and outs[0][1] == "J2":
            res.append(int(outs[0][2]) - 1)
        else:
            res.append(-1)
    # no other terminal pin of the column may be tied together (checked via the number of terminal members)
    return res


def bombe_scrambler(order: str, left: str, middle: str, right: str, pos: str) -> list[int] | None:
    try:
        sys.path.insert(0, str(REPO / "python"))
        from christopher import Bombe, Menu
    except Exception:
        return None
    m = Menu.from_edges([("A", "B", 0)], "A", "A")
    b = Bombe("enigma-i", diagonal_board=False).plug_up(m)
    out = []
    for x in range(N):
        live = b.trace(f"{order} {left}-{middle}-{right}", pos, input_wire=chr(65 + x))
        cable_b = live[1]
        bits = [w for w in range(N) if cable_b >> w & 1]
        out.append(bits[0] if len(bits) == 1 else -1)
    return out


def sample_positions(n: int, rng: random.Random):
    corner = [(0, 0, 0), (25, 25, 25), (1, 0, 0), (0, 1, 0), (0, 0, 1), (25, 0, 13), (13, 25, 0)]
    pts = list(corner)
    while len(pts) < n:
        pts.append((rng.randrange(N), rng.randrange(N), rng.randrange(N)))
    return pts[:n]


def run_checks(col: Column, orders, positions_per_order, rng, use_bombe=True, reflector="B", mirror_expected=False,
               sign=1, stop_first=False):
    bad = 0
    tested = 0
    bombe_checked = 0
    for left, middle, right in orders:
        rotors = {"U": left, "M": middle, "L": right}
        for (o_l, o_m, o_r) in positions_per_order:
            offs = {"U": o_l, "M": o_m, "L": o_r}
            want = scrambler_model(reflector, left, middle, right, o_l, o_m, o_r)
            got = connectivity(col, rotors, offsets=offs, sign=sign)
            tested += 1
            if got != want:
                bad += 1
                if stop_first:
                    return bad, tested, bombe_checked
    return bad, tested, bombe_checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--positions", type=int, default=40, help="sampled drum positions per wheel order")
    ap.add_argument("--exhaustive", metavar="ORDER", help="all 17576 positions for one order (e.g. II-IV-V) or for all 60 orders (ALL, about 15 min)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--no-bombe", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    station = parse_board(HERE / "contact_board.kicad_pcb")
    terminal = parse_board(HERE / "column_terminal.kicad_pcb")
    card_b = parse_board(HERE / "reflector_B.kicad_pcb")
    card_c = parse_board(HERE / "reflector_C.kicad_pcb")
    col = Column(station, terminal, card_b)
    orders = list(itertools.permutations(["I", "II", "III", "IV", "V"], 3))
    print(f"column: 3 stations x ({len(col.geo)} contacts, 4 headers) + terminal + reflector B + 8 ribbons; {len(orders)} wheel orders")

    # 1. the model's scrambler = christopher's Bombe.trace (Rust core) on a one-edge menu
    if not a.no_bombe:
        sample = rng.sample(orders, 12)
        n = bad = 0
        for left, middle, right in sample:
            for (o_l, o_m, o_r) in sample_positions(6, rng):
                pos = chr(65 + o_l) + chr(65 + o_m) + chr(65 + o_r)
                got = bombe_scrambler("B", left, middle, right, pos)
                if got is None:
                    print("  christopher package not importable: Bombe.trace cross-check skipped")
                    break
                n += 1
                bad += got != scrambler_model("B", left, middle, right, o_l, o_m, o_r)
            else:
                continue
            break
        if n:
            print(f"model scrambler vs Bombe.trace(one-edge menu, Rust core): {n - bad}/{n} identical")
            if bad:
                print("FAIL: model differs from the Rust core")
                return 1

    # 2. composed column vs scrambler
    positions = sample_positions(a.positions, rng)
    bad, tested, _ = run_checks(col, orders, positions, rng)
    print(f"column connectivity vs scrambler: {tested - bad}/{tested} (wheel order, position) cases identical "
          f"({len(orders)} orders x {len(positions)} positions incl. the corners 0/25)")
    errs = bad
    if a.exhaustive:
        trips = orders if a.exhaustive.upper() == "ALL" else [tuple(a.exhaustive.upper().split("-"))]
        allpos = list(itertools.product(range(N), repeat=3))
        b2, t2, _ = run_checks(col, trips, allpos, rng)
        print(f"exhaustive, {'all 60 orders' if len(trips) > 1 else 'order ' + a.exhaustive}: {t2 - b2}/{t2} (order, position) cases identical")
        errs += b2
    if errs:
        print(f"FAIL: {errs} cases differ")
        return 1
    print("PASS: IN wire x reaches exactly OUT wire S(x) for every case")

    # 3. negative tests
    print("negative tests (each must fail on the sampled cases):")
    ok = True
    few = sample_positions(12, random.Random(1))
    few_orders = orders[::7]

    def neg(name, col2, **kw):
        nonlocal ok
        b, t, _ = run_checks(col2, few_orders, few, rng, **kw)
        print(f"  {'detected' if b else 'MISSED  '}: {name} ({b}/{t} cases differ)")
        ok &= b > 0
    neg("board mirrored (contacts clockwise instead of counter-clockwise)", Column(station, terminal, card_b, mirror=True))
    neg("two pins of the lower R3 - middle R4 ribbon swapped (3 <-> 4)",
        Column(station, terminal, card_b, ribbon_swap=(("L", "J3", "M", "J4"), (3, 4))))
    neg("two pins of the IN ribbon swapped (10 <-> 11)", Column(station, terminal, card_b, ribbon_swap=(("L", "J4", "T", "K1"), (10, 11))))
    neg("reflector C card fitted while B is expected", Column(station, terminal, card_c))
    neg("drum offsets reversed (drum turned the wrong way)", col, sign=-1)
    neg("two pins of the upper R3 - reflector ribbon swapped (7 <-> 8)",
        Column(station, terminal, card_b, ribbon_swap=(("U", "J3", "R", "K1"), (7, 8))))
    neg("OUT ribbon connected to the lowest station's ring 2 instead of ring 1",
        Column(station, terminal, card_b, ribbons=[r if r[:2] != ("L", "J1") or r[2] == "M" else ("L", "J2", "T", "K2") for r in Column.RIBBONS]))
    # note: exchanging the two reflector ribbons (ring 2 <-> ring 3) is NOT a fault: the reflector is an involution,
    # ring3[j] - ring2[R[j]] and ring2[j] - ring3[R[j]] are the same set of connections (the card is symmetric).
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
