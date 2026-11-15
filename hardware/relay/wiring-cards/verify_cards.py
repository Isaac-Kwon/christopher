#!/usr/bin/env python3
"""Verify the relay wiring cards by composing a FULL relay unit netlist and tracing it against the Enigma scrambler.

    python verify_cards.py [--build-dir DIR] [--samples 50] [--exhaustive "B II IV V"] [--quick]

What is composed (everything from the actual generated schematics, exported with kicad-cli):
  * 4 shifter boards, hardware/relay/shifter-board/shifter_board.kicad_sch (S1..S4, 130 relays each),
  * 3 W cards (rotor_<name>.kicad_sch) for the chosen wheel order, 1 U card (reflector_<name>.kicad_sch),
  * the 14 straight 37-way ribbons (pin n to pin n) of layout.md 3.4, DERIVED from the card poses of the stack
    (which edge/slot each connector of each card has, a4 facing front, all others facing back) and compared with the
    explicit table of layout.md; the IN and OUT leads are S1.J1 / S1.J4.  S4 is the front-facing VARIANT of the
    shifter board (shifter_board_s4.kicad_pcb: J1-J4 mirrored on the back side), so its jumpers are 25 / 50 mm.
  * PIN-1 ALIGNMENT from the real board files (footprint poses of shifter_board.kicad_pcb for S1-S3,
    shifter_board_s4.kicad_pcb for S4, rotor_I / reflector_B for W / U): both sockets of every ribbon must have pin 1
    at the same end (a D-sub is keyed: the ribbon cannot be flipped), and so must the IN / OUT lead sockets with the
    rest of their edge/slot chain.  The old configuration (the standard shifter as S4) is a negative test: it fails
    on exactly the four jumpers S4.J1-U.J3, S4.J2-W_L.J4, S4.J3-W_L.J1, S4.J4-U.J2.
Simulation: the shifter stage relays are set from Controls::from_cores (Python port of relay.rs: a1 = r+k,
a2 = m-(r+k), a3 = l-m, a4 = l, S4 mounted reversed), relay contacts closed per the G6S-2 pin functions of
verify_shifter.py, connectivity by union-find over nets + closed contacts + ribbons.  For every position the 26
lines IN (S1.J1 pin i) must reach exactly OUT (S1.J4 pin s(i)) with s = the Enigma scrambler (christopher
Bombe.trace on a one-edge menu; the Python port of WheelOrder::scrambler is cross-checked against it), with
no other connection (no shorts between lines, none to the control pins).

Coverage: all 60 Enigma I wheel orders with reflector B at --samples random positions each (random crib offset k on
a third of them), all 336 Navy/M3 three-rotor orders with B and C at a few positions each, and one wheel order
exhaustively (17,576 positions).  Negative tests (a swapped ribbon, ribbons to the wrong slot, wrong/misplaced
cards, a card upside down, a4 not reversed, wrong reflector, mutated card netlists) must all fail.
"""
from __future__ import annotations

import argparse
import itertools
import random
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
import gen_cards as C  # noqa: E402
import ki_common as K  # noqa: E402

HERE = Path(__file__).resolve().parent
SHIFTER_DIR = HERE.parent / "shifter-board"
sys.path.insert(0, str(SHIFTER_DIR))
import verify_shifter_pcb as vsp  # noqa: E402  (board_parts / pin1_end of the real footprint poses)
N = 26
NPINS = 37

# G6S-2 pin functions (see gen_shifter.py / verify_shifter.py): coil 1(+)/12(-), pole 1 = 4/3/5, pole 2 = 9/10/8
COIL_P = "1"
POLES = [("4", "3", "5"), ("9", "10", "8")]
BITS = 5


# ============================================================================= golden model ports
def from_cores(l: int, m: int, r: int, k: int) -> dict:
    """Port of relay.rs Controls::from_cores (a4 = l: the hardware counts l, the reversed shifter shifts by -l)."""
    rk = (r + k % N) % N
    return {"S1": rk, "S2": (m - rk) % N, "S3": (l - m) % N, "S4": l}


def read_wirings() -> tuple[dict, dict]:
    rot, refl = {}, {}
    for line in C.WIRING_FILE.read_text(encoding="utf-8").splitlines():
        f = line.split()
        if len(f) >= 3 and not line.startswith("#"):
            if f[0] == "rotor":
                rot[f[1]] = [ord(c) - 65 for c in f[2]]
            elif f[0] == "reflector":
                refl[f[1]] = [ord(c) - 65 for c in f[2]]
    return rot, refl


ROT, REFL = read_wirings()


