#!/usr/bin/env python3
"""Verify the position display board from its exported netlist (no knowledge of the generator).

    python verify_display.py            # ERC, structure, receiver analysis, all 26^3 POS codes, transitions,
                                        # end-to-end with the counter common board netlist (17,576 clocks), negative tests
    python verify_display.py --quick    # end-to-end over 2,000 clocks only

Model (netlist only):
  * relay G6S-2: coil 1 (+) / 12 (-); pole 1 = COM 4 / NC 3 / NO 5, pole 2 = COM 9 / NC 10 / NO 8 (as the counter boards);
    a coil is energised iff its pin-1 net is reachable from +24 V through closed contacts, forward diodes (anode 2 ->
    cathode 1) and switched-on P-MOSFET channels; reaching 0 V is a short;
  * input channel: the netlist is traced from each J1 pin through the divider resistor to a BSS138 gate, the BSS138 drain
    through a resistor to an AO3401A gate pulled up to +24 V, and that P-MOSFET's drain (pin functions G/S/D from the
    netlist).  The divider and gate voltages are computed from the resistor values: the N-MOSFET must be off at
    2.4 V and on at 18 V on the pin (decision 0003 2.2), the P-MOSFET Vgs must be <= -4.5 V and >= -12 V over the
    supply range 23.0-26.4 V.  A channel whose analysis fails is reported (no 'maybe' state is simulated);
  * a lamp DSdvv is lit iff the net on the far side of its series resistor is reachable from +24 V.  The letter of a lamp
    is its value 'Lamp X'; the reference number vv must be ord(X) - ord('A').
Reference: lamp of digit d lit <=> POS digit d (bits from J1 pins 1-5 / 6-10 / 11-15) == vv, exactly one per digit.
End-to-end: the counter common board netlist (counter-board/verify_counter.py System, Python port of relay.rs Counters as
reference) is clocked; its POS connector J4 pin n drives display J1 pin n (1:1 ribbon); the lit letters must equal (l, m, r).
"""
from __future__ import annotations

import argparse
import copy
import filecmp
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402  (counter-board: netlist parser, switch-level system, Counters reference)

N, BITS = 26, 5
COIL_P, COIL_N = "1", "12"
POLES = [("4", "3", "5"), ("9", "10", "8")]
V_NOM, V_MIN, V_MAX = 24.0, 23.0, 26.4         # board terminal range (decision 0003 6)
VTH_N = (0.8, 1.5)                               # BSS138 gate threshold range (datasheet)
QN_ON_MIN, QN_OFF_MAX = 2.5, 0.6                 # required gate voltage: >= Vth max + 1 V on, <= Vth min - 0.2 V off
QP_VGS_ON, QP_VGS_ABS = -4.5, -12.0              # AO3401A: Rds(on) specified at -4.5 V, |Vgs| <= 12 V
POS_BUDGET = 30.0e-3                             # counter POS pin (slave contact) assumed load limit (counter README 10)
MONITOR_MAX = 15.0e-3                            # a receiver per decision 0003 2.2 (the monitor on control connector 7-21)


def seq_follower_current(v: float) -> float:
    """Sequencer input follower on POS10/13/14 (seqdesign.py): 22k pull-down, 6.8k + LED (2 V), coil 2.88 kOhm behind a
    Schottky (0.3 V)."""
    return v / 22e3 + (v - 2.0) / 6.8e3 + (v - 0.3) / 2880.0


class Fault(Exception):
    pass


def pin_functions(text: str) -> dict[tuple[str, str], str]:
    tree = vc.parse(text)
    out = {}
    for n in vc.find_all(vc.find1(tree, "nets"), "net"):
        for x in vc.find_all(n, "node"):
            pf = vc.find1(x, "pinfunction")
            # KiCad 10 writes the pin function as "<name>_<number>" for these symbols (e.g. "G_1"): keep the name
            out[(vc.find1(x, "ref")[1], vc.find1(x, "pin")[1])] = re.sub(r"_\d+$", "", pf[1]) if pf else ""
    return out


