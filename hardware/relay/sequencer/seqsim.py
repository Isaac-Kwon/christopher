"""Event-driven switch-level simulation engine for the sequencer netlist (stdlib only).

The circuit is taken ONLY from the exported KiCad netlist (seqnet.Netlist): relays G6S-2 (coil + pin 1, coil - pin 12,
pole 1 COM 4 / NC 3 / NO 5, pole 2 COM 9 / NC 10 / NO 8), diodes (pin 1 = cathode, pin 2 = anode), PTC fuses and the
series MOSFET (closed), solder jumpers (bridged = closed), TBD62783A (channel i: IN pin i, OUT pin 19 - i, supply pin 9),
the LMC555 clock (U1) and the 74HC4017 ring counter (U2).  A net is HIGH (5 V or 24 V) when it is joined by closed
contacts / static wires, or through forward diodes, to a source of that level; HIGH and 0 V in one joined group = short,
5 V and 24 V in one group = domain violation.  A relay coil is energised when its + net is 24 V and the - net is 0 V.

Relay time model (decision 0001): operate 2.5-5 ms (including contact settling), release 1.5-4 ms for coils with the
diode + zener clamp and 3-10 ms for the plain flyback diode; the contact transfers with a break-before-make dead time
of 0.2-0.6 ms (COM floats); operate / release only completes when the coil stays in its new state for the whole time.
"""
from __future__ import annotations

import heapq
import random
import re
from dataclasses import dataclass, field

Q_PIN = {"3": 0, "2": 1, "4": 2, "7": 3, "10": 4, "1": 5, "5": 6, "6": 7, "9": 8, "11": 9}   # 74HC4017 pin -> state


@dataclass
class Relay:
    name: str
    ref: str
    coil: int
    poles: list
    fast: bool
    state: bool = False
    transit: bool = False
    pend: int | None = None          # event serial of the pending completion
    pend_to: bool = False
    op: tuple = (2.5, 5.0)
    rel: tuple = (1.5, 4.0)
    n_ops: int = 0