def scrambler_port(order: tuple, l: int, m: int, r: int) -> list[int]:
    """Python port of WheelOrder::scrambler: order = (reflector, left, middle, right), core offsets (l, m, r)."""
    refl, (lw, mw, rw) = REFL[order[0]], [ROT[x] for x in order[1:]]

    def fw(w, o, c):
        return (w[(c + o) % N] - o) % N

    def bw(w, o, c):
        return (w.index((c + o) % N) - o) % N
    out = []
    for c in range(N):
        x = fw(rw, r, c)
        x = fw(mw, m, x)
        x = fw(lw, l, x)
        x = refl[x]
        x = bw(lw, l, x)
        x = bw(mw, m, x)
        out.append(bw(rw, r, x))
    return out


class Christopher:
    """The christopher package as oracle: Bombe.trace on a one-edge menu (A -> B), input wire w, gives scrambler(w)."""

    def __init__(self):
        from christopher import Bombe, Menu, WheelOrder
        self.WheelOrder = WheelOrder
        self.bombes = [Bombe("m3", diagonal_board=False).plug_up(Menu.from_edges([("A", "B", 0)], "A", chr(65 + w)))
                       for w in range(N)]
        self._orders = {}

    def scrambler(self, order: tuple, l: int, m: int, r: int) -> list[int]:
        wo = self._orders.get(order)
        if wo is None:
            wo = self._orders[order] = self.WheelOrder.parse(f"{order[0]} {order[1]} {order[2]} {order[3]}")
        pos = chr(65 + l) + chr(65 + m) + chr(65 + r)
        return [self.bombes[w].trace(wo, pos)[1].bit_length() - 1 for w in range(N)]


# ============================================================================= union-find (small and fast)
class UF:
    def __init__(self, p=None):
        self.p = p if p is not None else {}

    def find(self, a):
        p = self.p
        r = a
        while True:
            q = p.get(r)
            if q is None:
                p[r] = r
                return r
            if q == r:
                break
            r = q
        while a != r:          # path compression
            p[a], a = r, p[a]
        return r

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb

    def copy(self) -> "UF":
        return UF(dict(self.p))


# ============================================================================= card types
class CardType:
    """A card type from a netlist: nets as node lists; for a shifter also the relay structure."""

    def __init__(self, key: str, nl: K.Netlist):
        self.key, self.nl = key, nl
        self.stage_pairs: dict[int, tuple[list, list]] = {}   # shifter: bit -> (closed-when-on pairs, closed-when-off pairs)
        if key == "shifter":
            self._relays()

    def _relays(self):
        nl = self.nl
        net_of = nl.node_net
        relays = [r for r in nl.parts if r[0] == "K" and r[1:].isdigit()]
        for b in range(BITS):
            cnet = net_of.get(("J5", str(b + 1)))
            coils = [r for r in relays if net_of.get((r, COIL_P)) == cnet]
            if len(coils) != N:
                raise SystemExit(f"shifter control bit {b}: {len(coils)} coils on J5 pin {b + 1}")
            on, off = [], []
            for r in coils:
                for com, nc, no in POLES:
                    on.append(((r, com), (r, no)))
                    off.append(((r, com), (r, nc)))
            self.stage_pairs[b] = (on, off)


def export(sch: Path, work: Path) -> K.Netlist:
    return K.Netlist.export(sch, work)


def load_types(work: Path) -> dict[str, CardType]:
    """Netlists of the shifter board and of every wiring card, exported from the real schematics."""
    sd = work / "shifter-board"
    sd.mkdir(parents=True, exist_ok=True)
    for f in SHIFTER_DIR.glob("*.kicad_sch"):          # export from a copy: kicad-cli would write .kicad_prl next to it
        shutil.copyfile(f, sd / f.name)
    shutil.copyfile(SHIFTER_DIR / "shifter_board.kicad_pro", sd / "shifter_board.kicad_pro")
    types = {"shifter": CardType("shifter", export(sd / "shifter_board.kicad_sch", work))}
    for proj, kind, name in C.all_cards():
        types[proj] = CardType(proj, export(HERE / f"{proj}.kicad_sch", work))
    return types


# ============================================================================= stack model and ribbons
# connector -> (edge, slot) in the stack (viewed from the front, card rotated so the connector edges are top and bottom)
POSE = {"back": {"J1": ("top", "h1"), "J2": ("top", "h2"), "J3": ("bot", "h1"), "J4": ("bot", "h2")},
        "front": {"J1": ("bot", "h1"), "J2": ("bot", "h2"), "J3": ("top", "h1"), "J4": ("top", "h2")}}