class Board:
    """The display netlist + derived channel model."""

    def __init__(self, nl: vc.Netlist, pf: dict):
        self.nl, self.pf = nl, pf
        self.net = nl.node_net.get
        self.members: dict[str, list] = {}
        for node, n in nl.node_net.items():
            self.members.setdefault(n, []).append(node)
        self.gnd = self.net(("J2", "2"))
        self.plus = self.net(("Q1", "3"))       # board +24V behind the reverse-polarity MOSFET

    def pins_of(self, ref):
        return {p: n for (r, p), n in self.nl.node_net.items() if r == ref}

    def fn_pin(self, ref, fn):
        for (r, p), n in self.nl.node_net.items():
            if r == ref and self.pf.get((r, p)) == fn:
                return n
        return None

    def two_pin(self, ref):
        ps = self.pins_of(ref)
        return ps.get("1"), ps.get("2")

    def resistors_between(self, a, b):
        out = []
        for ref in self.nl.parts:
            if re.fullmatch(r"R[A-Z]*\d+", ref):
                x, y = self.two_pin(ref)
                if {x, y} == {a, b}:
                    out.append(kigen.parse_value(self.nl.parts[ref]["value"]))
        return out

    def resistors_at(self, n):
        out = []
        for ref in self.nl.parts:
            if re.fullmatch(r"R[A-Z]*\d+", ref):
                x, y = self.two_pin(ref)
                if n in (x, y):
                    out.append((ref, y if x == n else x))
        return out

    # ---------------------------------------------------------------- channels
    def channels(self) -> tuple[dict, list[str]]:
        """J1 pin -> (P-MOSFET drain net, analysis dict) traced through the netlist."""
        errs, ch = [], {}
        qns = [r for r in self.nl.parts if self.pf.get((r, "1")) == "G" and "BSS138" in self.nl.parts[r]["value"]]
        qps = [r for r in self.nl.parts if "AO3401A" in self.nl.parts[r]["value"]]
        gate_of_qn = {self.fn_pin(r, "G"): r for r in qns}
        gate_of_qp = {self.fn_pin(r, "G"): r for r in qps}
        for p in range(1, 16):
            pin = self.net(("J1", str(p)))
            if pin is None:
                errs.append(f"J1 pin {p}: no net")
                continue
            ups = [(ref, other) for ref, other in self.resistors_at(pin) if other in gate_of_qn]
            if len(ups) != 1:
                errs.append(f"J1 pin {p} ({pin}): {len(ups)} divider resistors to an input MOSFET gate (want 1)")
                continue
            gate = ups[0][1]
            qn = gate_of_qn[gate]
            extra = [ref for ref, other in self.resistors_at(pin) if other not in (gate, self.gnd)]
            extra += [ref for ref, other in self.resistors_at(gate) if other not in (pin, self.gnd)]
            if extra:
                errs.append(f"J1 pin {p}: unexpected resistors on the input / gate nets: {extra}")
            g_in = sum(1 / r for r in self.resistors_between(pin, gate))
            g_gnd = sum(1 / r for r in self.resistors_between(gate, self.gnd))
            g_pd = sum(1 / r for r in self.resistors_between(pin, self.gnd))
            if not g_gnd or not g_pd:
                errs.append(f"J1 pin {p}: divider lower resistor or pin pull-down missing")
                continue
            ratio = g_in / (g_in + g_gnd)
            g_eq = g_pd + g_in * g_gnd / (g_in + g_gnd)
            if self.fn_pin(qn, "S") != self.gnd:
                errs.append(f"{qn}: source not on 0 V")
            nd = self.fn_pin(qn, "D")
            qp_gate = [other for ref, other in self.resistors_at(nd) if other in gate_of_qp]
            if len(qp_gate) != 1:
                errs.append(f"{qn}: drain not tied by one resistor to a P-MOSFET gate")
                continue
            pg = qp_gate[0]
            qp = gate_of_qp[pg]
            if self.fn_pin(qp, "S") != self.plus:
                errs.append(f"{qp}: source not on the board +24V")
            r_up = self.resistors_between(pg, self.plus)
            r_dn = self.resistors_between(nd, pg)
            if len(r_up) != 1 or len(r_dn) != 1:
                errs.append(f"{qp}: gate pull-up / drive resistor missing")
                continue
            vgs = [-v * r_up[0] / (r_up[0] + r_dn[0]) for v in (V_MIN, V_MAX)]
            a = dict(ratio=ratio, req=1 / g_eq, off=2.4 * ratio, on=18.0 * ratio, gmax=V_MAX * ratio, vgs=vgs,
                     i_nom=V_NOM * g_eq, i_max=V_MAX * g_eq, qn=qn, qp=qp)
            if a["off"] > QN_OFF_MAX:
                errs.append(f"J1 pin {p}: gate {a['off']:.2f} V at 2.4 V input (must be <= {QN_OFF_MAX} V: off)")
            if a["on"] < QN_ON_MIN:
                errs.append(f"J1 pin {p}: gate {a['on']:.2f} V at 18 V input (must be >= {QN_ON_MIN} V: on)")
            if a["gmax"] > 20:
                errs.append(f"J1 pin {p}: gate over 20 V")
            if max(vgs) > QP_VGS_ON or min(vgs) < QP_VGS_ABS:
                errs.append(f"{qp}: Vgs {vgs[0]:.2f}..{vgs[1]:.2f} V outside [{QP_VGS_ABS}, {QP_VGS_ON}]")
            ch[p] = (self.fn_pin(qp, "D"), a)
        return ch, errs


