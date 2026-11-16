"""Event-driven switch-level simulation engine for the sequencer netlist (stdlib only).

The circuit is taken ONLY from the exported KiCad netlist (seqnet.Netlist): relays G6S-2 (coil + pin 1, coil - pin 12,
pole 1 COM 4 / NC 3 / NO 5, pole 2 COM 9 / NC 10 / NO 8), diodes (pin 1 = cathode, pin 2 = anode), PTC fuses and the
series MOSFET (closed), solder jumpers (bridged = closed), TBD62783A (channel i: IN pin i, OUT pin 19 - i, supply pin 9),
the LMC555 clock (U1) and the 74HC4017 ring counter (U2).  The 555 is an RC model taken from the netlist: R1 = the resistor from
5 V to the discharge pin, C = the capacitor from the threshold pin to 0 V, R2 = the resistance from the discharge pin to the
threshold pin through resistors and the trimmer, with the nets joined by the CLOSED relay contacts of the moment (the observation
resistor and its shorting contacts).  The capacitor voltage is continuous: when R2 changes in the middle of a tick the voltage
goes on from where it is at the new rate (charge 1/3 -> 2/3 Vcc through R1 + R2, discharge 2/3 -> 1/3 through R2, ring clock on
the rising output).  The trimmer RV1 is set (like the oscilloscope adjustment) so that the tick with every relay released is
t_tick; the capacitor then has the tolerance tol.  A net is HIGH (5 V or 24 V) when it is joined by closed
contacts / static wires, or through forward diodes, to a source of that level; HIGH and 0 V in one joined group = short,
5 V and 24 V in one group = domain violation.  A relay coil is energised when its + net is 24 V and the - net is 0 V.

Relay time model (decision 0001): operate 2.5-5 ms (including contact settling), release 1.5-4 ms for coils with the
diode + zener clamp and 3-10 ms for the plain flyback diode; the contact transfers with a break-before-make dead time
of 0.2-0.6 ms (COM floats); operate / release only completes when the coil stays in its new state for the whole time.
"""
from __future__ import annotations