CONNECTORS = {"shifter": ("J1", "J2", "J3", "J4"), "W": ("J1", "J2", "J3", "J4"), "U": ("J2", "J3")}
# stack from the front (Z = 0) to the back, layout.md 3.2: (instance, card kind, facing, Z of the board front face)
STACK = [("S4", "shifter", "front", 0), ("U", "U", "back", 25), ("W_L", "W", "back", 50), ("S3", "shifter", "back", 75),
         ("W_M", "W", "back", 100), ("S2", "shifter", "back", 125), ("W_R", "W", "back", 150), ("S1", "shifter", "back", 175)]

# layout.md 3.4 ribbon table (pairs of connectors; the IN / OUT leads are the unpaired ends), with the W/U connector
# names of this folder (README.md section 2)
LAYOUT_RIBBONS = {
    # bottom h1
    frozenset([("S4", "J1"), ("U", "J3")]), frozenset([("W_L", "J3"), ("S3", "J3")]),
    frozenset([("W_M", "J3"), ("S2", "J3")]), frozenset([("W_R", "J3"), ("S1", "J3")]),
    # bottom h2 (S1.J4 -> OUT jack)
    frozenset([("S4", "J2"), ("W_L", "J4")]), frozenset([("S3", "J4"), ("W_M", "J4")]),
    frozenset([("S2", "J4"), ("W_R", "J4")]),
    # top h1 (IN jack -> S1.J1)
    frozenset([("S4", "J3"), ("W_L", "J1")]), frozenset([("S3", "J1"), ("W_M", "J1")]),
    frozenset([("S2", "J1"), ("W_R", "J1")]),
    # top h2
    frozenset([("S4", "J4"), ("U", "J2")]), frozenset([("W_L", "J2"), ("S3", "J2")]),
    frozenset([("W_M", "J2"), ("S2", "J2")]), frozenset([("W_R", "J2"), ("S1", "J2")]),
}
LAYOUT_LEADS = {"IN": ("S1", "J1"), "OUT": ("S1", "J4")}


def derive_ribbons(stack=STACK, s4_variant=True):
    """Straight ribbons from the poses: at each (edge, slot) the connectors in stack order are joined pairwise
    front to back; an unpaired last connector is a lead to the panel jack.  Returns (pairs, leads, lengths).
    The standard shifter facing front has its sockets on the component side, 14 mm in front of its board
    (+14 mm per ribbon); the S4 variant has them on the back side like every other card."""
    chains: dict[tuple, list] = {}
    z = {inst: zz for inst, _, _, zz in stack}
    facing = {inst: f for inst, _, f, _ in stack}
    for inst, kind, fc, _ in stack:
        for conn in CONNECTORS[kind]:
            chains.setdefault(POSE[fc][conn], []).append((inst, conn))
    pairs, leads, lengths = [], [], []
    for key, chain in sorted(chains.items()):
        for k in range(0, len(chain) - 1, 2):
            a, b = chain[k], chain[k + 1]
            pairs.append(frozenset([a, b]))
            lengths.append(z[b[0]] - z[a[0]] + (14 if facing[a[0]] == "front" and not s4_variant else 0))
        if len(chain) % 2:
            leads.append(chain[-1])
    return pairs, leads, lengths


def board_pin1_ends() -> dict:
    """{(kind key, connector): 'L'/'R'} from the real board files, kind key in std / s4 / W / U.  'L' = pin 1 at the
    smaller board y of the connector's pin row.  In the stack frame the along-edge coordinate is +board y for BOTH
    facings (a front-facing card is seen from its component side rotated counter-clockwise, a back-facing one from
    the solder side rotated clockwise: both map board y to +x, README.md section 2), so the board-frame end is the
    stack-frame end."""
    files = {"std": SHIFTER_DIR / "shifter_board.kicad_pcb", "s4": SHIFTER_DIR / "shifter_board_s4.kicad_pcb",
             "W": HERE / "rotor_I.kicad_pcb", "U": HERE / "reflector_B.kicad_pcb"}
    out = {}
    for key, f in files.items():
        for ref, d in vsp.board_parts(f.read_text(encoding="utf8")).items():
            if re.fullmatch(r"J[1-4]", ref):
                out[(key, ref)] = vsp.pin1_end(d)
    return out


def _key(inst: str, s4_board: str) -> str:
    return {"S4": s4_board, "W_L": "W", "W_M": "W", "W_R": "W", "U": "U"}.get(inst, "std")