# ----------------------------------------------------------------------------- structure (incl. decision 0003 items)
def structure_errors(b: Board) -> list[str]:
    nl, net = b.nl, b.net
    errs = []
    rel = [r for r in nl.parts if re.fullmatch(r"K\d+", r)]
    dio = [r for r in nl.parts if re.fullmatch(r"D\d+", r)]
    lamps = [r for r in nl.parts if re.fullmatch(r"DS\d+", r)]
    if len(rel) != 39 or len(dio) != 39 or len(lamps) != 78:
        errs.append(f"counts: {len(rel)} relays, {len(dio)} flyback diodes, {len(lamps)} lamps (want 39 / 39 / 78)")
    for r in rel:
        if "G6S-2" not in nl.parts[r]["footprint"]:
            errs.append(f"{r}: footprint {nl.parts[r]['footprint']}")
        if net((r, COIL_N)) != b.gnd:
            errs.append(f"{r}: coil pin 12 not on 0 V")
        cp = net((r, COIL_P))
        if not any(net((d, "1")) == cp and net((d, "2")) == b.gnd for d in dio):
            errs.append(f"{r}: no flyback diode across the coil")
    for ref in lamps:
        m = re.fullmatch(r"Lamp ([A-Z])", nl.parts[ref]["value"])
        if not m or ord(m[1]) - 65 != int(ref[-2:]):
            errs.append(f"{ref}: value {nl.parts[ref]['value']!r} does not name letter number {ref[-2:]}")
        if net((ref, "1")) != b.gnd:
            errs.append(f"{ref}: cathode not on 0 V")
        rs = [r for r, other in b.resistors_at(net((ref, "2")))]
        if len(rs) != 1:
            errs.append(f"{ref}: anode not fed through exactly one series resistor")
    # power input chain (0003 7.3) as on the counter boards
    vin, gnd, plus, vf = net(("J2", "1")), b.gnd, b.plus, net(("F1", "2"))
    chain = [(net(("F1", "1")) == vin, "PTC F1 pin 1 is not on the supply input"),
             (net(("Q1", "2")) == vf and vf not in (None, vin, plus), "MOSFET Q1 drain is not on the PTC output"),
             (plus not in (None, vin, vf), "MOSFET Q1 source is not a separate board +24V"),
             (net(("Q1", "1")) is not None and net(("Q1", "1")) == net(("DZ1", "2")) == net(("RG1", "1")), "Q1 gate is not on DZ1 anode + RG1"),
             (net(("DZ1", "1")) == plus, "gate zener DZ1 cathode is not on +24V"), (net(("RG1", "2")) == gnd, "RG1 not on 0 V"),
             (net(("TVS1", "1")) == gnd and net(("TVS1", "2")) == plus, "TVS1 is not between +24V and 0 V"),
             (net(("CB1", "1")) == plus and net(("CB1", "2")) == gnd, "CB1 is not between +24V and 0 V")]
    errs += [m for ok, m in chain if not ok]
    for (ref, pin), n in nl.node_net.items():
        if n == plus and re.fullmatch(r"J\d", ref):
            errs.append(f"{ref} pin {pin} is on +24V (no power on signal connectors)")
        if n == vin and ref not in ("J2", "F1"):
            errs.append(f"{ref}.{pin} is on the unfused supply input")
    for p in (19, 20, 21):
        if net(("J1", str(p))) != gnd:
            errs.append(f"J1 pin {p} is not 0 V")
    for p in (16, 17, 18, 22, 23, 24, 25):
        n = net(("J1", str(p)))
        if n is None or len(b.members.get(n, [])) != 1:
            errs.append(f"J1 reserved pin {p} is connected")
    if len({net(("J1", str(p))) for p in range(1, 16)}) != 15:
        errs.append("J1 pins 1-15 are not 15 distinct nets")
    ch = net(("H1", "1"))
    want = {(f"H{k}", "1") for k in range(1, 5)} | {("RCH1", "1"), ("CCH1", "1"), ("J1", "SH")}
    if ch is None or set(b.members.get(ch, [])) != want or ch == gnd:
        errs.append(f"CHASSIS members differ from H1-H4, RCH1, CCH1, D-sub shell: {sorted(set(b.members.get(ch, [])) ^ want)[:5]}")
    if net(("RCH1", "2")) != gnd or net(("CCH1", "2")) != gnd:
        errs.append("RCH1/CCH1 are not between CHASSIS and 0 V")
    return errs


