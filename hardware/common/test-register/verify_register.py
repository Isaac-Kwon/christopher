#!/usr/bin/env python3
"""Verify the test register board by simulating its exported netlist at switch level.

    python verify_register.py                 # export the netlist with kicad-cli, then verify
    python verify_register.py --netlist X.net # verify an existing KiCad s-expression netlist
    python verify_register.py --quick         # smaller case set (what the negative tests use)

What is simulated (only the netlist is used: net membership of component pins):
  * G6S-2 relays: coil = pins 1 (+) / 12 (-); pole 1 = COM 4 / NC 3 / NO 5, pole 2 = COM 9 / NC 10 / NO 8.
  * diodes (D*, DZ*): conduct anode (pin 2) -> cathode (pin 1) only;  AO3400A (Q*): drain 3 - source 2 closed when the
    gate net is pulled up through a resistor from a driven-high net;  PTC fuses F1/F2: closed.
  * a net group is HIGH when it is reached from the 24 V source (J4.1) or a driven bank signal, through closed contacts and
    forward diodes; LOW when it is joined to 0 V.  HIGH and LOW at once = short (reported).  A relay coil is energised
    when its + net is HIGH and its - net is LOW.
  * time: event driven, ms.  A relay changes state when its coil has been continuously energised (de-energised) for its
    operate (release) time; the timers restart if the drive changes back before the time has run out.
    Operate time (incl. contact settling) K/P/S 2.5-5 ms, STROBE/JUDGE/CLEAR relays 2.5-4 ms; release time K/P/S/RELEASE
    3-10 ms (flyback diode), STROBE/JUDGE/CLEAR relays 1.5-4 ms (diode + zener clamp)   (decision 0001, tables in 1 and 2c).
  * test bench = the bank and the menu network: STROBE/JUDGE/CLEAR levels on J2 are scheduled so that the CONTACT windows
    are the decision-0001 schedule (STROBE electrical window 6 ms, JUDGE contact 12-19 ms, CLEAR contact 21-33 ms of the
    38.5 ms position period), i.e. the bank leads each signal by the actual relay operate time (calibration, test G.1 #5).
    The menu network ties the live lines to the test lead J5.1 while the drum is stable; between positions an unrelated set
    B of lines is tied to the test lead (drum moving): this is what the blocking diodes and the STROBE contact must survive.
Checks per position: K latched exactly for the live lines; P released iff all 26 live; S latched iff not all 26 live; the
lamps (display slave-relay drives and on-board LEDs) show the latched pattern only while stopped; CLEAR releases everything
when not stopped; while stopped K/P/S/lamps are frozen and the test voltage stays off; RELEASE clears all; RUN/STOP_LATCH.
Interface items of decision 0003 (static netlist checks + the simulation above): series diode pin -> coil on the three
timing inputs (R1; the clamp is on the coil side, the pin side keeps only the indicator pull-down), STOP_LATCH on J2.36
(R2), the ESTOP / ILOCK loops closed by solder jumpers and wired to the panel (R3), the 8-pole panel connector with the
HOME return and its pull-down (R4), CHASSIS = D-sub shells + plated holes, separate from 0 V, joined by 1M || 10 nF (R5),
and the series P-MOSFET ahead of F1/F2 in the right orientation (R6; modelled as a closed switch by the simulation).
Negative tests mutate the netlist and require the verification to fail.
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

# ---- schedule of decision 0001, section 2 (c), ms from the start of the position period
PERIOD, WINDOW = 38.5, 6.0
J_ON, J_OFF = 12.0, 19.0
C_ON, C_OFF = 21.0, 33.0


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
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for n in find_all(find1(tree, "nets"), "net"):
            name = find1(n, "name")[1]
            self.nets[name] = [(find1(x, "ref")[1], find1(x, "pin")[1]) for x in find_all(n, "node")]
        # D-sub shells are footprint pads "SH" without a schematic pin: they are on net CHASSIS (0003 7.1)
        for name, nodes in self.nets.items():
            if ("H1", "1") in nodes:
                nodes += [(r, "SH") for r, pt in sorted(self.parts.items()) if re.fullmatch(r"J\d", r) and "DSUB" in pt["footprint"]]
                break


# ----------------------------------------------------------------------------- static circuit
class Circuit:
    def __init__(self, nl: Netlist):
        self.nl = nl
        self.net_id: dict[tuple[str, str], int] = {}
        for i, (name, nodes) in enumerate(nl.nets.items()):
            for nd in nodes:
                self.net_id[nd] = i
        self.n = len(nl.nets)
        self.errs: list[str] = []
        P = nl.parts
        self.relays = {}
        for r in sorted((r for r in P if re.fullmatch(r"K\d+", r)), key=lambda s: int(s[1:])):
            self.relays[r] = dict(
                cp=self.net(r, "1"), cn=self.net(r, "12"),
                poles=[(self.net(r, "4"), self.net(r, "3"), self.net(r, "5")),
                       (self.net(r, "9"), self.net(r, "10"), self.net(r, "8"))])
        self.diodes = [(self.net(r, "2"), self.net(r, "1")) for r in P if re.fullmatch(r"D\d+|DZ\d+", r)]
        self.res = [(self.net(r, "1"), self.net(r, "2")) for r in P if re.fullmatch(r"R[A-Z]\d+", r)]
        self.mos = {r: dict(g=self.net(r, "1"), s=self.net(r, "2"), d=self.net(r, "3"))
                    for r in P if re.fullmatch(r"Q\d+", r)}
        self.fuse_unions = [(self.net(r, "1"), self.net(r, "2")) for r in P if re.fullmatch(r"F\d+", r)]
        # series reverse-polarity P-MOSFET (QP1: pin 2 = drain, pin 3 = source): closed switch for a correct supply
        self.fuse_unions += [(self.net(r, "2"), self.net(r, "3")) for r in P if re.fullmatch(r"QP\d+", r)]
        self.vin = self.net("J4", "1")
        self.gnd = self.net("J4", "2")
        self.test = self.net("J5", "1")
        self.line = [self.net("J1", str(i + 1)) for i in range(N)]
        self.lamp = [self.net("J3", str(i + 1)) for i in range(N)]
        self.lamp_stop = self.net("J3", "27")
        self.vd = self.net("J3", "28")
        self.drive_nets = {"STROBE": self.net("J2", "1"), "JUDGE": self.net("J2", "34"), "CLEAR": self.net("J2", "35")}
        self.run = self.net("J2", "2")
        self.stop_latch = self.net("J2", "36")
        self.panel24, self.start_sw, self.release_sw = (self.net("J6", "1"), self.net("J6", "2"), self.net("J6", "3"))
        # the lane relays K1..K26 and the control relays (found by what their coil net is connected to)
        self.K = [f"K{i + 1}" for i in range(N)]
        self.ref = dict(STB="K27", JUD="K28", CLR="K29", P="K30", S=["K31", "K32", "K33"], REL="K34")

    def net(self, ref, pin):
        k = (ref, pin)
        if k not in self.net_id:
            self.errs.append(f"missing node {ref}.{pin}")
            return -1
        return self.net_id[k]

    # -- level solver
    def solve(self, op: dict, links: list, drives: list):
        """Return (root, high, low, mos_on, shorts); nets are ints."""
        mos_on: set[str] = set()
        for _ in range(8):
            parent = list(range(self.n + 1))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def union(a, b):
                parent[find(a)] = find(b)

            for a, b in self.fuse_unions:
                union(a, b)
            for r, rl in self.relays.items():
                st = op[r]
                for com, nc, no in rl["poles"]:
                    union(com, no if st else nc)
            for a, b in links:
                union(a, b)
            for r in mos_on:
                union(self.mos[r]["d"], self.mos[r]["s"])
            high = {find(self.vin)} | {find(d) for d in drives}
            low = {find(self.gnd)}
            changed = True
            while changed:
                changed = False
                for a, k in self.diodes:
                    ra, rk = find(a), find(k)
                    if ra in high and rk not in high:
                        high.add(rk)
                        changed = True
            new_on = set()
            for r, m in self.mos.items():
                rg = find(m["g"])
                for a, b in self.res:
                    ra, rb = find(a), find(b)
                    if (ra == rg and rb in high) or (rb == rg and ra in high):
                        new_on.add(r)
            if new_on == mos_on:
                break
            mos_on = new_on
        return find, high, low, mos_on, high & low


class Level:
    def __init__(self, circ, op, links, drives):
        self.c = circ
        self.find, self.high, self.low, self.mos_on, self.shorts = circ.solve(op, links, drives)

    def hi(self, net):
        return self.find(net) in self.high

    def lo(self, net):
        return self.find(net) in self.low

    def coil(self, r):
        rl = self.c.relays[r]
        return self.hi(rl["cp"]) and self.lo(rl["cn"])


# ----------------------------------------------------------------------------- timing parameters
def timing_set(circ, mode: str, rng: random.Random):
    """ref -> (operate ms, release ms)."""
    t = {}
    names = circ.ref
    slow = set(circ.K) | {names["P"], names["REL"]} | set(names["S"])
    timing_relays = {names["STB"], names["JUD"], names["CLR"]}
    for r in circ.relays:
        if mode == "typ":
            op, rel = (3.0, 5.0) if r in slow else (3.0, 2.5)
        elif mode == "worst":     # slowest operate, slowest release
            op, rel = (5.0, 10.0) if r in slow else (4.0, 4.0)
        elif mode == "best":      # fastest
            op, rel = (2.5, 3.0) if r in slow else (2.5, 1.5)
        else:
            op = rng.uniform(2.5, 5.0) if r in slow else rng.uniform(2.5, 4.0)
            rel = rng.uniform(3.0, 10.0) if r in slow else rng.uniform(1.5, 4.0)
        t[r] = (op, rel)
    return t


# ----------------------------------------------------------------------------- simulator
class Sim:
    def __init__(self, circ: Circuit, timing, rng):
        self.c, self.timing, self.rng = circ, timing, rng
        self.t = 0.0
        self.op = {r: False for r in circ.relays}
        self.pending: dict[str, tuple[float, bool]] = {}
        self.levels = {"STROBE": False, "JUDGE": False, "CLEAR": False}
        self.live: set[int] = set()
        self.start_on = True
        self.release_pressed = False
        self.events: list[tuple[float, int, object]] = []
        self.seq = 0
        self.flips: dict[str, int] = {r: 0 for r in circ.relays}
        self.shorts = 0
        self.lvl: Level | None = None

    def at(self, t, fn):
        self.seq += 1
        self.events.append((t, self.seq, fn))

    def links(self):
        c = self.c
        ls = [(c.test, c.line[i]) for i in self.live]
        if self.start_on:
            ls.append((c.panel24, c.start_sw))
        if self.release_pressed:
            ls.append((c.panel24, c.release_sw))
        return ls

    def drives(self):
        return [self.c.drive_nets[k] for k, v in self.levels.items() if v]

    def evaluate(self):
        self.lvl = Level(self.c, self.op, self.links(), self.drives())
        if self.lvl.shorts:
            self.shorts += 1
        return self.lvl

    def settle(self):
        """Re-evaluate and (re)schedule relay changes at the current time."""
        lvl = self.evaluate()
        for r in self.c.relays:
            d = lvl.coil(r)
            if d != self.op[r]:
                cur = self.pending.get(r)
                if cur is None or cur[1] != d:
                    op, rel = self.timing[r]
                    self.pending[r] = (self.t + (op if d else rel), d)
            else:
                self.pending.pop(r, None)

    def run_until(self, tend):
        guard = 0
        while True:
            guard += 1
            if guard > 200000:
                raise RuntimeError("simulation did not settle")
            self.settle()
            nxt_rel = min((v[0] for v in self.pending.values()), default=1e18)
            self.events.sort()
            nxt_ev = self.events[0][0] if self.events else 1e18
            nxt = min(nxt_rel, nxt_ev, tend)
            if nxt >= tend and nxt_rel > tend and nxt_ev > tend:
                self.t = tend
                self.settle()
                return
            self.t = max(self.t, nxt)
            for r, (due, tgt) in list(self.pending.items()):
                if due <= self.t + 1e-9:
                    self.op[r] = tgt
                    self.flips[r] += 1
                    del self.pending[r]
            while self.events and self.events[0][0] <= self.t + 1e-9:
                _, _, fn = self.events.pop(0)
                fn(self)

    # -- observation helpers (use the last evaluated level)
    def kset(self):
        return {i for i, r in enumerate(self.c.K) if self.op[r]}

    def lamps(self):
        lvl = self.evaluate()
        return {i for i in range(N) if lvl.lo(self.c.lamp[i])}

    def leds(self):
        lvl = self.evaluate()
        out = set()
        for i in range(N):
            rl = self.c.nl_pin(f"RL{i + 1}", "1")
            dl = self.c.nl_pin(f"DL{i + 1}", "1")
            if lvl.hi(rl) and lvl.lo(dl):
                out.add(i)
        return out

    def stopped(self):
        return all(self.op[r] for r in self.c.ref["S"])


Circuit.nl_pin = lambda self, ref, pin: self.net(ref, pin)


# ----------------------------------------------------------------------------- bench scheduling
def set_level(name, v):
    def fn(s):
        s.levels[name] = v
    return fn


def set_live(live):
    def fn(s):
        s.live = set(live)
    return fn


def schedule_cycle(sim: Sim, T: float, A: set, B: set, jit=0.25, window=WINDOW, j_on=J_ON, j_off=J_OFF,
                   c_on=C_ON, c_off=C_OFF):
    """Schedule one position period starting at T; `A` live while the drum is stable, `B` otherwise."""
    c, tm, rng = sim.c, sim.timing, sim.rng
    jt = lambda: rng.uniform(-jit, jit)
    op_s, rel_s = tm[c.ref["STB"]]
    op_j, rel_j = tm[c.ref["JUD"]]
    op_c, rel_c = tm[c.ref["CLR"]]
    rise, fall = T - (op_s + jt()), T + window - (rel_s + jt())
    sim.at(rise, set_level("STROBE", True))
    sim.at(fall, set_level("STROBE", False))
    sim.at(T - 0.5, set_live(A))                                # the drum is stable around the electrical window
    sim.at(T + window + 0.5 + max(0.0, rel_s - op_s + 1.5), set_live(B))
    sim.at(T + j_on - (op_j + jt()), set_level("JUDGE", True))
    sim.at(T + j_off - (rel_j + jt()), set_level("JUDGE", False))
    sim.at(T + c_on - (op_c + jt()), set_level("CLEAR", True))
    sim.at(T + c_off - (rel_c + jt()), set_level("CLEAR", False))


class Bench:
    """Runs cycles on one simulator and checks every position."""

    def __init__(self, circ, timing, rng, name=""):
        self.sim = Sim(circ, timing, rng)
        self.c = circ
        self.rng = rng
        self.name = name
        self.errs: list[str] = []
        self.T = 20.0
        self.stopped_set: set | None = None   # frozen K pattern while S is latched
        self.cycles = 0
        self.stops = 0
        self.false_stops = 0
        self.window_info: dict = {}

    def err(self, msg):
        if len(self.errs) < 12:
            self.errs.append(f"[{self.name}] {msg}")
        else:
            self.errs.append(None)

    def cycle(self, A: set, B=None, ignore_run=False, **kw):
        sim, c = self.sim, self.c
        B = set(B) if B is not None else {i for i in range(N) if self.rng.random() < 0.5}
        T = self.T = max(self.T, sim.t + 8.0)   # the bank leads each signal by up to one relay operate time
        schedule_cycle(sim, T, set(A), B, **kw)
        was_stopped = self.stopped_set is not None
        exp_k = set(self.stopped_set) if was_stopped else set(A)
        all_live = len(A) == N
        pres = {}
        out = {}

        def probe(name):
            def fn(s):
                lvl = s.evaluate()
                out[name] = dict(k=s.kset(), P=s.op[c.ref["P"]], S=s.stopped(), S_any=any(s.op[r] for r in c.ref["S"]),
                                 lamps=s.lamps(), leds=s.leds(), run=lvl.hi(c.run), latch=lvl.hi(c.stop_latch),
                                 stop_lamp=lvl.lo(c.lamp_stop), test=lvl.hi(c.test),
                                 ops={r: s.op[r] for r in c.relays})
            return fn

        sim.at(T + WINDOW + 3.0, probe("after_strobe"))
        sim.at(T + J_ON - 0.3, probe("before_judge"))
        sim.at(T + J_OFF + 1.0, probe("after_judge"))
        sim.at(T + C_ON - 0.3, probe("before_clear"))
        sim.at(T + PERIOD - 3.0, probe("end"))
        sim.run_until(T + PERIOD - 2.9)
        self.cycles += 1
        a, bj, aj, bc, e = (out[k] for k in ("after_strobe", "before_judge", "after_judge", "before_clear", "end"))
        tag = f"T={T:.1f} |A|={len(A)}"
        # --- K latch
        if a["k"] != exp_k:
            self.err(f"{tag}: K latched {sorted(a['k'])} expected {sorted(exp_k)}")
        # --- P
        exp_p = was_stopped or all_live
        if bj["P"] != exp_p:
            self.err(f"{tag}: P={'on' if bj['P'] else 'off'} expected {'on' if exp_p else 'off'} before JUDGE")
        # --- S
        exp_s = was_stopped or not all_live
        if aj["S"] != exp_s or aj["S_any"] != exp_s:
            self.err(f"{tag}: S={'on' if aj['S'] else 'off'} expected {'on' if exp_s else 'off'} after JUDGE")
        if exp_s and not was_stopped:
            self.stops += 1
            if all_live:
                self.false_stops += 1
        # --- after JUDGE and at the end: lamps / latch / RUN / test voltage
        for nm, o in (("after JUDGE", aj), ("end", e)):
            if exp_s:
                if o["lamps"] != exp_k:
                    self.err(f"{tag}: {nm}: lamps {sorted(o['lamps'])} expected {sorted(exp_k)}")
                if o["leds"] != exp_k:
                    self.err(f"{tag}: {nm}: on-board LEDs {sorted(o['leds'])} expected {sorted(exp_k)}")
                if not (o["latch"] and o["stop_lamp"]) or o["run"]:
                    self.err(f"{tag}: {nm}: STOP_LATCH/stop lamp/RUN wrong {o['latch']},{o['stop_lamp']},{o['run']}")
            else:
                if o["lamps"] or o["leds"] or o["latch"] or o["stop_lamp"]:
                    self.err(f"{tag}: {nm}: lamps/latch on while running")
                if not o["run"]:
                    self.err(f"{tag}: {nm}: RUN low while not stopped")
        if exp_s and aj["test"]:
            self.err(f"{tag}: test voltage present while stopped")
        # --- after CLEAR
        if exp_s:
            if e["k"] != exp_k or not e["P"] or not e["S"]:
                self.err(f"{tag}: end: stopped state not frozen (K {sorted(e['k'])}, P {e['P']}, S {e['S']})")
        else:
            if e["k"] or e["P"] or e["S_any"]:
                self.err(f"{tag}: end: relays still operated after CLEAR: K {sorted(e['k'])} P {e['P']}")
        if not was_stopped and exp_s:
            self.stopped_set = set(A)
        self.T = T + PERIOD
        self.last = (a, bj, aj, e)
        return exp_s

    def release(self, hold=60.0):
        """Operator presses RELEASE for `hold` ms; everything must be cleared and RUN restored."""
        sim, c = self.sim, self.c
        t0 = self.T + 2.0

        def press(s):
            s.release_pressed = True

        def lift(s):
            s.release_pressed = False
        sim.at(t0, press)
        sim.at(t0 + hold, lift)
        res = {}

        def probe(s):
            lvl = s.evaluate()
            res.update(ops={r: s.op[r] for r in c.relays}, run=lvl.hi(c.run), lamps=s.lamps(), latch=lvl.hi(c.stop_latch))
        sim.at(t0 + hold + 22.0, probe)
        sim.run_until(t0 + hold + 22.1)
        if any(res["ops"].values()):
            self.err("RELEASE: relays still operated " + str([r for r, v in res["ops"].items() if v]))
        if not res["run"] or res["lamps"] or res["latch"]:
            self.err(f"RELEASE: RUN {res['run']} lamps {sorted(res['lamps'])} latch {res['latch']}")
        self.stopped_set = None
        self.T = t0 + hold + 24.0


def finish(b: Bench):
    errs = [e for e in b.errs if e]
    if b.sim.shorts:
        errs.append(f"[{b.name}] {b.sim.shorts} evaluations with a net both HIGH and LOW (short)")
    for r, n in b.sim.flips.items():
        pass
    return errs


# ----------------------------------------------------------------------------- the case sets
def case_suite(circ, quick: bool, seed=1):
    rng = random.Random(seed)
    errs: list[str] = []
    stat = dict(cycles=0, stops=0, false_stops=0)
    allset = set(range(N))

    def run(name, mode, plan):
        tm = timing_set(circ, mode, rng)
        b = Bench(circ, tm, random.Random(rng.random()), f"{name}/{mode}")
        for step in plan:
            if step[0] == "cycle":
                b.cycle(*step[1:3], **(step[3] if len(step) > 3 else {}))
            elif step[0] == "release":
                b.release()
            elif step[0] == "stopped_cycle":
                b.cycle(step[1], step[2], ignore_run=True)
        for k in stat:
            stat[k] += {"cycles": b.cycles, "stops": b.stops, "false_stops": b.false_stops}[k]
        errs.extend(finish(b))
        return b

    rnd = lambda k: set(rng.sample(range(N), k))
    # 1. all 26 live, several positions in a row (no stop, everything cleared each time)
    run("all26", "typ", [("cycle", allset, None)] * 3)
    # 2. 25 live (each letter dead once), 1 live, none live
    letters = range(N) if not quick else [0, 12, 25]
    for i in letters:
        run(f"dead-{chr(65 + i)}", "typ", [("cycle", allset - {i}, None), ("release",), ("cycle", allset, None)])
    for i in letters:
        run(f"only-{chr(65 + i)}", "typ", [("cycle", {i}, None), ("release",)])
    run("none-live", "typ", [("cycle", set(), None), ("release",)])
    # 3. random subsets, one stop per run (random sizes incl. 24..26)
    for r in range(8 if quick else 120):
        k = rng.choice([rng.randrange(0, N + 1), 25, 24, 26, 13])
        run(f"rand{r}", rng.choice(["typ", "rand", "rand"]), [("cycle", rnd(k), None), ("release",), ("cycle", allset, None)])
    # 4. long sequences: positions with no stop, a stop, the drum keeps moving (RUN ignored), RELEASE, more positions
    for r in range(2 if quick else 12):
        plan = []
        for _ in range(3):
            plan.append(("cycle", allset, None))
        plan.append(("cycle", rnd(rng.randrange(0, N)), None))              # stop
        for _ in range(3):
            plan.append(("stopped_cycle", rnd(rng.randrange(0, N + 1)), None))  # bank ignores RUN: frozen display
        plan.append(("release",))
        plan.append(("cycle", allset, None))
        plan.append(("cycle", rnd(25), None))
        plan.append(("release",))
        run(f"seq{r}", rng.choice(["typ", "rand"]), plan)
    # 5. corner timings: every relay worst / best at once
    for mode in ("worst", "best"):
        run("corner-all26", mode, [("cycle", allset, None)])
        run("corner-dead", mode, [("cycle", allset - {rng.randrange(N)}, None), ("release",), ("cycle", allset, None)])
        run("corner-only", mode, [("cycle", rnd(1), None), ("release",)])
    return errs, stat


# ----------------------------------------------------------------------------- margin scans (informational, full run only)
def margin_scan(circ):
    """Smallest STROBE window / JUDGE width / CLEAR width that still behaves, at the worst relay times."""
    out = {}
    rng = random.Random(7)
    allset = set(range(N))

    def trial(**kw):
        tm = timing_set(circ, "worst", rng)
        b = Bench(circ, tm, random.Random(3), "scan")
        b.cycle(allset, set(range(0, N, 2)), jit=0.0, **kw)
        ok_all = not [e for e in b.errs if e]
        b2 = Bench(circ, timing_set(circ, "worst", rng), random.Random(3), "scan")
        b2.cycle(allset - {5}, set(range(0, N, 2)), jit=0.0, **kw)
        b2.release()
        ok_stop = not [e for e in b2.errs if e]
        return ok_all and ok_stop

    for w in [x / 10 for x in range(30, 80)]:
        if trial(window=w):
            out["strobe_window_min"] = w
            break
    for width in [x / 10 for x in range(20, 120)]:
        if trial(j_off=J_ON + width):
            out["judge_width_min"] = width
            break
    for width in [x / 10 for x in range(30, 160)]:
        if trial(c_off=C_ON + width):
            out["clear_width_min"] = width
            break
    return out


def missed_stop_stress(circ, n=60, seed=5):
    """Short or long STROBE windows and any relay times: a stop must never be missed; false stops are the safe side."""
    rng = random.Random(seed)
    missed = false_stops = total = 0
    for _ in range(n):
        tm = timing_set(circ, "rand", rng)
        b = Bench(circ, tm, random.Random(rng.random()), "stress")
        A = set(rng.sample(range(N), rng.choice([0, 1, 13, 24, 25, 26])))
        kw = dict(window=rng.uniform(2.0, 8.0))    # JUDGE / CLEAR widths are interface requirements (margin_scan)
        sim = b.sim
        schedule_cycle(sim, b.T, A, {i for i in range(N) if rng.random() < .5}, **kw)
        sim.run_until(b.T + PERIOD + 5)
        total += 1
        stopped = sim.stopped()
        if len(A) < N and not stopped:
            missed += 1
        if len(A) == N and stopped:
            false_stops += 1
    return total, missed, false_stops


# ----------------------------------------------------------------------------- static checks
def static_checks(circ: Circuit, nl: Netlist) -> list[str]:
    e = list(circ.errs)
    P = nl.parts
    relays = [r for r in P if re.fullmatch(r"K\d+", r)]
    if len(relays) != 34:
        e.append(f"expected 34 relays, found {len(relays)}")
    for r in relays:
        if "G6S-2" not in P[r]["footprint"]:
            e.append(f"{r}: footprint {P[r]['footprint']}")
    for pref, cnt in (("D", 32 + 0), ):
        pass
    n_d = len([r for r in P if re.fullmatch(r"D\d+", r)])
    if n_d != 36:
        e.append(f"expected 36 diodes D1..D36, found {n_d}")
    if len([r for r in P if re.fullmatch(r"Q\d+", r)]) != 28:
        e.append("expected 28 MOSFETs (26 lamp drivers + LAMPGND switch + stop lamp)")
    return e + interface_checks(circ, nl)


def interface_checks(circ: Circuit, nl: Netlist) -> list[str]:
    """Decision 0003 items R1-R6 as netlist topology checks."""
    e: list[str] = []
    net_of = {nd: name for name, nodes in nl.nets.items() for nd in nodes}
    nt = lambda r, p: net_of.get((r, p))
    alone = lambda r, p: nt(r, p) is not None and len(nl.nets[nt(r, p)]) == 1
    # R1: pin -> series diode -> coil; clamp on the coil side; indicator (pull-down) on the pin side
    for k, (rel, pin, dref, ind, name) in enumerate((("K27", "1", "D34", "RI1", "STROBE"), ("K28", "34", "D35", "RI2", "JUDGE"),
                                                      ("K29", "35", "D36", "RI3", "CLEAR"))):
        pin_net, coil = nt("J2", pin), nt(rel, "1")
        dz, dc = f"DZ{27 + k}", f"D{27 + k}"
        if pin_net is None or coil is None or pin_net == coil:
            e.append(f"R1 {name}: coil + is on the connector pin net (no series diode)")
            continue
        if not (nt(dref, "2") == pin_net and nt(dref, "1") == coil):
            e.append(f"R1 {name}: series diode {dref} is not anode on J2.{pin}, cathode on the coil")
        if nt(dz, "2") != coil or nt(dc, "2") != nt("J4", "2"):
            e.append(f"R1 {name}: clamp (diode + zener) is not on the coil side")
        if nt(ind, "1") != pin_net:
            e.append(f"R1 {name}: indicator resistor {ind} is not on the pin side")
    # R2 / R3 / R4: connector J2 and the panel
    if nt("J2", "36") is None or nt("J2", "36") != nt("K33", "5") and nt("J2", "36") != nt("K33", "8"):
        e.append("R2: J2.36 is not STOP_LATCH (S3 pole 2 NO)")
    for a, b, sj, lo, hi in (("22", "23", "SJ1", "6", "7"), ("24", "25", "SJ2", None, None)):
        na, nb = nt("J2", a), nt("J2", b)
        if na is None or nb is None or na == nb:
            e.append(f"R3: J2.{a}/J2.{b} are missing or shorted together")
            continue
        if {nt(sj, "1"), nt(sj, "2")} != {na, nb}:
            e.append(f"R3: solder jumper {sj} does not join J2.{a} and J2.{b}")
        if lo and (nt("J6", lo) != na or nt("J6", hi) != nb):
            e.append(f"R3: J6.{lo}/{hi} are not wired to J2.{a}/{b}")
    if "bridged" not in nl.parts.get("SJ1", {}).get("value", "") or "Bridged" not in nl.parts["SJ1"]["footprint"]:
        e.append("R3: SJ1 is not a bridged (closed by default) solder jumper")
    if nt("J6", "5") != nt("J2", "4") or nt("RR4", "1") != nt("J2", "4") or nt("RR4", "2") != nt("J4", "2"):
        e.append("R4: J6.5 / J2.4 (HOME) / pull-down RR4 are not on one net")
    if not alone("J6", "8"):
        e.append("R4: J6.8 is not left unconnected")
    for p in [5, 6, 7] + list(range(8, 22)) + [26, 27, 37]:
        if not alone("J2", str(p)):
            e.append(f"R8: J2.{p} must stay unconnected (is on {nt('J2', str(p))})")
    # R5: CHASSIS
    ch = nt("H1", "1")
    want = {(f"H{k}", "1") for k in range(1, 5)} | {("RCH1", "1"), ("CCH1", "1")} | {(r, "SH") for r in ("J1", "J2", "J3")}
    if ch is None or set(nl.nets[ch]) != want:
        e.append(f"R5: CHASSIS members differ from H1-H4, RCH1, CCH1 and the shells of J1-J3 "
                 f"({sorted(set(nl.nets.get(ch, [])) ^ want)[:6]})")
    elif ch == nt("J4", "2") or nt("RCH1", "2") != nt("J4", "2") or nt("CCH1", "2") != nt("J4", "2"):
        e.append("R5: CHASSIS is not separated from 0 V by 1M || 10nF only")
    if nl.parts.get("RCH1", {}).get("value") != "1M" or not nl.parts.get("CCH1", {}).get("value", "").startswith("10nF"):
        e.append("R5: CHASSIS-0 V pair is not 1M || 10nF")
    # R6: series P-MOSFET, drain on the supply side, source to F1 / F2 / TVS, gate zener + resistor
    vin, vmos = nt("J4", "1"), nt("F1", "1")
    chain = [
        (nt("QP1", "2") == vin and vin is not None, "drain (pin 2) is not on the J4 supply"),
        (nt("QP1", "3") == vmos == nt("F2", "1") == nt("DV1", "1") and vmos != vin, "source (pin 3) is not on F1/F2/TVS (reverse-polarity orientation)"),
        (nt("QP1", "1") is not None and nt("QP1", "1") == nt("DZP1", "2") == nt("RGP1", "1"), "gate is not on DZP1 anode + RGP1"),
        (nt("DZP1", "1") == vmos and nt("RGP1", "2") == nt("J4", "2"), "gate zener cathode / resistor return are wrong"),
        (nt("F1", "1") != vin and nt("F2", "1") != vin, "a PTC is fed from the unprotected input"),
        ("SOT-223" in nl.parts.get("QP1", {}).get("footprint", ""), "QP1 is not a SOT-223 P-MOSFET"),
    ]
    e += [f"R6: {m}" for ok, m in chain if not ok]
    return e


# ----------------------------------------------------------------------------- negative tests
def swap_pins(text: str, ref: str, pa: str, pb: str) -> str:
    pat = lambda p: re.compile(r'(\(ref "%s"\)\s*\(pin )"%s"' % (re.escape(ref), re.escape(p)))
    t, na = pat(pa).subn(r'\1"@@A@@"', text)
    t, nb = pat(pb).subn(r'\1"@@B@@"', t)
    assert na == 1 and nb == 1, (ref, pa, pb, na, nb)
    return t.replace('"@@A@@"', f'"{pb}"').replace('"@@B@@"', f'"{pa}"')


NEGATIVE = [
    ("reverse blocking diode D9 (line -> K9)", [("D9", "1", "2")]),
    ("reverse blocking diode D20 (backflow into the menu net)", [("D20", "1", "2")]),
    ("K5 hold contact NO<->NC (pins 5/3)", [("K5", "5", "3")]),
    ("K7 chain contact NO<->NC (pins 8/10): dead line G would be ignored", [("K7", "8", "10")]),
    ("K26 chain contact NO<->NC (pins 8/10)", [("K26", "8", "10")]),
    ("P self-hold NO<->NC (K30 pins 5/3)", [("K30", "5", "3")]),
    ("P judge-path NC<->NO (K30 pins 10/8): S can never latch", [("K30", "10", "8")]),
    ("S1 self-hold NO<->NC (K31 pins 8/10)", [("K31", "8", "10")]),
    ("S1 H-bypass NO<->NC (K31 pins 5/3): CLEAR would wipe the stopped display", [("K31", "5", "3")]),
    ("S2 lock of P NO<->NC (K32 pins 5/3)", [("K32", "5", "3")]),
    ("S2 test cut NC<->NO (K32 pins 10/8): test voltage stays on while stopped", [("K32", "10", "8")]),
    ("S3 RUN break NC<->NO (K33 pins 3/5)", [("K33", "3", "5")]),
    ("STROBE contact NO<->NC (K27 pins 5/3 and 8/10): test voltage on outside STROBE", [("K27", "5", "3"), ("K27", "8", "10")]),
    ("swap JUDGE and CLEAR inputs (J2 pins 34/35)", [("J2", "34", "35")]),
    ("swap two display lamps (J3 pins 3/4)", [("J3", "3", "4")]),
    ("swap two test register lines (J1 pins 10/11)", [("J1", "10", "11")]),
    ("CLEAR relay contact NC<->NO (K29 pins 3/5 and 10/8)", [("K29", "3", "5"), ("K29", "10", "8")]),
    ("RELEASE relay clear path NC<->NO (K34 pins 10/8)", [("K34", "10", "8")]),
]


def mutate(text, spec):
    for ref, a, b in spec:
        text = swap_pins(text, ref, a, b)
    return text


def move_node(nl: Netlist, node, to_node) -> Netlist:
    """Copy of the netlist with pin `node` moved onto the net of pin `to_node`."""
    import copy
    n2 = copy.deepcopy(nl)
    dst = next(name for name, nodes in n2.nets.items() if to_node in nodes)
    for nodes in n2.nets.values():
        if node in nodes:
            nodes.remove(node)
    n2.nets[dst].append(node)
    return n2


def swap_nodes(nl: Netlist, a, b) -> Netlist:
    """Copy of the netlist with the nets of two pins (possibly of different parts) exchanged."""
    import copy
    n2 = copy.deepcopy(nl)
    na = next(name for name, nodes in n2.nets.items() if a in nodes)
    nb = next(name for name, nodes in n2.nets.items() if b in nodes)
    n2.nets[na].remove(a); n2.nets[nb].remove(b)
    n2.nets[na].append(b); n2.nets[nb].append(a)
    return n2


# decision-0003 negative tests: (description, mutation of the parsed netlist)
NEGATIVE_0003 = [
    ("reversed series P-MOSFET QP1 (pins 2<->3)", lambda nl: swap_nodes(nl, ("QP1", "2"), ("QP1", "3"))),
    ("series diode D34 (STROBE) reversed", lambda nl: swap_nodes(nl, ("D34", "1"), ("D34", "2"))),
    ("series diode D35 (JUDGE) missing: coil straight on the J2.34 pin", lambda nl: move_node(nl, ("K28", "1"), ("J2", "34"))),
    ("series diode D36 (CLEAR) missing: coil straight on the J2.35 pin", lambda nl: move_node(nl, ("K29", "1"), ("J2", "35"))),
    ("STROBE clamp zener DZ27 left on the pin side", lambda nl: move_node(nl, ("DZ27", "2"), ("J2", "1"))),
    ("ESTOP loop open: SJ1 pin 2 not on ESTOP_B", lambda nl: move_node(nl, ("SJ1", "2"), ("J2", "24"))),
    ("ILOCK jumper SJ2 missing its return (pin 2 on 0 V)", lambda nl: move_node(nl, ("SJ2", "2"), ("J4", "2"))),
    ("panel ESTOP wires crossed (J6 pins 6<->7)", lambda nl: swap_nodes(nl, ("J6", "6"), ("J6", "7"))),
    ("HOME pull-down RR4 not on the HOME net", lambda nl: move_node(nl, ("RR4", "1"), ("J4", "2"))),
    ("J2 pin 26 (reserved) tied to +24 V", lambda nl: move_node(nl, ("J2", "26"), ("F1", "2"))),
    ("mounting hole H2 on 0 V instead of CHASSIS", lambda nl: move_node(nl, ("H2", "1"), ("J4", "2"))),
    ("CHASSIS tied straight to 0 V (RCH1 pin 1 on 0 V)", lambda nl: move_node(nl, ("RCH1", "1"), ("J4", "2"))),
]


# ----------------------------------------------------------------------------- main
def find_cli() -> str:
    c = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if c:
        return c
    for p in ("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "/usr/bin/kicad-cli"):
        if Path(p).exists():
            return p
    raise SystemExit("kicad-cli not found (set KICAD_CLI)")


def default_build_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "christopher" / "test-register"


def export_netlist(outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "test_register.net"
    subprocess.run([find_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out),
                    str(HERE / "test_register.kicad_sch")], check=True, stdout=subprocess.DEVNULL)
    return out


def verify(nl: Netlist, quick: bool):
    circ = Circuit(nl)
    errs = static_checks(circ, nl)
    if errs:
        return errs, None, circ
    e2, stat = case_suite(circ, quick)
    return errs + e2, stat, circ


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist")
    ap.add_argument("--build-dir")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    net = Path(args.netlist) if args.netlist else export_netlist(Path(args.build_dir) if args.build_dir else default_build_dir())
    text = net.read_text(encoding="utf8")
    nl = Netlist(text)
    print(f"netlist: {len(nl.parts)} components, {len(nl.nets)} nets")
    t0 = time.time()
    errs, stat, circ = verify(nl, args.quick)
    if errs:
        print(f"FAIL: {len(errs)} discrepancies, first: {errs[:6]}")
        return 1
    print(f"PASS ({time.time() - t0:.0f} s): {stat['cycles']} position cycles, {stat['stops']} stops "
          f"(false stops with all 26 live: {stat['false_stops']})")
    print("  K latched = live lines, P released iff all 26 live, S latched iff not all 26 live, lamps show the latched\n"
          "  pattern only while stopped, CLEAR releases everything when running, stopped state frozen, RELEASE clears all.")
    if not args.quick:
        total, missed, fs = missed_stop_stress(circ)
        print(f"stress (out-of-spec windows/relay times): {total} trials, missed stops {missed}, false stops {fs} (safe side)")
        if missed:
            print("FAIL: a stop was missed under out-of-spec timing")
            return 1
        print("margins at worst-case relay times (smallest value that still passes):", margin_scan(circ))
    ok = True
    print("negative tests (mutated netlist copies must fail):")
    for name, mut in NEGATIVE_0003:
        try:
            bad, _, _ = verify(mut(nl), True)
        except Exception as ex:
            bad = [f"exception {type(ex).__name__}: {ex}"]
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} discrepancies)")
        ok &= bool(bad)
    for name, spec in NEGATIVE:
        try:
            nlm = Netlist(mutate(text, spec))
            bad, _, _ = verify(nlm, True)
        except Exception as ex:   # a mutation that breaks the circuit model counts as detected
            bad = [f"exception {type(ex).__name__}: {ex}"]
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} discrepancies)")
        ok &= bool(bad)
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