def pin1_check(stack, s4_board="s4", ends=None, s4_variant=True) -> list[str]:
    """Pin-1 alignment of the 14 jumpers and the IN / OUT leads; returns the list of problems (empty = aligned).
    stack: (instance, kind, facing, z); the shifter of S4 is the board file `s4_board` (std or s4), the others std."""
    ends = ends or board_pin1_ends()
    pairs, leads, _ = derive_ribbons(stack, s4_variant)
    end = {(inst, c): ends[(_key(inst, s4_board), c)] for inst, kind, _, _ in stack for c in CONNECTORS[kind]}
    bad = []
    for a, b in sorted(map(sorted, pairs)):
        if end[a] != end[b]:
            bad.append(f"{a[0]}.{a[1]} (pin 1 {end[a]}) - {b[0]}.{b[1]} (pin 1 {end[b]})")
    # a lead runs along its edge/slot chain as the continuation of the jumper before it: its socket must have the
    # pin-1 end of that neighbouring connector (the jack end of the lead is then wired the same way for IN and OUT)
    facing = {i: f for i, _, f, _ in stack}
    chain_of = {}
    for inst, kind, fc, _ in stack:
        for c in CONNECTORS[kind]:
            chain_of.setdefault(POSE[fc][c], []).append((inst, c))
    for lead in leads:
        chain = chain_of[POSE[facing[lead[0]]][lead[1]]]
        prev = chain[chain.index(lead) - 1]
        if end[prev] != end[lead]:
            bad.append(f"lead on {lead[0]}.{lead[1]} (pin 1 {end[lead]}) vs its neighbour {prev[0]}.{prev[1]} (pin 1 {end[prev]})")
    return bad


class Ribbon:
    def __init__(self, a, b, pinmap=None):
        self.a, self.b = a, b
        self.pinmap = pinmap or {}

    def pins(self):
        for p in range(1, NPINS + 1):
            yield p, self.pinmap.get(p, p)


class Unit:
    """cards: instance -> card type key; ribbons; leads = {'IN': (inst, conn), 'OUT': (inst, conn)}."""

    def __init__(self, types: dict[str, CardType], cards: dict[str, str], ribbons: list[Ribbon], leads: dict):
        self.types, self.cards, self.ribbons, self.leads = types, cards, ribbons, leads
        base = UF()
        for inst, key in cards.items():
            for nodes in types[key].nl.nets.values():
                for nd in nodes[1:]:
                    base.union((inst,) + nodes[0], (inst,) + nd)
        for rb in ribbons:
            for pa, pb in rb.pins():
                base.union((rb.a[0], rb.a[1], str(pa)), (rb.b[0], rb.b[1], str(pb)))
        for a in list(base.p):
            base.find(a)
        self.base = base
        self.shifters = [i for i, k in cards.items() if k == "shifter"]
        self.in_pins = [(leads["IN"][0], leads["IN"][1], str(i + 1)) for i in range(N)]
        self.out_pins = [(leads["OUT"][0], leads["OUT"][1], str(i + 1)) for i in range(N)]
        self.j5 = [(s, "J5", str(p)) for s in self.shifters for p in range(1, 10)]

    def simulate(self, controls: dict) -> list[str]:
        """Set the stage relays from the control values, trace IN -> OUT; return [] or the permutation read."""
        uf = self.base.copy()
        for inst in self.shifters:
            a = controls[inst]
            for b in range(BITS):
                for (r1, p1), (r2, p2) in self.types["shifter"].stage_pairs[b][0 if a >> b & 1 else 1]:
                    uf.union((inst, r1, p1), (inst, r2, p2))
        return uf

    def permutation(self, controls: dict):
        """(perm, problems): perm[i] = OUT index reached from IN i (None if open / ambiguous), problems = sneak paths."""
        uf = self.simulate(controls)
        rin = [uf.find(p) for p in self.in_pins]
        rout = [uf.find(p) for p in self.out_pins]
        problems = []
        if len(set(rin)) != N:
            problems.append("IN lines shorted together")
        if len(set(rout)) != N:
            problems.append("OUT lines shorted together")
        where = {r: j for j, r in enumerate(rout)}
        perm = [where.get(r) for r in rin]
        if any(p is None for p in perm):
            problems.append("an IN line reaches no OUT line")
        forbidden = {uf.find(p) for p in self.j5}
        if forbidden & (set(rin) | set(rout)):
            problems.append("a signal line is connected to a control pin")
        return perm, problems