# ----------------------------------------------------------------------------- switch-level evaluation
class Sim:
    def __init__(self, b: Board, ch: dict):
        nl = b.nl
        ids: dict[str, int] = {}
        nid = lambda n: ids.setdefault(n, len(ids))
        for n in sorted(set(nl.node_net.values())):
            nid(n)
        self.ids = ids
        self.gnd, self.plus = ids[b.gnd], ids[b.plus]
        self.relays = sorted(r for r in nl.parts if re.fullmatch(r"K\d+", r))
        self.coilp = [ids[nl.node_net[(r, COIL_P)]] for r in self.relays]
        self.adj = [[] for _ in ids]
        for k, r in enumerate(self.relays):
            for c, nc, no in POLES:
                if (r, c) not in nl.node_net:
                    continue
                c_, nc_, no_ = (ids[nl.node_net[(r, x)]] for x in (c, nc, no))
                self.adj[c_] += [(nc_, k, 0), (no_, k, 1)]
                self.adj[nc_].append((c_, k, 0))
                self.adj[no_].append((c_, k, 1))
        for d in nl.parts:
            if re.fullmatch(r"D\d+", d):
                self.adj[ids[nl.node_net[(d, "2")]]].append((ids[nl.node_net[(d, "1")]], -1, 0))
        self.chan = {p: ids[drain] for p, (drain, _a) in ch.items()}
        self.lamps = {}
        for ref in nl.parts:
            if re.fullmatch(r"DS\d+", ref):
                an = nl.node_net[(ref, "2")]
                rs = [r for r, other in b.resistors_at(an)]
                feed = [n for n in b.two_pin(rs[0]) if n != an][0]
                self.lamps[ref] = ids[feed]
        self.state = [0] * len(self.relays)

    def reach(self, high_pins):
        seen = bytearray(len(self.ids))
        starts = [self.plus] + [self.chan[p] for p in high_pins if p in self.chan]
        stack = []
        for s in starts:
            if not seen[s]:
                seen[s] = 1
                stack.append(s)
        st, adj, gnd = self.state, self.adj, self.gnd
        while stack:
            n = stack.pop()
            for m, r, need in adj[n]:
                if seen[m] or (r >= 0 and st[r] != need):
                    continue
                if m == gnd:
                    raise Fault("short: +24 V reaches 0 V through contacts / diodes")
                seen[m] = 1
                stack.append(m)
        return seen

    def settle(self, high_pins, maxit=20):
        for _ in range(maxit):
            seen = self.reach(high_pins)
            want = [seen[c] for c in self.coilp]
            if want == self.state:
                return seen
            self.state = want
        raise Fault("relays do not settle")

    def lit(self, seen):
        return sorted(r for r, n in self.lamps.items() if seen[n])


def pins_for(l, m, r):
    return [1 + 5 * d + bit for d, v in enumerate((l, m, r)) for bit in range(BITS) if v >> bit & 1]


def expect_lit(l, m, r):
    return [f"DS{d + 1}{v:02d}" for d, v in enumerate((l, m, r))]