import heapq
import math
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
        self.t_tick = t_tick              # nominal fast tick (trimmer setting)
        self.ctol = 1 + self.rng.uniform(-tol, tol)
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
        self.osc_setup()

    # ---------------------------------------------------------------- 555 (RC model from the netlist)
    @staticmethod
    def value_of(v: str) -> float:
        m = re.match(r"\s*([0-9.]+)\s*([pnumkKM]?)", v)
        if not m:
            return float("nan")
        mult = {"": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6}[m.group(2)]
        return float(m.group(1)) * mult

    def osc_setup(self):
        nl, N = self.nl, self.N
        self.dis, self.th = N("U1", "7"), N("U1", "6")
        rails = {self.nid[x] for x in ("GND", "V5", "SW", "VIN") if x in self.nid}
        res = []                                              # (net a, net b, ohms)
        self.r1 = None
        self.cap = None
        pot_max = None
        for ref, p in nl.parts.items():
            if re.fullmatch(r"R\d+", ref):
                a, b = N(ref, "1"), N(ref, "2")
                v = self.value_of(p["value"])
                if {a, b} == {self.v5, self.dis}:
                    self.r1 = v
                elif a not in rails and b not in rails:
                    res.append((a, b, v))
            elif re.fullmatch(r"RV\d+", ref):
                pot_max = self.value_of(p["value"])
                self.pot = (N(ref, "1"), N(ref, "2"), N(ref, "3"))
            elif re.fullmatch(r"C\d+", ref) and {N(ref, "1"), N(ref, "2")} == {self.th, self.nid["GND"]}:
                self.cap = self.value_of(p["value"])
        if self.r1 is None or self.cap is None or pot_max is None:
            self.errors.append("555 timing network not found (R1 5 V -> DIS, C TH -> 0 V, trimmer)")
            self.r1, self.cap, pot_max = 10e3, 22e-9, 100e3
            self.pot = (self.dis, self.th, self.th)
        self.pot_max = pot_max
        # the region of the timing network: nets reached from DIS / TH through resistors, the trimmer and relay contacts
        adj: dict[int, set] = {}

        def link(a, b):
            if a < 0 or b < 0 or a in rails or b in rails:
                return
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        for a, b, _ in res:
            link(a, b)
        link(self.pot[0], self.pot[1])
        link(self.pot[1], self.pot[2])
        for r in self.relays:
            for com, nc, no in r.poles:
                link(com, nc)
                link(com, no)
        region, todo = {self.dis, self.th}, [self.dis, self.th]
        while todo:
            x = todo.pop()
            for y in adj.get(x, ()):
                if y not in region:
                    region.add(y)
                    todo.append(y)
        self.osc_res = [(a, b, v) for a, b, v in res if a in region or b in region]
        self.osc_relays = [r for r in self.relays if any(n in region for pole in r.poles for n in pole if n >= 0)]
        # trimmer: set so that the tick with every relay released (power-up state) is t_tick with the nominal capacitor
        self.rv = 0.0
        released = self.base_parent[:]

        def findr(a):
            while released[a] != a:
                released[a] = released[released[a]]
                a = released[a]
            return a
        for r in self.osc_relays:
            for com, nc, no in r.poles:
                if com >= 0 and nc >= 0:
                    ra, rb = findr(com), findr(nc)
                    if ra != rb:
                        released[ra] = rb
        r2_0 = self.r2_with(findr)
        want = (self.t_tick * 1e-3 / (math.log(2) * self.cap) - self.r1) / 2 - r2_0
        self.rv = min(max(want, 0.0), pot_max)
        self.cap_act = self.cap * self.ctol
        self.osc_key = None
        self.osc_phase = "d"
        self.osc_v = 1 / 3 + self.rng.random() / 3
        self.osc_t = 0.0
        self.osc_ser = None
        self.osc_mul = 1.0
        self.r2_now = None
        self.last_edge = None
        self.min_period = 1e9
        self.ring_log: list = []          # (t, new ring state) on every ring step

    def r2_with(self, find) -> float:
        """series resistance DIS -> TH (Dijkstra over the timing resistors, nets joined by closed contacts)"""
        a0, b0 = find(self.dis), find(self.th)
        edges: dict[int, list] = {}

        def add(a, b, v):
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            edges.setdefault(ra, []).append((rb, v))
            edges.setdefault(rb, []).append((ra, v))
        for a, b, v in self.osc_res:
            add(a, b, v)
        p1, p2, p3 = self.pot
        add(p1, p2, self.rv)
        add(p2, p3, self.pot_max - self.rv)
        dist = {a0: 0.0}
        pq = [(0.0, a0)]
        while pq:
            d_, x = heapq.heappop(pq)
            if x == b0:
                return d_
            if d_ > dist.get(x, 1e30):
                continue
            for y, v in edges.get(x, ()):
                nd = d_ + v
                if nd < dist.get(y, 1e30):
                    dist[y] = nd
                    heapq.heappush(pq, (nd, y))
        return math.inf

    def osc_taus(self):
        r2 = self.r2_now
        if r2 is None or math.isinf(r2):
            return math.inf, math.inf
        return (self.r1 + r2) * self.cap_act * 1e3 * self.osc_mul, r2 * self.cap_act * 1e3 * self.osc_mul

    def osc_progress(self):
        """bring the capacitor voltage to the present time with the time constant in force since osc_t"""
        dt = self.t - self.osc_t
        if dt > 0 and self.r2_now is not None and not math.isinf(self.r2_now):
            tc, td = self.osc_taus()
            if self.osc_phase == "c":
                self.osc_v = 1 - (1 - self.osc_v) * math.exp(-dt / tc)
            else:
                self.osc_v = self.osc_v * math.exp(-dt / td)
        self.osc_t = self.t

    def osc_schedule(self):
        tc, td = self.osc_taus()
        if self.osc_phase == "c":
            rem = 0.0 if self.osc_v >= 2 / 3 else tc * math.log((1 - self.osc_v) / (1 / 3))
        else:
            rem = 0.0 if self.osc_v <= 1 / 3 else td * math.log(3 * self.osc_v)
        if math.isinf(rem):
            self.osc_ser = None
            return
        self.osc_ser = self.schedule(self.t + rem, "osc", None)

    def osc_check(self):
        """after a solve: re-time the running phase when the timing network has changed"""
        key = tuple((r.state, r.transit) for r in self.osc_relays)
        if key == self.osc_key:
            return
        self.osc_key = key
        self.osc_progress()
        self.r2_now = self.r2_with(self.find)
        self.osc_schedule()

    def osc_event(self, ser):
        if ser != self.osc_ser:
            return
        self.osc_t = self.t
        if self.osc_phase == "c":
            self.osc_phase, self.osc_v = "d", 2 / 3
        else:
            self.osc_phase, self.osc_v = "c", 1 / 3
            self.ring_edge()
        self.osc_mul = 1 + self.rng.uniform(-self.jit, self.jit)
        self.osc_schedule()

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
    def ring_edge(self):
        """rising edge of the 555 output = clock of the ring counter"""
        if self.last_edge is not None:
            self.min_period = min(self.min_period, self.t - self.last_edge)
        self.last_edge = self.t
        if self.power:
            if self.lev.get(self.find(self.reset_id), 0) or not self.por_done:
                self.ring = 0
                self.por_done = True
                self.ring_log.append((self.t, 0))
            elif self.level_raw(self.park_id) != 5:
                self.ring = (self.ring + 1) % 10
                self.ring_log.append((self.t, self.ring))

    def start(self, t0=0.0):
        self.t = t0
        self.osc_t = t0
        self.schedule(t0 + 0.2, "por")
        self.solve()
        self.update_relays()
        self.osc_check()
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
            elif kind == "osc":
                self.osc_event(ser)
            elif kind == "por":
                self.por_done = False
            elif kind == "cb":
                payload()
            self.solve()
            self.update_relays()
            self.osc_check()
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