# ============================================================================= building units
def build_unit(types, rotors=("I", "II", "III"), reflector="B", *, facing4="front", swap=None, mutate=None) -> Unit:
    """rotors = (left, middle, right); reflector name.  facing4/swap/mutate are the negative-test hooks."""
    stack = [(i, k, (facing4 if i == "S4" else f), z) for i, k, f, z in STACK]
    pairs, leads, _ = derive_ribbons(stack)
    cards = {"S1": "shifter", "S2": "shifter", "S3": "shifter", "S4": "shifter",
             "W_L": f"rotor_{rotors[0]}", "W_M": f"rotor_{rotors[1]}", "W_R": f"rotor_{rotors[2]}",
             "U": f"reflector_{reflector}"}
    rib = [Ribbon(*sorted(p)) for p in pairs]
    lead_map = {}
    for inst, conn in leads:
        if (inst, conn) == LAYOUT_LEADS["IN"]:
            lead_map["IN"] = (inst, conn)
        elif (inst, conn) == LAYOUT_LEADS["OUT"]:
            lead_map["OUT"] = (inst, conn)
    if mutate:
        rib, cards = mutate(rib, cards)
    return Unit(types, cards, rib, lead_map)


def order_of(rotors, reflector):
    return (reflector, rotors[0], rotors[1], rotors[2])


def check_positions(unit: Unit, order: tuple, positions, oracle: Christopher | None, port_check=True, stop_first=False):
    """positions: iterable of (l, m, r, k).  Returns (n_checked, failures[list of str], oracle_mismatches)."""
    fails, mism, n = [], 0, 0
    for l, m, r, k in positions:
        n += 1
        rk = (r + k) % N
        exp = oracle.scrambler(order, l, m, rk) if oracle else scrambler_port(order, l, m, rk)
        if oracle and port_check and scrambler_port(order, l, m, rk) != exp:
            mism += 1
        perm, problems = unit.permutation(from_cores(l, m, r, k))
        if problems or perm != exp:
            fails.append(f"{order} l={l} m={m} r={r} k={k}: " + ("; ".join(problems) if problems else
                         "first difference at " + next(f"{C.L(i)}->{C.L(perm[i])} (want {C.L(exp[i])})"
                                                       for i in range(N) if perm[i] != exp[i])))
            if stop_first:
                break
    return n, fails, mism


# ============================================================================= card-level netlist checks
def check_w_card(nl: K.Netlist, name: str) -> list[str]:
    w = C.table("rotor", name)
    winv = C.inverse(w)
    errs = []
    nn = nl.node_net
    if set(nl.parts) != {"J1", "J2", "J3", "J4", "H1", "H2", "H3", "H4"}:
        errs.append(f"parts {sorted(nl.parts)}")
    for i in range(N):
        if nn.get(("J3", str(i + 1))) is None or set(nl.nets[nn[("J3", str(i + 1))]]) != {("J3", str(i + 1)), ("J1", str(w[i] + 1))}:
            errs.append(f"outbound J3.{i + 1} is not wired to J1.{w[i] + 1} only")
        if nn.get(("J4", str(i + 1))) is None or set(nl.nets[nn[("J4", str(i + 1))]]) != {("J4", str(i + 1)), ("J2", str(winv[i] + 1))}:
            errs.append(f"return J4.{i + 1} is not wired to J2.{winv[i] + 1} only (W inverse)")
    for ref in ("J1", "J2", "J3", "J4"):
        for p in range(27, 38):
            n = nn.get((ref, str(p)))
            if n is not None and len(nl.nets[n]) > 1:
                errs.append(f"{ref} pin {p} is connected (must float, 0003 5.2)")
    errs += check_chassis(nl)
    return errs


def check_u_card(nl: K.Netlist, name: str) -> list[str]:
    r = C.table("reflector", name)
    errs = []
    nn = nl.node_net
    if set(nl.parts) != {"J2", "J3", "H1", "H2", "H3", "H4"}:
        errs.append(f"parts {sorted(nl.parts)}")
    for i in range(N):
        n = nn.get(("J3", str(i + 1)))
        if n is None or set(nl.nets[n]) != {("J3", str(i + 1)), ("J2", str(r[i] + 1))}:
            errs.append(f"J3.{i + 1} is not wired to J2.{r[i] + 1} only")
        if r[r[i]] != i or r[i] == i:
            errs.append(f"reflector {name} is not a fixed-point-free involution at {i}")
    for ref in ("J2", "J3"):
        for p in range(27, 38):
            n = nn.get((ref, str(p)))
            if n is not None and len(nl.nets[n]) > 1:
                errs.append(f"{ref} pin {p} is connected (must float, 0003 5.2)")
    errs += check_chassis(nl)
    return errs


def check_chassis(nl: K.Netlist) -> list[str]:
    ch = nl.node_net.get(("H1", "1"))
    want = {(f"H{k}", "1") for k in range(1, 5)}
    if ch is None or set(nl.nets[ch]) != want:
        return ["CHASSIS net is not exactly H1-H4"]
    return []