def all_codes(sim: Sim, codes=None) -> int:
    n = 0
    for l in range(N):
        for m in range(N):
            for r in range(N):
                if codes is not None and (l, m, r) not in codes:
                    continue
                got = sim.lit(sim.settle(pins_for(l, m, r)))
                if got != expect_lit(l, m, r):
                    raise Fault(f"POS (l, m, r) = ({l}, {m}, {r}): lit {got}, expected {expect_lit(l, m, r)}")
                n += 1
    return n


def transitions(sim: Sim, trials=3000, seed=5) -> int:
    """Any relay state (relays of one bit switching at different moments): exactly one lamp per digit, never a short."""
    rng = random.Random(seed)
    for t in range(trials):
        sim.state = [rng.getrandbits(1) for _ in sim.state]
        seen = sim.reach([])
        lit = sim.lit(seen)
        for d in (1, 2, 3):
            k = [x for x in lit if x.startswith(f"DS{d}")]
            if len(k) != 1:
                raise Fault(f"random relay state {t}: digit {d} lamps lit {k}")
    return trials


def run_checks(nl: vc.Netlist, pf: dict, full=True) -> tuple[Board, dict, Sim, list[str]]:
    b = Board(nl, pf)
    errs = structure_errors(b)
    ch, e2 = b.channels()
    errs += e2
    sim = Sim(b, ch) if not e2 else None
    return b, ch, sim, errs


# ----------------------------------------------------------------------------- end-to-end with the counter netlist
def end_to_end(common: vc.Netlist, sim: Sim, clocks: int) -> int:
    drives = {}
    for name, pin in (("CK", "1"), ("NCK", "2"), ("LOAD", "3")):
        drives[name] = [("C", "J1", pin)]
    s = vc.System({"C": common}, [], [("C", "Q1", "3")], ("C", "J1", "6"), drives)
    ref = vc.Counters(0)
    pins = {p: s.nid("C", "J4", str(p)) for p in range(1, 16)}

    def check(seen_c, where):
        high = [p for p, n in pins.items() if seen_c[n]]
        got = sim.lit(sim.settle(high))
        want = expect_lit(ref.l, ref.m, ref.r)
        if got != want:
            raise Fault(f"{where}: counter (l, m, r) = ({ref.l}, {ref.m}, {ref.r}) -> display {got}, expected {want}")
    s.settle([]); s.settle(["LOAD"]); s.settle(["LOAD", "NCK"])
    check(s.settle(["NCK"]), "after HOME")
    for step in range(1, clocks + 1):
        ref.clock()
        check(s.settle(["CK"]), f"clock {step} (commit)")
        check(s.settle(["NCK"]), f"clock {step} (release)")
    return clocks


# ----------------------------------------------------------------------------- negative tests
def swap_node(nl, ref, pa, pb):
    n2 = copy.deepcopy(nl)
    n2.node_net[(ref, pa)], n2.node_net[(ref, pb)] = nl.node_net[(ref, pb)], nl.node_net[(ref, pa)]
    return n2


def move_node(nl, node, to_node):
    assert node in nl.node_net and to_node in nl.node_net, (node, to_node)
    n2 = copy.deepcopy(nl)
    n2.node_net[node] = n2.node_net[to_node]
    return n2


def set_value(nl, ref, value):
    assert ref in nl.parts, ref
    n2 = copy.deepcopy(nl)
    n2.parts[ref] = dict(n2.parts[ref], value=value)
    return n2