class Sim:
    def __init__(self, nl, seed=0, mode="random", tol=0.03, t_tick=6.3, verbose=False):
        self.nl = nl
        self.rng = random.Random(seed)
        self.mode = mode
        self.t = 0.0
        self.ev: list = []
        self.serial = 0
        self.errors: list[str] = []
        self.watch_names: set[str] = set()
        self.on_change = None            # callback(t, name, level)
        self.edges: dict[str, list] = {}
        self.ext: dict[str, bool] = {}   # externally driven nets (24 V sources)
        self.power = True
        self.t_tick = t_tick * (1 + self.rng.uniform(-tol, tol))
        self.jit = 0.003
        self.build()
        self.ring = self.rng.randrange(10)
        self.por_done = False
        self.levels_prev: dict[str, int] = {}
        self.tbd_on = {}
        self.ncalls = 0
        self.log = None

    # ---------------------------------------------------------------- static structure
    def build(self):
        nl = self.nl
        names = list(nl.nets)
        self.nid = {n: i for i, n in enumerate(names)}
        self.names = names
        n = len(names)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        def N(ref, pin):
            net = nl.net(ref, pin)
            return self.nid[net] if net is not None else -1

        self.N = N
        self.relays: list[Relay] = []
        self.diodes: list[tuple[int, int]] = []
        self.tbd = []
        for ref, p in nl.parts.items():
            v = p["value"]
            if v.startswith("G6S-2 "):
                coil = N(ref, "1")
                cn = N(ref, "12")
                assert names[cn] == "GND", (ref, names[cn])
                poles = [(N(ref, "4"), N(ref, "3"), N(ref, "5")), (N(ref, "9"), N(ref, "10"), N(ref, "8"))]
                self.relays.append(Relay(v[6:], ref, coil, poles, False))
            elif re.fullmatch(r"D\d+", ref) and v not in ("SMAJ33A",):
                self.diodes.append((N(ref, "2"), N(ref, "1")))
            elif re.fullmatch(r"DZ\d+", ref) and v != "12V":
                self.diodes.append((N(ref, "2"), N(ref, "1")))
            elif re.fullmatch(r"F\d+", ref):
                union(N(ref, "1"), N(ref, "2"))
            elif re.fullmatch(r"Q\d+", ref):
                union(N(ref, "2"), N(ref, "3"))
            elif re.fullmatch(r"SJ\d+", ref) and "bridged" in v:
                union(N(ref, "1"), N(ref, "2"))
            elif v == "TBD62783A":
                self.tbd.append((ref, [(N(ref, str(i)), N(ref, str(19 - i))) for i in range(1, 9)], N(ref, "9")))
        # clamp class: a relay whose coil + net is the anode of a zener (DZ clamp) releases fast
        zener_anodes = {N(r, "2") for r, p in nl.parts.items() if re.fullmatch(r"DZ\d+", r) and p["value"] != "12V"}
        for r in self.relays:
            r.fast = r.coil in zener_anodes
            r.rel = (1.5, 4.0) if r.fast else (3.0, 10.0)
        self.rname = {r.name: r for r in self.relays}
        self.base_parent = [find(i) for i in range(n)]
        self.gnd = self.base_parent[self.nid["GND"]]
        self.sw = self.nid["SW"]
        self.v5 = self.nid["V5"]
        self.vin = self.nid["VIN"]
        # ring counter pins
        self.qnet = {nl.net("U2", pin): s for pin, s in Q_PIN.items()}
        self.qid = {s: self.nid[name] for name, s in self.qnet.items()}
        self.park_id = self.nid[nl.net("U2", "13")]
        self.reset_id = self.nid[nl.net("U2", "15")]
        self.clk_ok = nl.net("U1", "3") == nl.net("U2", "14")
        if not self.clk_ok:
            self.errors.append("555 output (U1.3) is not joined to the ring counter clock (U2.14)")

    # ---------------------------------------------------------------- solving
    def solve(self):
        self.ncalls += 1
        base = self.base_parent
        parent = base[:]

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for r in self.relays:
            if r.transit:
                continue
            for com, nc, no in r.poles:
                tgt = no if r.state else nc
                if tgt >= 0 and com >= 0:
                    ra, rb = find(com), find(tgt)
                    if ra != rb:
                        parent[ra] = rb
        for _ in range(6):
            lev: dict[int, int] = {}
            errs: list[str] = []

            def src(net, level):
                root = find(net)
                old = lev.get(root, 0)
                if old and old != level:
                    errs.append(f"domain short {self.names[net]} ({old} V vs {level} V)")
                lev[root] = max(old, level)

            if self.power:
                src(self.vin, 24)
                src(self.v5, 5)
            for name, on in self.ext.items():
                if on:
                    src(self.nid[name], 24)
            if self.power:
                src(self.qid[self.ring], 5)
            for (ref, chans, vcc), on in zip(self.tbd, [self.tbd_on.get(t[0], [False] * 8) for t in self.tbd]):
                for ch, (i, o) in enumerate(chans):
                    if on[ch] and o >= 0:
                        src(o, 24)
            changed = True
            while changed:
                changed = False
                for a, k in self.diodes:
                    la = lev.get(find(a), 0)
                    if la:
                        rk = find(k)
                        lk = lev.get(rk, 0)
                        if lk < la:
                            if lk and lk != la:
                                errs.append(f"domain violation through a diode into {self.names[k]}")
                            else:
                                lev[rk] = la
                                changed = True
            gl = lev.get(find(self.gnd), 0)
            if gl:
                errs.append("short circuit: a source reaches 0 V")
            # TBD outputs
            new_on = {}
            for ref, chans, vcc in self.tbd:
                vc = lev.get(find(vcc), 0)
                new_on[ref] = [(vc == 24 and lev.get(find(i), 0) == 5) for i, o in chans]
            if new_on == self.tbd_on:
                break
            self.tbd_on = new_on
        self.lev = lev
        self.find = find
        if errs:
            for e in errs:
                if e not in self.errors:
                    self.errors.append(f"t={self.t:.3f}: {e}")
        return lev

    def level(self, name) -> int:
        return self.lev.get(self.find(self.nid[name]), 0)

    # ---------------------------------------------------------------- scheduling
    def schedule(self, t, kind, payload=None):
        self.serial += 1
        heapq.heappush(self.ev, (t, self.serial, kind, payload))
        return self.serial

    def draw(self, lo, hi):
        if self.mode == "slow":
            return hi
        if self.mode == "fast":
            return lo
        if self.mode == "corner":
            return lo if self.rng.random() < 0.5 else hi
        return self.rng.uniform(lo, hi)

    def update_relays(self):
        for r in self.relays:
            on = self.lev.get(self.find(r.coil), 0) == 24
            if r.transit:
                continue
            if on != r.state:
                if r.pend is None or r.pend_to != on:
                    t = self.t + self.draw(*(r.op if on else r.rel))
                    r.pend = self.schedule(t, "relay", r)
                    r.pend_to = on
            else:
                r.pend = None          # coil returned to the old state before the move was completed

    def apply_relay(self, serial, r: Relay):
        if r.pend != serial:
            return
        r.pend = None
        r.transit = True
        self.schedule(self.t + self.rng.uniform(0.2, 0.6), "land", (r, r.pend_to))

    # ---------------------------------------------------------------- main loop
    def tick_period(self):
        return self.t_tick * (1 + self.rng.uniform(-self.jit, self.jit))

    def start(self, t0=0.0):
        self.schedule(t0 + self.rng.uniform(0, self.t_tick), "tick")
        self.schedule(t0 + 0.2, "por")
        self.solve()
        self.update_relays()
        self.snapshot()

    def snapshot(self):
        for name in self.watch_names:
            lv = 1 if self.level(name) == 24 else 0
            if self.levels_prev.get(name) != lv:
                self.levels_prev[name] = lv
                self.edges.setdefault(name, []).append((self.t, lv))
                if self.on_change:
                    self.on_change(self.t, name, lv)

    def run_until(self, t_end):
        while self.ev and self.ev[0][0] <= t_end:
            t, ser, kind, payload = heapq.heappop(self.ev)
            self.t = t
            if kind == "relay":
                self.apply_relay(ser, payload)
            elif kind == "land":
                r, to = payload
                r.transit = False
                r.state = to
                r.n_ops += 1
                if self.log is not None:
                    self.log.append((self.t, r.name, int(to), self.ring))
            elif kind == "tick":
                if self.power:
                    if self.lev.get(self.find(self.reset_id), 0) or not self.por_done:
                        self.ring = 0
                        self.por_done = True
                    elif self.level_raw(self.park_id) != 5:
                        self.ring = (self.ring + 1) % 10
                self.schedule(self.t + self.tick_period(), "tick")
            elif kind == "por":
                self.por_done = False
            elif kind == "cb":
                payload()
            self.solve()
            self.update_relays()
            self.snapshot()
        self.t = t_end

    def level_raw(self, nid):
        return self.lev.get(self.find(nid), 0)

    # ---------------------------------------------------------------- power
    def set_power(self, on):
        self.power = on
        if not on:
            self.ring = self.rng.randrange(10)
            self.por_done = False
            self.schedule(self.t + 0.0, "noop")

    def edge_list(self, name):
        return self.edges.get(name, [])
