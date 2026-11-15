"""A register-level fake of ``bombe_array`` for hardware-free tests and demos.

``FakeArray`` implements the Transport interface (read32/write32) and the register map of
rtl/fpga/README.md.  The sweep itself is delegated to ``sweep(order_word, options_word, edges)``
(returns ``[((g, l, m, r), register), ...]``), so tests can plug in the software bombe or scripted
stops.  A sweep takes ``busy_polls`` STATUS reads to finish, to exercise the polling loop.

``FakeDemoArray`` additionally emulates the exhibit mode (PACE, DEMO, STEP, live drum, test
register, 26x26 snapshot) in wall-clock time, so the visualizer runs without a board.
"""

from __future__ import annotations

import time
from collections import deque

from bombe_fpga.driver import (
    CTRL_ABORT, CTRL_START, CTRL_STEP, DEMO_PAUSE_ON_STOP, DEMO_SINGLE, ENGINE_STRIDE, GLOBAL_ID, GLOBAL_INFO,
    ID_MAGIC, POSITION_CYCLES, REG, ST_HOLD,
)

DEMO_READ_REGS = (REG["PACE"], REG["DEMO"], REG["DRUM"], REG["TESTREG"], REG["SNAP_POS"], REG["SNAP_ITERS"])


class FakeEngine:
    def __init__(self, sweep, busy_polls, fifo_depth):
        self.sweep, self.busy_polls, self.fifo_depth = sweep, busy_polls, fifo_depth
        self.regs = {"ORDER": 0, "OPTIONS": 0, "EDGE_EN": 0, "PACE": 0, "DEMO": 0}
        self.edges = [0] * 32
        self.fifo = deque()
        self.pending = deque()     # stops not yet moved into the FIFO (engine stalls when it is full)
        self.busy = 0
        self.done = False
        self.positions = 0
        self.log = []              # (order_word, options_word, tuple(edges)) per START

    def start(self):
        order, opt, en = self.regs["ORDER"], self.regs["OPTIONS"], self.regs["EDGE_EN"]
        edges = tuple(self.edges[i] for i in range(32) if en >> i & 1)
        self.log.append((order, opt, edges))
        self.pending = deque(self.sweep(order, opt, edges))
        self.fifo.clear()
        self.busy, self.done = self.busy_polls, False
        self.positions = 17576

    def abort(self):
        self.busy, self.pending = 0, deque()
        self.done = True

    def step(self):
        while self.pending and len(self.fifo) < self.fifo_depth:
            self.fifo.append(self.pending.popleft())
        if self.busy > 0 and not self.pending:
            self.busy -= 1
        if self.busy == 0 and not self.pending:
            self.done = True

    def status(self):
        self.step()
        return (1 if self.busy else 0) | (2 if self.done else 0) | (4 if self.fifo else 0)

    def stop_pos(self):
        if not self.fifo:
            return 0
        g, l, m, r = self.fifo[0][0]
        return 1 << 31 | g << 20 | l << 15 | m << 10 | r << 5

    def stop_reg(self):
        v = self.fifo.popleft()[1] if self.fifo else 0
        return v

    # exhibit registers: the plain fake has no snapshot (PACE / DEMO read back only)
    def demo_read(self, off):
        if off == REG["PACE"]:
            return self.regs["PACE"]
        if off == REG["DEMO"]:
            return self.regs["DEMO"]
        return 0

    def demo_write(self, off, value):
        if off == REG["PACE"]:
            self.regs["PACE"] = value
        elif off == REG["DEMO"]:
            self.regs["DEMO"] = value & 3

    def ctrl_step(self):
        pass