NEGATIVE = [
    ("tree: digit m bit 1 contact NC/NO swapped (K210 pins 3<->5)", lambda nl: swap_node(nl, "K210", "3", "5")),
    ("tree: digit r bit 4 pole 2 NC/NO swapped (K344 pins 10<->8)", lambda nl: swap_node(nl, "K344", "10", "8")),
    ("connector: POS0 / POS1 swapped (J1 pins 1<->2)", lambda nl: swap_node(nl, "J1", "1", "2")),
    ("connector: l bit 4 / m bit 0 swapped (J1 pins 5<->6)", lambda nl: swap_node(nl, "J1", "5", "6")),
    ("lamps: digit l lamps F and G exchanged (DS105 / DS106 anodes)", lambda nl: _swap_two(nl, ("DS105", "2"), ("DS106", "2"))),
    ("coil: a digit-l bit-3 relay on the bit-4 coil bus (K130.1 -> net of K140.1)", lambda nl: move_node(nl, ("K130", "1"), ("K140", "1"))),
    ("flyback diode of K221 reversed (D221 pins 1<->2)", lambda nl: swap_node(nl, "D221", "1", "2")),
    ("receiver: divider lower resistor RB12 18k -> 100k (gate too high when off)", lambda nl: set_value(nl, "RB12", "100k")),
    ("receiver: divider upper resistor RA20 100k -> 680k (gate too low at 18 V)", lambda nl: set_value(nl, "RA20", "680k")),
    ("high side: RD31 4.7k -> 47k (P-MOSFET Vgs too small)", lambda nl: set_value(nl, "RD31", "47k")),
    ("high side: QP23 source / drain swapped", lambda nl: _swap_fn(nl, "QP23", "S", "D")),
    ("power: reversed series P-MOSFET (Q1 pins 2<->3)", lambda nl: swap_node(nl, "Q1", "2", "3")),
    ("connector: +24 V on reserved pin J1.16", lambda nl: move_node(nl, ("J1", "16"), ("Q1", "3"))),
    ("chassis: mounting hole H2 on 0 V", lambda nl: move_node(nl, ("H2", "1"), ("J2", "2"))),
    ("lamp value: DS300 labelled 'Lamp B'", lambda nl: set_value(nl, "DS300", "Lamp B")),
    ("pull-down: RP14 removed from 0 V (pin side left without pull-down)", lambda nl: _set_net(nl, ("RP14", "2"), "__open__")),
]
_PF: dict = {}


def _swap_two(nl, a, b):
    n2 = copy.deepcopy(nl)
    n2.node_net[a], n2.node_net[b] = nl.node_net[b], nl.node_net[a]
    return n2


def _set_net(nl, node, name):
    assert node in nl.node_net, node
    n2 = copy.deepcopy(nl)
    n2.node_net[node] = name
    return n2


def _swap_fn(nl, ref, fa, fb):
    pa = [p for (r, p), fn in _PF.items() if r == ref and fn == fa][0]
    pb = [p for (r, p), fn in _PF.items() if r == ref and fn == fb][0]
    return swap_node(nl, ref, pa, pb)