# ============================================================================= main
def run_negative_tests(types, texts, work, oracle) -> list[str]:
    """Each mutation must make the composed unit differ from the scrambler of the intended wheel order."""
    rotors, refl = ("I", "II", "III"), "B"
    order = order_of(rotors, refl)
    rng = random.Random(7)
    pos = [(0, 0, 0, 0), (1, 0, 0, 0)] + [(rng.randrange(N), rng.randrange(N), rng.randrange(N), 0) for _ in range(120)]
    errs = []

    def rib_idx(rib, a, b):
        for i, rb in enumerate(rib):
            if {rb.a, rb.b} == {a, b}:
                return i
        raise KeyError((a, b))

    def swap_wires(rib, cards, a=("W_M", "J3"), b=("S2", "J3"), p=3, q=4):
        r = rib[rib_idx(rib, a, b)]
        r.pinmap = {p: q, q: p}
        return rib, cards

    def slot_swap(rib, cards):    # W_M's outbound-in and return-in ribbons plugged into each other's connector
        for rb in rib:
            for end in ("a", "b"):
                if getattr(rb, end) == ("W_M", "J3"):
                    setattr(rb, end, ("W_M", "J4"))
                elif getattr(rb, end) == ("W_M", "J4"):
                    setattr(rb, end, ("W_M", "J3"))
        return rib, cards

    def card_upside_down(rib, cards):    # W_M turned end for end: its J1<->J3 and J2<->J4 exchanged
        m = {"J1": "J3", "J3": "J1", "J2": "J4", "J4": "J2"}
        for rb in rib:
            for end in ("a", "b"):
                inst, conn = getattr(rb, end)
                if inst == "W_M":
                    setattr(rb, end, (inst, m[conn]))
        return rib, cards

    def other_rotor(rib, cards):
        cards = dict(cards, W_M="rotor_IV")
        return rib, cards

    def lm_swapped(rib, cards):
        cards = dict(cards, W_L=cards["W_M"], W_M=cards["W_L"])
        return rib, cards

    def reflector_c(rib, cards):
        return rib, dict(cards, U="reflector_C")

    def skipped_ribbon(rib, cards):    # S4's outbound ribbon plugged to the neighbour (U) is moved to W_L's slot
        i = rib_idx(rib, ("S4", "J2"), ("W_L", "J4"))
        rib[i].a = ("S4", "J4")
        return rib, cards

    tests = [
        ("two wires of the W_M-S2 outbound ribbon swapped (pins 3<->4)", dict(mutate=swap_wires)),
        ("W_M outbound-in and return-in ribbons plugged into each other's connector", dict(mutate=slot_swap)),
        ("W_M turned end for end (J1<->J3, J2<->J4: W and W^-1 exchanged)", dict(mutate=card_upside_down)),
        ("two wires of the S4-U outbound ribbon swapped (pins 1<->2)",
         dict(mutate=lambda r, c: swap_wires(r, c, ("S4", "J1"), ("U", "J3"), 1, 2))),
        ("S4 jumpers not turned around (a4 mounted like S1-S3, not reversed)", dict(facing4="back")),
        ("wrong card: W_M = rotor IV instead of II", dict(mutate=other_rotor)),
        ("wrong card: W_L and W_M interchanged", dict(mutate=lm_swapped)),
        ("wrong reflector: C instead of B", dict(mutate=reflector_c)),
        ("S4 ribbon moved to the wrong connector (S4.J2 jumper on S4.J4)", dict(mutate=skipped_ribbon)),
    ]
    for title, kw in tests:
        u = build_unit(types, rotors, refl, **kw)
        n, fails, _ = check_positions(u, order, pos, oracle, port_check=False)
        print(f"  negative test {'detected' if fails else 'MISSED  '}: {title} ({len(fails)}/{n} positions differ)")
        if not fails:
            errs.append(f"negative test missed: {title}")
    # ribbon table of a mounting with S4 facing back must differ from layout.md
    pairs, _, _ = derive_ribbons([(i, k, "back" if i == "S4" else f, z) for i, k, f, z in STACK])
    tbl = set(pairs) != LAYOUT_RIBBONS
    print(f"  negative test {'detected' if tbl else 'MISSED  '}: ribbon table with S4 facing back differs from layout.md")
    if not tbl:
        errs.append("negative test missed: S4 facing back vs ribbon table")

    # pin-1 alignment (the physical orientation of the sockets; the netlist composition cannot see it)
    ends = board_pin1_ends()
    want_bad = {"S4.J1 (pin 1 L) - U.J3 (pin 1 R)", "S4.J2 (pin 1 L) - W_L.J4 (pin 1 R)",
                "S4.J3 (pin 1 R) - W_L.J1 (pin 1 L)", "S4.J4 (pin 1 R) - U.J2 (pin 1 L)"}
    got = set(pin1_check(STACK, "std", ends, s4_variant=False))
    ok = got == want_bad
    print(f"  negative test {'detected' if ok else 'MISSED  '}: the standard shifter used as S4 "
          f"(pin 1 at the wrong end of {len(got)} jumpers: {', '.join(sorted(x.split(' (')[0] for x in got))})")
    if not ok:
        errs.append(f"negative test missed: standard shifter as S4 (got {sorted(got)})")
    s3 = pin1_check(STACK, "s4", ends | {("std", c): ends[("s4", c)] for c in CONNECTORS["shifter"]})
    print(f"  negative test {'detected' if s3 else 'MISSED  '}: the mirrored S4 board used for S1-S3 as well ({len(s3)} problems)")
    if not s3:
        errs.append("negative test missed: mirrored board in every shifter slot")
    flipped = ends | {("W", "J1"): "R" if ends[("W", "J1")] == "L" else "L"}
    wf = pin1_check(STACK, "s4", flipped)
    print(f"  negative test {'detected' if wf else 'MISSED  '}: W card with pin 1 of J1 at the other end ({len(wf)} problems)")
    if not wf:
        errs.append("negative test missed: W.J1 pin 1 flipped")

    # netlist mutations of single cards (a pin swap inside a card), composed into the unit
    def mutated(proj, ref, pa, pb, flags):
        t = dict(types)
        t[proj] = CardType(proj, K.Netlist(K.swap_pins(texts[proj], ref, pa, pb)))
        u = build_unit(t, rotors, refl)
        n, fails, _ = check_positions(u, order, pos[:30], oracle, port_check=False)
        card_errs = (check_w_card if proj.startswith("rotor") else check_u_card)(t[proj].nl, proj.split("_")[1])
        print(f"  negative test {'detected' if fails and card_errs else 'MISSED  '}: {proj} netlist, {ref} pins {pa}<->{pb} swapped "
              f"({len(fails)}/{n} positions differ, {len(card_errs)} card-check errors)")
        if not (fails and card_errs):
            errs.append(f"negative test missed: {proj} {ref} {pa}<->{pb}")
    mutated("rotor_II", "J1", "1", "2", None)
    mutated("rotor_II", "J2", "25", "26", None)
    mutated("rotor_III", "J3", "5", "6", None)
    mutated("reflector_B", "J2", "1", "2", None)
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    ap.add_argument("--samples", type=int, default=50, help="random positions per Enigma I wheel order")
    ap.add_argument("--exhaustive", default="B II IV V", help='wheel order checked at all 17,576 positions, "B L M R", or "all" for the 60 Enigma I orders')
    ap.add_argument("--quick", action="store_true", help="skip the exhaustive run and the Navy sweep")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("wiring-cards")
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    allerr: list[str] = []

    types = load_types(work)
    texts = {p: (work / f"{p}.net").read_text(encoding="utf8") for p in types if p != "shifter"}
    print(f"netlists: shifter {len(types['shifter'].nl.parts)} parts; "
          + ", ".join(f"{p} {len(t.nl.parts)}" for p, t in types.items() if p != "shifter"))

    # --- 1. the cards themselves
    for proj, kind, name in C.all_cards():
        e = (check_w_card if kind == "rotor" else check_u_card)(types[proj].nl, name)
        allerr += [f"{proj}: {x}" for x in e]
    print(f"card netlists vs data/wirings/enigma.txt (8 W cards, 2 U cards): {'OK' if not allerr else allerr[:3]}")

    # --- 2. the ribbons derived from the stack poses equal layout.md 3.4
    pairs, leads, lengths = derive_ribbons()
    e = []
    if set(pairs) != LAYOUT_RIBBONS or len(pairs) != 14:
        e.append(f"derived ribbons differ from layout.md: {sorted(map(sorted, set(pairs) ^ LAYOUT_RIBBONS))}")
    if set(leads) != set(LAYOUT_LEADS.values()):
        e.append(f"leads {leads}")
    if Counter(lengths) != Counter({25: 12, 50: 2}):
        e.append(f"ribbon lengths {dict(Counter(lengths))} != layout.md 3.4 (S4 variant: 25 x12, 50 x2)")
    print(f"ribbons derived from the card poses (S4 variant front, rest back): {len(pairs)} ribbons + {len(leads)} leads, "
          f"lengths {dict(Counter(lengths))} mm: {'= layout.md 3.4' if not e else e}")
    allerr += e

    # --- 2b. pin-1 alignment of all 14 jumpers + the IN / OUT leads, from the real board files
    ends = board_pin1_ends()
    pe = pin1_check(STACK, "s4", ends)
    print("pin-1 alignment from the board files (S1-S3 shifter_board, S4 shifter_board_s4, W rotor_I, U reflector_B):")
    for p1, p2 in sorted(map(sorted, pairs)):
        print(f"   {p1[0]}.{p1[1]} (pin 1 {ends[(_key(p1[0], 's4'), p1[1])]}) - {p2[0]}.{p2[1]} (pin 1 {ends[(_key(p2[0], 's4'), p2[1])]})")
    for lead in leads:
        print(f"   lead {lead[0]}.{lead[1]} (pin 1 {ends[(_key(lead[0], 's4'), lead[1])]})")
    nbad = len([x for x in pe if not x.startswith("lead")])
    print(f"   {14 - nbad} of 14 jumpers aligned, {len(leads) - len([x for x in pe if x.startswith('lead')])} of {len(leads)} leads "
          f"with their neighbour: {'PASS' if not pe else 'FAIL ' + str(pe)}")
    allerr += [f"pin 1: {x}" for x in pe]

    oracle = Christopher()

    # --- 3. all 60 Enigma I wheel orders with reflector B, random positions
    rng = random.Random(20261115)
    enigma_i = [("B",) + t for t in itertools.permutations(("I", "II", "III", "IV", "V"), 3)]
    tot = bad = mism = 0
    t1 = time.time()
    for order in enigma_i:
        unit = build_unit(types, order[1:], order[0])
        pos = [(0, 0, 0, 0), (25, 25, 25, 0)]
        pos += [(rng.randrange(N), rng.randrange(N), rng.randrange(N), rng.randrange(N) if rng.random() < 1 / 3 else 0)
                for _ in range(a.samples - 2)]
        n, fails, mm = check_positions(unit, order, pos, oracle)
        tot += n
        mism += mm
        bad += len(fails)
        allerr += fails[:2]
    print(f"Enigma I: {len(enigma_i)} wheel orders x {a.samples} positions = {tot} unit traces (26 lines each): "
          f"{'all equal to the scrambler' if not bad else f'{bad} FAIL'}; port vs christopher mismatches {mism} "
          f"[{time.time() - t1:.0f} s]")
    allerr += [f"port/christopher mismatch x{mism}"] if mism else []

    if not a.quick:
        # --- 4. every three-rotor order of I..VIII with reflectors B and C (Navy rotors VI-VIII, reflector C)
        tot = bad = 0
        t1 = time.time()
        orders = [(rf,) + t for rf in ("B", "C") for t in itertools.permutations(C.ROTORS, 3)]
        for order in orders:
            unit = build_unit(types, order[1:], order[0])
            pos = [(rng.randrange(N), rng.randrange(N), rng.randrange(N), rng.randrange(N)) for _ in range(4)]
            n, fails, _ = check_positions(unit, order, pos, oracle, port_check=False)
            tot += n
            bad += len(fails)
            allerr += fails[:2]
        print(f"all 3-rotor orders of I..VIII: {len(orders)} (reflectors B and C) x 4 positions = {tot} traces: "
              f"{'all equal' if not bad else f'{bad} FAIL'} [{time.time() - t1:.0f} s]")

        # --- 5. one wheel order exhaustively (--exhaustive all: every Enigma I order, about 12 minutes)
        targets = enigma_i if a.exhaustive.strip().lower() == "all" else [tuple(a.exhaustive.split())]
        for order in targets:
            unit = build_unit(types, order[1:], order[0])
            t1 = time.time()
            n, fails, mm = check_positions(unit, order, ((l, m, r, 0) for l in range(N) for m in range(N) for r in range(N)),
                                           oracle, port_check=True)
            print(f"exhaustive {' '.join(order)}: {n} positions x 26 lines: "
                  f"{'all equal to the scrambler' if not fails else f'{len(fails)} FAIL: {fails[:2]}'}; port mismatches {mm} "
                  f"[{time.time() - t1:.0f} s]")
            allerr += fails[:3]
            allerr += [f"port mismatch x{mm}"] if mm else []

    # --- 6. negative tests
    print("negative tests (each mutated unit must differ from the scrambler of the intended wheel order):")
    allerr += run_negative_tests(types, texts, work, oracle)

    print(f"[{time.time() - t0:.0f} s]  RESULT:", "PASS" if not allerr else f"FAIL {allerr[:5]}")
    return 1 if allerr else 0


if __name__ == "__main__":
    sys.exit(main())