class FakeArray:
    def __init__(self, sweep, n_engines=2, max_edges=16, busy_polls=3, fifo_depth=4, clock_mhz=100):
        self.engines = [FakeEngine(sweep, busy_polls, fifo_depth) for _ in range(n_engines)]
        self.info = n_engines | max_edges << 8 | clock_mhz << 16
        self.max_edges = max_edges
        self.reads = self.writes = 0

    def read32(self, addr):
        self.reads += 1
        if addr == GLOBAL_ID:
            return ID_MAGIC
        if addr == GLOBAL_INFO:
            return self.info
        e, off = self.engines[addr // ENGINE_STRIDE], addr % ENGINE_STRIDE
        if off == REG["STATUS"]:
            return e.status()
        if off == REG["STOP_POS"]:
            return e.stop_pos()
        if off == REG["STOP_REG"]:
            return e.stop_reg()
        if off == REG["POSITIONS"]:
            return e.positions
        if off == REG["CYCLES"]:
            return e.cycles() if hasattr(e, "cycles") else 12345
        if off == REG["PARAMS"]:
            return self.max_edges | 6 << 8 | 2 << 16
        if off in DEMO_READ_REGS or REG["SNAP"] <= off < REG["SNAP"] + 0x100:
            return e.demo_read(off)
        for name in ("ORDER", "OPTIONS", "EDGE_EN"):
            if off == REG[name]:
                return e.regs[name]
        if REG["EDGE"] <= off < REG["EDGE"] + 0x100:
            return e.edges[(off - REG["EDGE"]) // 4]
        return 0

    def write32(self, addr, value):
        self.writes += 1
        e, off = self.engines[addr // ENGINE_STRIDE], addr % ENGINE_STRIDE
        if off == REG["CTRL"]:
            if value & CTRL_START:
                e.start()
            if value & CTRL_ABORT:
                e.abort()
            if value & CTRL_STEP:
                e.ctrl_step()
            return
        if off in (REG["PACE"], REG["DEMO"]):
            e.demo_write(off, value)
            return
        for name in ("ORDER", "OPTIONS", "EDGE_EN"):
            if off == REG[name]:
                e.regs[name] = value
                return
        if REG["EDGE"] <= off < REG["EDGE"] + 0x100:
            idx = (off - REG["EDGE"]) // 4
            if idx >= self.max_edges:
                raise AssertionError(f"edge index {idx} beyond MAX_EDGES")
            e.edges[idx] = value

    def close(self):
        pass


def decode_order(word):
    """Inverse of driver.encode_order -> christopher WheelOrder (for the software-backed fake)."""
    from bombe_fpga.driver import REFLECTOR_IDS, ROTOR_IDS
    from christopher.bombe import WheelOrder

    rot = {v: k for k, v in ROTOR_IDS.items()}
    refl = {v: k for k, v in REFLECTOR_IDS.items()}
    greek = rot[word >> 4 & 15] if word >> 2 & 1 else None
    return WheelOrder(refl[word & 3], (rot[word >> 8 & 15], rot[word >> 12 & 15], rot[word >> 16 & 15]), greek)


def software_sweep(menu, model="enigma-i"):
    """A ``sweep`` callback that decodes the engine registers and runs the software bombe."""
    from christopher.bombe import Bombe

    def sweep(order_word, options_word, edges):
        diagonal = bool(options_word >> 16 & 1)
        turnover = options_word >> 24 & 0xFF if options_word >> 17 & 1 else None
        want = tuple((ord(e.plain) - 65) | (ord(e.cipher) - 65) << 8 | e.pos << 16 for e in menu.edges)
        assert edges == want, "edge registers do not encode the menu"
        assert options_word & 31 == ord(menu.test_letter) - 65
        bombe = Bombe(model, diagonal).plug_up(menu)
        return [(s._pos, s.test_register) for s in bombe.run(decode_order(order_word), turnover_at=turnover)]

    return sweep


def software_snapshot(menu, model="enigma-i"):
    """A ``snapshot`` callback: the software bombe's full 676-wire state (``Bombe.trace``) of a position."""
    from christopher.bombe import Bombe, _drum_letters

    cache = {}

    def snapshot(order_word, options_word, edges, pos):
        diagonal = bool(options_word >> 16 & 1)
        turnover = options_word >> 24 & 0xFF if options_word >> 17 & 1 else None
        bombe = cache.setdefault(diagonal, Bombe(model, diagonal).plug_up(menu))
        order = decode_order(order_word)
        return bombe.trace(order, _drum_letters(order, pos), None, False, turnover)

    return snapshot


# ---------------------------------------------------------------------------------------------------
# Time-driven fake of the exhibit mode: a sweep that runs at the paced speed in wall-clock time.
# ---------------------------------------------------------------------------------------------------
def position_of(index: int, greek: bool) -> tuple:
    """Core offsets (g, l, m, r) of the index-th position of a sweep (greek outermost, right innermost)."""
    r, rest = index % 26, index // 26
    m, rest = rest % 26, rest // 26
    return (rest // 26 if greek else 0, rest % 26, m, r)


class DemoEngine(FakeEngine):
    """An engine that really sweeps positions as time passes (PACE, DEMO, snapshot, stops).

    ``sweep(order, options, edges) -> [((g, l, m, r), test_register), ...]`` supplies the stops;
    ``snapshot(order, options, edges, pos) -> [26 cable masks]`` supplies the 676 wires of a position.
    ``clock()`` is wall time in seconds (injectable for tests); ``fast_rate`` caps the speed of an
    unpaced (PACE = 0) sweep so that the model stays cheap.
    """

    def __init__(self, sweep, snapshot, clock=time.monotonic, clock_mhz=50, fast_rate=50_000.0, fifo_depth=64):
        super().__init__(sweep, 0, fifo_depth)
        self.snapshot_fn, self.clock, self.clock_mhz, self.fast_rate = snapshot, clock, clock_mhz, fast_rate
        self.running = False
        self.idx = -1                  # index of the last finished position
        self.n_positions = 26 ** 3
        self.stop_at = {}              # index -> test register
        self.t_ref = self.t_start = 0.0
        self.cache = (None, None)      # (index, cables)
        self.cfg = (0, 0, ())

    @property
    def greek(self):
        return bool(self.regs["ORDER"] >> 2 & 1)

    def pos(self, idx):
        return position_of(idx, self.greek)

    def _held(self):
        single = bool(self.regs["DEMO"] & DEMO_SINGLE)
        pause = bool(self.regs["DEMO"] & DEMO_PAUSE_ON_STOP)
        return self.running and (single or (pause and self.idx in self.stop_at))

    def _finish(self, idx):
        """Positions idx_old+1 .. idx are done: queue their stops."""
        idx = min(idx, self.n_positions - 1)
        for i in range(self.idx + 1, idx + 1):
            if i in self.stop_at:
                self.fifo.append((self.pos(i), self.stop_at[i]))
        self.idx = idx

    def _rate(self):
        pace = self.regs["PACE"]
        return self.fast_rate if pace == 0 else min(self.fast_rate, self.clock_mhz * 1e6 / (pace + POSITION_CYCLES))

    def tick(self):
        if not self.running:
            return
        now = self.clock()
        if self._held():
            self.t_ref = now
            return
        rate = self._rate()
        n = int((now - self.t_ref) * rate)
        if n <= 0:
            return
        self.t_ref += n / rate
        target = self.idx + n
        if self.regs["DEMO"] & DEMO_PAUSE_ON_STOP:
            nxt = min((i for i in self.stop_at if i > self.idx), default=None)
            if nxt is not None and nxt <= target:
                target = nxt
        if target >= self.n_positions - 1:
            self._finish(self.n_positions - 1)
            self.running, self.done = False, True
        else:
            self._finish(target)

    # --- register behaviour ----------------------------------------------------------------------
    def start(self):
        order, opt, en = self.regs["ORDER"], self.regs["OPTIONS"], self.regs["EDGE_EN"]
        edges = tuple(self.edges[i] for i in range(32) if en >> i & 1)
        self.log.append((order, opt, edges))
        self.cfg = (order, opt, edges)
        self.n_positions = 26 ** (4 if self.greek else 3)
        self.stop_at = {}
        for (g, l, m, r), reg in self.sweep(order, opt, edges):
            self.stop_at[(((g if self.greek else 0) * 26 + l) * 26 + m) * 26 + r] = reg
        self.fifo.clear()
        self.idx, self.done, self.running = -1, False, True
        self.t_ref = self.t_start = self.clock()
        self._finish(0)

    def abort(self):
        self.running, self.done = False, True

    def ctrl_step(self):
        self.tick()
        if not self._held():
            return
        if self.idx >= self.n_positions - 1:
            self.running, self.done = False, True
        else:
            self._finish(self.idx + 1)
            self.t_ref = self.clock()

    def status(self):
        self.tick()
        st = (1 if self.running else 0) | (2 if self.done else 0) | (4 if self.fifo else 0)
        return st | (ST_HOLD if self._held() else 0)

    @property
    def positions(self):
        self.tick()
        return self.idx + 1

    @positions.setter
    def positions(self, _):          # FakeEngine.start() assigns it; this class computes it
        pass

    def cycles(self):
        if self.idx < 0:
            return 0
        return int((self.clock() - self.t_start) * self.clock_mhz * 1e6) & 0xFFFFFFFF

    def cables(self):
        if self.idx < 0:
            return [0] * 26
        if self.cache[0] != self.idx:
            self.cache = (self.idx, list(self.snapshot_fn(*self.cfg, self.pos(self.idx))))
        return self.cache[1]

    def demo_read(self, off):
        self.tick()
        if off in (REG["PACE"], REG["DEMO"]):
            return super().demo_read(off)
        if off == REG["DRUM"]:
            g, l, m, r = self.pos(max(self.idx, 0))
            return g << 20 | l << 15 | m << 10 | r << 5
        if off == REG["TESTREG"]:
            return self.cables()[self.cfg[1] & 31] if self.idx >= 0 else 0
        if off == REG["SNAP_POS"]:
            if self.idx < 0:
                return 0
            g, l, m, r = self.pos(self.idx)
            return 1 << 31 | int(self.idx in self.stop_at) << 30 | g << 20 | l << 15 | m << 10 | r << 5
        if off == REG["SNAP_ITERS"]:
            return 2 + sum(c.bit_count() for c in self.cables()) // 90 if self.idx >= 0 else 0
        if off >= REG["SNAP"]:
            c = (off - REG["SNAP"]) // 4
            return self.cables()[c] if c < 26 and self.idx >= 0 else 0
        return 0


class FakeDemoArray(FakeArray):
    """``FakeArray`` whose engines are time-driven :class:`DemoEngine` s (for the visualizer)."""

    def __init__(self, sweep, snapshot, n_engines=1, max_edges=16, clock_mhz=50, **kw):
        super().__init__(sweep, n_engines, max_edges, 0, 64, clock_mhz)
        self.engines = [DemoEngine(sweep, snapshot, clock_mhz=clock_mhz, **kw) for _ in range(n_engines)]


def synthetic_model(stop_every=1237):
    """(sweep, snapshot) pair that needs no software bombe: pseudo-random but deterministic wires and
    a stop every ``stop_every`` positions.  Shows the display only; the numbers mean nothing."""

    def sweep(order_word, options_word, edges):
        greek = bool(order_word >> 2 & 1)
        out = []
        for i in range(stop_every - 7, 26 ** (4 if greek else 3), stop_every):
            test = options_word & 31
            out.append((position_of(i, greek), (1 << test) | (1 << ((i * 7) % 26))))
        return out

    def snapshot(order_word, options_word, edges, pos):
        g, l, m, r = pos
        x = (((g * 26 + l) * 26 + m) * 26 + r) * 2654435761 + 12345
        letters = {e & 31 for e in edges} | {e >> 8 & 31 for e in edges} | {options_word & 31}
        cab = [0] * 26
        for c in sorted(letters):
            x = (x * 1103515245 + 12345) & 0xFFFFFFFF
            cab[c] = x & ((x >> 7) | (x >> 13)) & 0x3FFFFFF
        cab[options_word & 31] |= 1 << (options_word >> 8 & 31)
        return cab

    return sweep, snapshot
