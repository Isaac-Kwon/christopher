#!/usr/bin/env python3
"""Verify the relay counter boards by simulating their exported netlists at the switch level.

    python verify_counter.py                # export both netlists with kicad-cli, then verify
    python verify_counter.py --netlists A.net B.net   # existing kicadsexpr netlists (common, unit)
    python verify_counter.py --quick        # shorter runs (first batch of offsets only, 1,400 asynchronous clocks)

Model (netlist only, no knowledge of the generator):
  * a relay coil (G6S-2: pin 1 = +, pin 12 = -) is energised iff its pin-1 net is reachable from the
    +24 V net (or an externally driven net) through closed contacts (undirected), forward-biased
    diodes (anode pin 2 -> cathode pin 1) and fixed wires (cable, dial); coils/resistors/LEDs do not conduct;
    reaching 0 V is a short (fault);
  * contacts: pole 1 = COM 4 / NC 3 / NO 5, pole 2 = COM 9 / NC 10 / NO 8, closed per relay state;
  * per input vector the relay states are iterated to a fixed point (all unstable relays flip together
    = synchronous mode, or one random unstable relay at a time = asynchronous/race mode).  Relay memory
    (self-holding paths) lives in the state vector, which is carried from one vector to the next.
Reference model = a direct Python port of crates/christopher-core/src/relay.rs Counters / Controls::from_cores
(self-checked against each other here the same way the Rust test counters_track_controls_over_full_run does).

Tests: structure; digit-level (unit board alone: every value 0..25 x advance/hold, carry, preset);
system-level (common board + three unit boards with offsets k = 0, 7, 25 over a full run of 17,576 clocks;
preset at HOME for all 26 offsets from random initial relay states); negative tests (mutated netlists must fail).
"""
from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = 26
BITS = 5
FULL_RUN = N ** 3  # 17,576

# G6S-2 pin functions (see gen_counter.py)
COIL_P, COIL_N = "1", "12"
POLES = [("4", "3", "5"), ("9", "10", "8")]  # (COM, NC, NO)


# ----------------------------------------------------------------------------- reference model (relay.rs port)
def from_cores(l: int, m: int, r: int, k: int):
    """Controls::from_cores -> (a1, a2, a3, a4_l)."""
    rk = (r + k) % N
    return (rk, (m - rk) % N, (l - m) % N, l)


class Counters:
    """Port of relay.rs Counters: increment-or-hold counter bank of one unit (crib offset k)."""

    def __init__(self, k: int):
        self.l = self.m = self.r = 0
        self.k = k
        self.c = list(from_cores(0, 0, 0, k))  # a1 a2 a3 a4

    def clock(self):
        l_wraps = self.l == 25
        m_adv = l_wraps
        m_wraps = m_adv and self.m == 25
        c = self.c
        c[3] = (c[3] + 1) % N
        if not l_wraps:
            c[2] = (c[2] + 1) % N
        if m_adv and not m_wraps:
            c[1] = (c[1] + 1) % N
        if m_wraps:
            c[0] = (c[0] + 1) % N
        self.l = (self.l + 1) % N
        if m_adv:
            self.m = (self.m + 1) % N
        if m_wraps:
            self.r = (self.r + 1) % N

    def controls(self):
        return tuple(self.c)


def selfcheck_reference():
    for k in range(N):
        c = Counters(k)
        for step in range(FULL_RUN + 1):
            assert c.controls() == from_cores(c.l, c.m, c.r, k), (k, step)
            c.clock()
    # one full run returns to home
    c = Counters(7)
    for _ in range(FULL_RUN):
        c.clock()
    assert c.controls() == Counters(7).controls() and (c.l, c.m, c.r) == (0, 0, 0)


