"""Simulated unit under test + HAL, to run the firmware logic on a PC.

A passive unit is a graph over the 52 nodes (A0..A25 = 0..25, B0..B25 = 26..51). A driven node
raises every node in its connected component. Faults are plain graph edits.
"""

from tester_core import (
    ADC_FULL, ADC_RATIO, ADC_VREF, F_ESTOP_OK, F_ILOCK_OK, NODES, R_LED, R_SERIES, V_DRIVE, V_LED,
)


class SimNetwork:
    """Undirected conductor graph; edge value = resistance in ohm."""

    def __init__(self):
        self.edges = {}

    @staticmethod
    def _key(a, b):
        return (a, b) if a < b else (b, a)

    def connect(self, a, b, ohm=0.05):
        if a != b:
            self.edges[self._key(a, b)] = ohm
        return self

    def disconnect(self, a, b):
        self.edges.pop(self._key(a, b), None)
        return self

    def has(self, a, b):
        return self._key(a, b) in self.edges

    def neighbours(self, n):
        for (a, b), r in self.edges.items():
            if a == n:
                yield b, r
            elif b == n:
                yield a, r

    def component(self, start):
        seen = {start}
        todo = [start]
        while todo:
            n = todo.pop()
            for m, _ in self.neighbours(n):
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        return seen

    def path_resistance(self, a, b):
        """Resistance along a breadth-first path, None when not connected."""
        dist = {a: 0.0}
        todo = [a]
        while todo:
            nxt = []
            for n in todo:
                for m, r in self.neighbours(n):
                    if m not in dist:
                        dist[m] = dist[n] + r
                        nxt.append(m)
            todo = nxt
        return dist.get(b)

    def copy(self):
        c = SimNetwork()
        c.edges = dict(self.edges)
        return c


def network_from_links(links):
    net = SimNetwork()
    for a, b in links:
        net.connect(a, b)
    return net


class SimHal:
    """``network``: static unit; ``sequence``: iterator of (pos15, SimNetwork), one per STROBE."""

    name = "sim"
    has_adc = True

    def __init__(self, network=None, sequence=None, strobe_width_us=6000, scan_us_per_step=150,
                 flags=F_ESTOP_OK | F_ILOCK_OK):
        self.network = network if network is not None else SimNetwork()
        self.sequence = iter(sequence) if sequence is not None else None
        self.pos = 0
        self.strobe_us = strobe_width_us
        self.scan_us_per_step = scan_us_per_step
        self.flags = flags
        self.settle_us = 100
        self.driven = 0
        self.drive_log = []
        self.run = 0
        self.home = 0
        self.strobe_seen = 0
        self.end_after = None
        self._clock = 0

    # -- drive / sense ---------------------------------------------------------------------
    def drive(self, mask):
        self.driven = mask
        self.drive_log.append(mask)
        if mask:
            self._clock += self.scan_us_per_step

    def settle(self):
        pass

    def sense(self):
        out = 0
        for n in range(NODES):
            if self.driven >> n & 1:
                for m in self.network.component(n):
                    out |= 1 << m
        return out

    def adc(self, nd):
        if not self.driven:
            return 0
        d = [n for n in range(NODES) if self.driven >> n & 1][0]
        r = self.network.path_resistance(d, nd)
        if r is None:
            return 0
        # driven node feeds its own sense LED and, through r, the far node's sense LED
        x = R_LED * (V_DRIVE - V_LED) / (R_LED + R_SERIES + R_SERIES * R_LED / (r + R_LED))
        v_node = V_LED + x * R_LED / (r + R_LED)
        return int(round(v_node * ADC_RATIO / ADC_VREF * ADC_FULL))

    # -- auxiliary signals -------------------------------------------------------------------
    def aux(self):
        fl = self.flags
        if self.end_after is not None and self.strobe_seen >= self.end_after:
            fl |= 8
        return self.pos, fl

    def set_run(self, v):
        self.run = v

    def set_home(self, v):
        self.home = v

    def wait_strobe(self, timeout_ms):
        if self.sequence is None:
            return False
        try:
            self.pos, self.network = next(self.sequence)
        except StopIteration:
            return False
        self.strobe_seen += 1
        return True

    def strobe_width(self):
        return self.strobe_us

    def ticks_us(self):
        return self._clock

    def ticks_since(self, t0):
        return self._clock - t0


def pack_pos(left, middle, right):
    return left | middle << 5 | right << 10


def unpack_pos(pos):
    return pos & 31, pos >> 5 & 31, pos >> 10 & 31