def negative(nl, pf, mut) -> tuple[bool, str]:
    try:
        nl2 = mut(nl)
    except (KeyError, IndexError, AssertionError) as e:
        return False, f"mutation could not be applied: {e!r}"
    b, ch, sim, errs = run_checks(nl2, pf)
    if errs:
        return True, f"caught by the structure / receiver check: {errs[0][:110]}"
    try:
        all_codes(sim, codes={(l, m, r) for l in range(N) for m in (0, 13, 25) for r in (0, 7, 25)} |
                  {(v, v, v) for v in range(N)} | {(25 - v, v, (v * 7) % N) for v in range(N)})
        transitions(sim, 300)
    except Fault as e:
        return True, f"caught by the simulation: {str(e)[:110]}"
    return False, "NOT DETECTED"


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    work = kigen.build_dir("position-display")
    fails = []

    viol = kigen.run_erc(HERE / "position_display.kicad_sch", work / "erc.json")
    print(f"ERC (kicad-cli, all severities): {len(viol)} violations")
    for v in viol[:5]:
        print("  ", v["type"], v["description"])
    if viol:
        fails.append("ERC")

    netf = kigen.export_netlist(HERE / "position_display.kicad_sch", work / "position_display.net")
    text = netf.read_text(encoding="utf8")
    nl, pf = vc.Netlist(text), pin_functions(text)
    _PF.update(pf)
    print(f"netlist: {len(nl.parts)} parts, {len(set(nl.node_net.values()))} nets")
    b, ch, sim, errs = run_checks(nl, pf)
    if errs:
        print("FAIL structure / receivers:", errs[:6])
        return 1
    n_rel = sum(1 for r in nl.parts if re.fullmatch(r"K\d+", r))
    per_bus = {}
    for r in nl.parts:
        if re.fullmatch(r"K\d+", r):
            per_bus.setdefault(nl.node_net[(r, COIL_P)], []).append(r)
    bus_of = {p: drain for p, (drain, _x) in ch.items()}
    counts = [len(per_bus.get(bus_of[p], [])) for p in range(1, 16)]
    print(f"structure: {n_rel} relays (G6S-2; relays per POS line 1-15 = {counts}), "
          f"39 flyback diodes, 78 lamps with letter values A-Z x 3, power input chain, CHASSIS, J1 = counter J4 pin layout: OK")
    a0 = ch[1][1]
    print(f"receivers (from the netlist values): gate = {a0['ratio']:.3f} x pin voltage -> {a0['off']:.2f} V at 2.4 V (off, BSS138 "
          f"Vth >= {VTH_N[0]} V), {a0['on']:.2f} V at 18 V (on, Vth <= {VTH_N[1]} V), {a0['gmax']:.2f} V at 26.4 V; "
          f"P-MOSFET Vgs {a0['vgs'][0]:.2f} .. {a0['vgs'][1]:.2f} V; equivalent load {a0['req'] / 1e3:.1f} kOhm = "
          f"{a0['i_nom'] * 1e3:.2f} mA at 24 V, {a0['i_max'] * 1e3:.2f} mA at 26.4 V (all 15 channels identical: "
          f"{len({(round(x['ratio'], 4), round(x['req'])) for _, x in ch.values()}) == 1})")
    # POS pin budget (counter README 10: 30 mA per slave contact output)
    worst = 0.0
    for v in (V_NOM, V_MAX):
        disp = max(x["i_max"] if v == V_MAX else x["i_nom"] for _, x in ch.values())
        seq = seq_follower_current(v)
        tot3, tot = seq + MONITOR_MAX + disp, MONITOR_MAX + disp
        worst = max(worst, tot3)
        print(f"POS load per pin at {v:.1f} V: POS10/13/14 = sequencer follower {seq * 1e3:.2f} + monitor (0003 max) "
              f"{MONITOR_MAX * 1e3:.1f} + display {disp * 1e3:.2f} = {tot3 * 1e3:.2f} mA; other pins = {tot * 1e3:.2f} mA "
              f"(limit {POS_BUDGET * 1e3:.0f} mA)")
    if worst > POS_BUDGET:
        fails.append("POS load budget")

    t0 = time.time()
    try:
        n = all_codes(sim)
        print(f"all POS codes: {n} = 26^3 combinations (l, m, r): exactly the lamps of the three letters lit "
              f"(DS1vv / DS2vv / DS3vv), all 26 letters of every digit ({time.time() - t0:.0f} s)")
        info = []
        for v in range(26, 32):
            sim.state = [0] * len(sim.state)
            lit = [x for x in sim.lit(sim.settle(pins_for(v, 0, 0))) if x.startswith("DS1")]
            info.append(f"{v}->{','.join(chr(65 + int(x[-2:])) for x in lit)}")
        print("info: invalid codes 26..31 (never produced by the counter) light: " + " ".join(info))
        n = transitions(sim)
        print(f"transitions: {n} random relay states (bit relays switching at different moments): always exactly one lamp per "
              f"digit, never a short")
        t0 = time.time()
        clocks = 2000 if a.quick else vc.FULL_RUN
        cnet = kigen.export_netlist(HERE.parent / "counter-board" / "counter_common.kicad_sch", work / "counter_common.net")
        n = end_to_end(vc.Netlist(cnet.read_text(encoding="utf8")), sim, clocks)
        print(f"end-to-end: counter common board netlist (HOME, then {n} clocks, commit and release) -> J4 POS -> 1:1 ribbon -> "
              f"display J1 -> lit letters == relay.rs Counters (l, m, r) at every clock ({time.time() - t0:.0f} s)")
    except Fault as e:
        print("FAIL:", e)
        return 1

    print("negative tests (mutated netlists must fail):")
    ok = True
    for name, mut in NEGATIVE:
        det, msg = negative(nl, pf, mut)
        print(f"  {'detected' if det else 'MISSED  '}: {name} -> {msg}")
        ok &= det
    if not ok:
        fails.append("negative test")

    # determinism of the schematic generator
    import gen_display
    with tempfile.TemporaryDirectory() as td:
        gen_display.main(Path(td))
        names = [p.name for p in Path(td).iterdir() if p.suffix == ".kicad_sch"]
        same = all(filecmp.cmp(Path(td) / n_, HERE / n_, shallow=False) for n_ in names)
    print(f"determinism: regenerated schematics ({len(names)} files) identical to the repository files: {same}")
    if not same:
        fails.append("schematic not deterministic / stale")
    print("RESULT:", "PASS" if not fails else f"FAIL {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