# ----------------------------------------------------------------------------- netlist parsing
def tokenize(text: str):
    for m in re.finditer(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+', text):
        yield m.group(0) if m.group(1) is None else ("s", m.group(1))


def parse(text: str):
    stack: list[list] = [[]]
    for tok in tokenize(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            lst = stack.pop()
            stack[-1].append(lst)
        else:
            stack[-1].append(tok[1] if isinstance(tok, tuple) else tok)
    return stack[0][0]


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def find1(node, key):
    r = find_all(node, key)
    return r[0] if r else None


class Netlist:
    def __init__(self, text: str):
        tree = parse(text)
        self.parts = {}
        for c in find_all(find1(tree, "components"), "comp"):
            ref = find1(c, "ref")[1]
            self.parts[ref] = dict(value=(find1(c, "value") or [0, ""])[1],
                                   footprint=(find1(c, "footprint") or [0, ""])[1])
        self.node_net: dict[tuple[str, str], str] = {}
        for n in find_all(find1(tree, "nets"), "net"):
            name = find1(n, "name")[1]
            for x in find_all(n, "node"):
                self.node_net[(find1(x, "ref")[1], find1(x, "pin")[1])] = name


class Fault(Exception):
    pass


# ----------------------------------------------------------------------------- switch-level system
class System:
    """Several boards joined by fixed wires; relay states live in self.state."""

    def __init__(self, boards: dict[str, Netlist], wires: list, plus24, gnd, drives: dict[str, tuple]):
        """boards: tag -> Netlist; wires: [((tag, ref, pin), (tag, ref, pin))]; plus24/gnd: (tag, ref, pin);
        drives: name -> (tag, ref, pin) of externally driven nets."""
        self.boards = boards
        parent: dict = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def key(tag, ref, pin):
            return (tag, boards[tag].node_net[(ref, pin)])

        for a, b in wires:
            parent[find(key(*a))] = find(key(*b))
        ids: dict = {}

        def nid(tag, ref, pin):
            r = find(key(tag, ref, pin))
            return ids.setdefault(r, len(ids))

        self.nid = nid
        self.relays: list[tuple[str, str]] = []
        self.relay_idx: dict[tuple[str, str], int] = {}
        contacts, coilp, coiln, diodes = [], [], [], []
        for tag, nl in boards.items():
            for ref in sorted(nl.parts):
                if re.fullmatch(r"K\d+", ref):
                    self.relay_idx[(tag, ref)] = len(self.relays)
                    self.relays.append((tag, ref))
                    contacts.append([(nid(tag, ref, c), nid(tag, ref, nc), nid(tag, ref, no)) for c, nc, no in POLES])
                    coilp.append(nid(tag, ref, COIL_P))
                    coiln.append(nid(tag, ref, COIL_N))
                elif re.fullmatch(r"D\d+", ref):
                    diodes.append((nid(tag, ref, "2"), nid(tag, ref, "1")))  # anode -> cathode
        for tag, nl in boards.items():           # make sure every node has an id (observation points)
            for (ref, pin) in nl.node_net:
                nid(tag, ref, pin)
        self.nn = len(ids)
        self.coilp, self.coiln = coilp, coiln
        self.gnd = nid(*gnd)
        self.plus = nid(*plus24)
        self.drive = {name: nid(*p) for name, p in drives.items()}
        # adjacency: (neighbour, relay or -1, required state)
        self.adj = [[] for _ in range(self.nn)]
        for r, poles in enumerate(contacts):
            for c, nc, no in poles:
                self.adj[c].append((nc, r, 0)); self.adj[nc].append((c, r, 0))
                self.adj[c].append((no, r, 1)); self.adj[no].append((c, r, 1))
        for a, k in diodes:
            self.adj[a].append((k, -2, 0))      # directed: only from a (see reach)
        self.state = [0] * len(self.relays)

    def reach(self, drive_nets):
        seen = bytearray(self.nn)
        stack = []
        for s in [self.plus] + drive_nets:
            if not seen[s]:
                seen[s] = 1
                stack.append(s)
        gnd, state, adj = self.gnd, self.state, self.adj
        short = False
        while stack:
            n = stack.pop()
            for m, r, need in adj[n]:
                if seen[m]:
                    continue
                if r >= 0 and state[r] != need:
                    continue
                # r == -2: directed diode edge stored only at the anode, r == -1: fixed wire (unused)
                if m == gnd:
                    short = True
                    continue
                seen[m] = 1
                stack.append(m)
        return seen, short

    def settle(self, high: list[str], rng: random.Random | None = None, maxit: int = 400):
        """Apply the externally driven nets named in `high`, iterate to a fixed point. Returns the reach map."""
        dn = [self.drive[h] for h in high]
        state, coilp = self.state, self.coilp
        for _ in range(maxit):
            seen, short = self.reach(dn)
            if short:
                raise Fault("short circuit +24 V -> 0 V through contacts/diodes")
            want = [seen[c] for c in coilp]
            if want == state:
                return seen
            if rng is None:
                state[:] = want
            else:
                un = [i for i in range(len(state)) if want[i] != state[i]]
                i = rng.choice(un)
                state[i] = want[i]
        raise Fault("no fixed point (oscillation)")


def digit_value(sys_: System, tag: str, d: int, kind: int = 0) -> int:
    """Value held by digit d of a board: kind 0 = slave relays K{d}0i, kind 1 = masters K{d}1i (should be NOT value)."""
    v = 0
    for i in range(BITS):
        v |= sys_.state[sys_.relay_idx[(tag, f"K{d}{kind}{i}")]] << i
    return v


# ----------------------------------------------------------------------------- common/unit assembly
CTRL_REFS = {"a1": "J3", "a2": "J4", "a3": "J5", "a4": "J6"}
CABLE_PINS = range(1, 26)


def build_system(common: Netlist, unit: Netlist, ks: list[int]) -> System:
    boards = {"C": common}
    wires = []
    for u, k in enumerate(ks):
        t = f"U{u}"
        boards[t] = unit
        for p in CABLE_PINS:
            if ("J2", str(p)) in common.node_net and ("J1", str(p)) in unit.node_net:
                wires.append((("C", "J2", str(p)), (t, "J1", str(p))))
        for b in range(BITS):                       # offset dial: COM joined to the 1-bit code pins
            if k >> b & 1:
                wires.append(((t, "J2", "1"), (t, "J2", str(2 + b))))          # a1 = k
            if ((N - k) % N) >> b & 1:
                wires.append(((t, "J2", "1"), (t, "J2", str(7 + b))))          # a2 = -k
    drives = {"CK": ("C", "J1", "1"), "NCK": ("C", "J1", "2"), "LOAD": ("C", "J1", "3")}
    s = System(boards, wires, ("C", "J1", "4"), ("C", "J1", "6"), drives)
    s.ks = ks
    return s


def structure_errors(nl: Netlist, nrelays: int, ndiodes: int, tag: str) -> list[str]:
    errs = []
    rel = [r for r in nl.parts if re.fullmatch(r"K\d+", r)]
    dio = [r for r in nl.parts if re.fullmatch(r"D\d+", r)]
    if len(rel) != nrelays:
        errs.append(f"{tag}: expected {nrelays} relays, found {len(rel)}")
    if len(dio) != ndiodes:
        errs.append(f"{tag}: expected {ndiodes} diodes, found {len(dio)}")
    gnd = nl.node_net.get(("J1", "6" if tag == "common" else "19"))
    for r in rel:
        if "G6S-2" not in nl.parts[r]["footprint"]:
            errs.append(f"{r}: footprint {nl.parts[r]['footprint']}")
        if nl.node_net.get((r, COIL_N)) != gnd:
            errs.append(f"{r}: coil pin 12 not on 0 V")
    return errs


# ----------------------------------------------------------------------------- system run
class Mismatch(Exception):
    pass


def observe(s: System, seen, u: int):
    """CTRL values on the four shifter-board connectors of unit u, from the settled reach map."""
    out = {}
    for a, j in CTRL_REFS.items():
        v = 0
        for b in range(BITS):
            if seen[s.nid(f"U{u}", j, str(b + 1))]:
                v |= 1 << b
        out[a] = v
    return (out["a1"], out["a2"], out["a3"], out["a4"])


def master_of(v: int) -> int:
    """Master relays mirror NOT slave; at value 25 the wrap relay forces them all to 0 (the wrap marker)."""
    return 0 if v == 25 else ~v & 31


def check_state(s: System, seen, refs: list[Counters], where: str, internal: bool = True):
    for u, c in enumerate(refs):
        got = observe(s, seen, u)
        if got != c.controls():
            raise Mismatch(f"{where}: unit {u} (k={c.k}) CTRL (a1,a2,a3,a4) = {got}, expected {c.controls()} (l={c.l} m={c.m} r={c.r})")
    if not internal:
        return
    c0 = refs[0]
    # internal digit values: common pL = l, pM = m, a3; unit a2, a1; masters = NOT slaves everywhere
    exp_common = {1: c0.l, 2: c0.m, 3: c0.c[2]}
    for d, v in exp_common.items():
        sv, mv = digit_value(s, "C", d, 0), digit_value(s, "C", d, 1)
        if sv != v or mv != master_of(v):
            raise Mismatch(f"{where}: common digit {d} slave={sv} master={mv}, expected {v} / {master_of(v)}")
    for u, c in enumerate(refs):
        for d, v in ((1, c.c[1]), (2, c.c[0])):
            sv, mv = digit_value(s, f"U{u}", d, 0), digit_value(s, f"U{u}", d, 1)
            if sv != v or mv != master_of(v):
                raise Mismatch(f"{where}: unit {u} digit {d} slave={sv} master={mv}, expected {v} / {master_of(v)}")


def randomise(s: System, rng: random.Random):
    s.state[:] = [rng.getrandbits(1) for _ in s.state]


def do_home(s: System, rng: random.Random | None):
    """HOME preset sequence: clock rails off (clear), LOAD on (dial -> slaves), idle rails back, LOAD off."""
    s.settle([], rng)
    s.settle(["LOAD"], rng)
    s.settle(["LOAD", "NCK"], rng)
    return s.settle(["NCK"], rng)


def do_clock(s: System, rng: random.Random | None):
    seen1 = s.settle(["CK"], rng)
    seen2 = s.settle(["NCK"], rng)
    return seen1, seen2


def run_system(common, unit, ks, clocks: int, rng_mode: random.Random | None, seed_state: int | None,
               label: str, progress: bool = False) -> int:
    s = build_system(common, unit, ks)
    if seed_state is not None:
        randomise(s, random.Random(seed_state))
    refs = [Counters(k) for k in ks]
    seen = do_home(s, rng_mode)
    check_state(s, seen, refs, f"{label} after HOME")
    t0 = time.time()
    for step in range(1, clocks + 1):
        for c in refs:
            c.clock()
        seen1, seen2 = do_clock(s, rng_mode)
        check_state(s, seen1, refs, f"{label} clock {step} (commit)", internal=False)
        check_state(s, seen2, refs, f"{label} clock {step} (release)")
        if progress and step % 2000 == 0:
            print(f"    {label}: {step} clocks OK ({time.time() - t0:.0f} s)", flush=True)
    return clocks


def home_variants_test(common, unit, trials: int = 150) -> int:
    """Other legal HOME sequences from random relay states: with LOAD/NCK overlap and direct (zero-gap) hand-over,
    entered from the running state (NCK) or from power-up (everything off)."""
    rng = random.Random(21)
    seqs = [[["NCK"], ["LOAD"], ["NCK"]], [["NCK"], ["LOAD"], ["LOAD", "NCK"], ["NCK"]], [[], ["LOAD"], ["NCK"]],
            [["NCK"], [], ["LOAD"], ["LOAD", "NCK"], ["NCK"]]]
    for trial in range(trials):
        ks = [rng.randrange(N) for _ in range(3)]
        s = build_system(common, unit, ks)
        randomise(s, rng)
        seq = seqs[trial % len(seqs)]
        for h in seq:
            seen = s.settle(h)
        check_state(s, seen, [Counters(k) for k in ks], f"home variant {trial % len(seqs)} k={ks}")
    return trials


def preset_test(common, unit, rng_seed: int, ks_all=range(N), mode_rng=None) -> int:
    """HOME preset for every offset (batches of 3 units), from random initial relay states, then a few clocks."""
    ks_all = list(ks_all)
    n = 0
    rng = random.Random(rng_seed)
    for i in range(0, len(ks_all), 3):
        batch = ks_all[i:i + 3]
        batch += [batch[-1]] * (3 - len(batch))
        for trial in range(2):
            s = build_system(common, unit, batch)
            randomise(s, rng)
            refs = [Counters(k) for k in batch]
            seen = do_home(s, mode_rng)
            check_state(s, seen, refs, f"preset k={batch} trial {trial}")
            # a few clocks, then HOME again from a non-home state
            for step in range(rng.randrange(1, 60)):
                for c in refs:
                    c.clock()
                seen1, seen2 = do_clock(s, mode_rng)
                check_state(s, seen2, refs, f"preset k={batch} +{step + 1}")
            refs = [Counters(k) for k in batch]
            seen = do_home(s, mode_rng)
            check_state(s, seen, refs, f"re-preset k={batch}")
            n += 1
    return n


# ----------------------------------------------------------------------------- digit-level (unit board alone)
def unit_only_system(unit: Netlist, v1: int, v2: int) -> System:
    wires = []
    for b in range(BITS):
        if v1 >> b & 1:
            wires.append((("U", "J2", "1"), ("U", "J2", str(2 + b))))
        if v2 >> b & 1:
            wires.append((("U", "J2", "1"), ("U", "J2", str(7 + b))))
    drives = {"A2B": ("U", "J1", "1"), "Z2B": ("U", "J1", "2"), "A1B": ("U", "J1", "3"), "Z1B": ("U", "J1", "4"),
              "LOAD": ("U", "J1", "15")}
    return System({"U": unit}, wires, ("U", "J1", "16"), ("U", "J1", "19"), drives)


def digit_test(unit: Netlist, seed: int) -> int:
    """a2 / a1 digit alone: preset every value, then random advance/hold sequences incl. wraps; carry rail checked."""
    rng = random.Random(seed)
    cnt = 0
    for v in range(N):
        v2 = (v * 7 + 3) % N                      # a2 digit gets a different preset than a1
        s = unit_only_system(unit, v, v2)
        randomise(s, rng)
        carry = {2: s.nid("U", "K114", "3"), 1: s.nid("U", "K214", "3")}   # AOUT = NC contact node of master bit 4
        # HOME: both rails low, LOAD, idle rails + LOAD, LOAD off
        s.settle([]); s.settle(["LOAD"]); s.settle(["LOAD", "Z2B", "Z1B"])
        seen = s.settle(["Z2B", "Z1B"])
        vals = {2: v2, 1: v}
        def rd(seen_):
            q2 = sum(1 << b for b in range(BITS) if seen_[s.nid("U", "J4", str(b + 1))])
            q1 = sum(1 << b for b in range(BITS) if seen_[s.nid("U", "J3", str(b + 1))])
            return {2: q2, 1: q1}
        if rd(seen) != vals:
            raise Mismatch(f"digit preset v={v}: got {rd(seen)}, expected {vals}")
        for step in range(60):
            adv = {2: rng.random() < 0.6, 1: rng.random() < 0.6}
            high = []
            for d, a in adv.items():
                high.append(f"A{d}B" if a else f"Z{d}B")
            seen = s.settle(high)
            exp = {d: (vals[d] + 1) % N if adv[d] else vals[d] for d in vals}
            if rd(seen) != exp:
                raise Mismatch(f"digit step: values {vals} adv {adv}: got {rd(seen)}, expected {exp}")
            for d in vals:
                want_carry = adv[d] and vals[d] == 25
                if bool(seen[carry[d]]) != want_carry:
                    raise Mismatch(f"digit carry rail d={d} value {vals[d]} adv {adv[d]}: {bool(seen[carry[d]])}")
            vals = exp
            seen = s.settle(["Z2B", "Z1B"])
            if rd(seen) != vals:
                raise Mismatch(f"digit release: got {rd(seen)}, expected {vals}")
            cnt += 1
    return cnt


# ----------------------------------------------------------------------------- negative tests
def swap_pins(text: str, ref: str, pa: str, pb: str) -> str:
    pat = lambda p: re.compile(r'(\(ref "%s"\)\s*\(pin )"%s"' % (re.escape(ref), re.escape(p)))
    t, na = pat(pa).subn(r'\1"@@A@@"', text)
    t, nb = pat(pb).subn(r'\1"@@B@@"', t)
    assert na == 1 and nb == 1, (ref, pa, pb, na, nb)
    return t.replace('"@@A@@"', f'"{pb}"').replace('"@@B@@"', f'"{pa}"')


NEGATIVE = [
    # (description, board, ref, pin a, pin b)
    ("common bus: swap L0/L1 lines (J2 pins 5<->6)", "common", "J2", "5", "6"),
    ("common pL slave bit 1: swap NC/NO of Q pole (K101 pins 3<->5)", "common", "K101", "3", "5"),
    ("common pL master bit 2: swap NC/NO of carry pole (K112 pins 3<->5)", "common", "K112", "3", "5"),
    ("common pL wrap relay: swap NC/NO (K120 pins 3<->5)", "common", "K120", "3", "5"),
    ("common a3 slave hold diode bit 2 reversed (D315 pins 1<->2)", "common", "D315", "1", "2"),
    ("common a3 hold-rail OR diode from LC reversed (D802 pins 1<->2)", "common", "D802", "1", "2"),
    ("common bus: swap A1B/Z1B (J2 pins 3<->4)", "common", "J2", "3", "4"),
    ("common bus: swap A2B/A1B (J2 pins 1<->3)", "common", "J2", "1", "3"),
    ("unit dial: swap a2 preset bits 0/2 (J2 pins 7<->9)", "unit", "J2", "7", "9"),
    ("unit a1 master bit 2: swap NC/NO (K212 pins 3<->5)", "unit", "K212", "3", "5"),
    ("unit a1 wrap relay: swap NC/NO (K220 pins 3<->5)", "unit", "K220", "3", "5"),
    ("unit buffer a4 bit 3: coil reversed (K903 pins 1<->12)", "unit", "K903", "1", "12"),
    ("unit CTRL a2 connector: swap CTRL0/CTRL1 (J4 pins 1<->2)", "unit", "J4", "1", "2"),
    ("unit a2 slave bit 3: self-hold diode reversed (D122 pins 1<->2)", "unit", "D122", "1", "2"),
]


def negative_test(name, board, ref, pa, pb, ctext, utext) -> tuple[bool, str]:
    try:
        if board == "common":
            common, unit = Netlist(swap_pins(ctext, ref, pa, pb)), Netlist(utext)
        else:
            common, unit = Netlist(ctext), Netlist(swap_pins(utext, ref, pa, pb))
    except AssertionError as e:
        return False, f"mutation could not be applied: {e}"
    stages = []
    try:
        stages.append("preset"); preset_test(common, unit, 5, ks_all=[0, 7, 25, 13, 1, 12])
        stages.append("digit"); digit_test(unit, 3)
        stages.append("run"); run_system(common, unit, [0, 7, 25], 720, None, 11, "neg")
    except (Mismatch, Fault) as e:
        return True, f"caught in {stages[-1]} stage: {str(e)[:110]}"
    except KeyError as e:
        return True, f"caught structurally ({e!r})"
    return False, "NOT DETECTED"


# ----------------------------------------------------------------------------- main
def find_cli() -> str:
    c = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if c:
        return c
    for p in ("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "/usr/bin/kicad-cli"):
        if Path(p).exists():
            return p
    raise SystemExit("kicad-cli not found (set KICAD_CLI)")


def export_netlist(project: str, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{project}.net"
    subprocess.run([find_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out),
                    str(HERE / f"{project}.kicad_sch")], check=True, stdout=subprocess.DEVNULL)
    return out


def default_build_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "christopher" / "counter-board"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlists", nargs=2, metavar=("COMMON", "UNIT"))
    ap.add_argument("--build-dir")
    ap.add_argument("--quick", action="store_true", help="only offsets 0/7/25 synchronously and 1,400 asynchronous clocks")
    args = ap.parse_args()
    if args.netlists:
        cpath, upath = map(Path, args.netlists)
    else:
        bd = Path(args.build_dir) if args.build_dir else default_build_dir()
        cpath, upath = export_netlist("counter_common", bd), export_netlist("counter_unit", bd)
    ctext, utext = cpath.read_text(encoding="utf8"), upath.read_text(encoding="utf8")
    common, unit = Netlist(ctext), Netlist(utext)
    print(f"netlists: common {len(common.parts)} parts, unit {len(unit.parts)} parts")

    selfcheck_reference()
    print("reference: Python port of relay.rs Counters == Controls::from_cores for all 26 k over 17,577 steps; "
          "a full run returns to HOME")

    errs = structure_errors(common, 33, 3 * 64 + 4 + 0, "common") + structure_errors(unit, 32, 2 * 64 + 10, "unit")
    n_rel = lambda nl: sum(1 for r in nl.parts if re.fullmatch(r"K\d+", r))
    n_dio = lambda nl: sum(1 for r in nl.parts if re.fullmatch(r"D\d+", r))
    print(f"structure: common {n_rel(common)} relays / {n_dio(common)} diodes, unit {n_rel(unit)} relays / {n_dio(unit)} diodes")
    if errs:
        print("FAIL structure:", errs[:5])
        return 1

    t0 = time.time()
    try:
        n = digit_test(unit, 1)
        print(f"digit-level (unit board alone): 26 presets x 60 random advance/hold steps = {n} steps, values + carry rail OK "
              f"({time.time() - t0:.0f} s)")
        t0 = time.time()
        n = home_variants_test(common, unit)
        print(f"HOME sequence variants (direct hand-over, LOAD/NCK overlap, from power-up): {n} runs OK")
        n = preset_test(common, unit, 2)
        print(f"preset at HOME: all 26 offsets (random initial relay states, re-preset after counting): {n} runs OK "
              f"({time.time() - t0:.0f} s)")
        t0 = time.time()
        batches = [[0, 7, 25]]
        if not args.quick:
            rest = [k for k in range(N) if k not in (0, 7, 25)]
            batches += [rest[i:i + 3] + [rest[-1]] * (3 - len(rest[i:i + 3])) for i in range(0, len(rest), 3)]
        total = 0
        for ks in batches:
            total += run_system(common, unit, ks, FULL_RUN, None, None, f"full k={ks}")
        print(f"full runs, synchronous settle: common + 3 units per batch, offsets k = {sorted({k for b in batches for k in b})}: "
              f"{len(batches)} runs x {FULL_RUN} clocks x 3 units; CTRL a1..a4 of every unit == Counters at every clock "
              f"(commit and release), internal digits == l, m, a3, a2, a1 ({time.time() - t0:.0f} s)")
        # asynchronous (random order) settle: races must not change the result
        t0 = time.time()
        rng = random.Random(7)
        n_async = 1400 if args.quick else FULL_RUN
        run_system(common, unit, [3, 13, 18], n_async, rng, 99, "async")
        print(f"asynchronous settle (one random relay at a time, random initial relay states), k=[3, 13, 18]: {n_async} clocks OK "
              f"({time.time() - t0:.0f} s)")
        n_async2 = preset_test(common, unit, 4, ks_all=range(N), mode_rng=random.Random(9))
        print(f"asynchronous settle: preset for all 26 offsets: {n_async2} runs OK")
    except (Mismatch, Fault) as e:
        print("FAIL:", e)
        return 1

    # documented limitation: the clock pair must be break-before-make (one changeover contact), never both high
    s = build_system(common, unit, [0, 7, 25])
    refs = [Counters(k) for k in s.ks]
    do_home(s, None)
    overlap_fails = False
    try:
        for step in range(1, 200):
            for c in refs:
                c.clock()
            s.settle(["CK", "NCK"]); s.settle(["CK"]); s.settle(["CK", "NCK"]); seen = s.settle(["NCK"])
            check_state(s, seen, refs, f"overlap {step}")
    except Mismatch:
        overlap_fails = True
    print(f"info: CK and NCK both high (make-before-make overlap) {'miscounts, as expected: the clock relay must be break-before-make' if overlap_fails else 'was tolerated'}")

    print("negative tests (mutated netlists must fail):")
    ok = True
    for name, board, ref, pa, pb in NEGATIVE:
        det, msg = negative_test(name, board, ref, pa, pb, ctext, utext)
        print(f"  {'detected' if det else 'MISSED  '}: {name} -> {msg}")
        ok &= det
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
