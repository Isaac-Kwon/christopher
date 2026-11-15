"""A register-level fake of ``bombe_array`` for hardware-free tests.

``FakeArray`` implements the Transport interface (read32/write32) and the register map of
rtl/fpga/README.md.  The sweep itself is delegated to ``sweep(order_word, options_word, edges)``
(returns ``[((g, l, m, r), register), ...]``), so tests can plug in the software bombe or scripted
stops.  A sweep takes ``busy_polls`` STATUS reads to finish, to exercise the polling loop.
"""

from __future__ import annotations

from collections import deque

from bombe_fpga.driver import ENGINE_STRIDE, GLOBAL_ID, GLOBAL_INFO, ID_MAGIC, REG


class FakeEngine:
    def __init__(self, sweep, busy_polls, fifo_depth):
        self.sweep, self.busy_polls, self.fifo_depth = sweep, busy_polls, fifo_depth
        self.regs = {"ORDER": 0, "OPTIONS": 0, "EDGE_EN": 0}
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
            return 12345
        if off == REG["PARAMS"]:
            return self.max_edges | 6 << 8
        for name in ("ORDER", "OPTIONS", "EDGE_EN"):
            if off == REG[name]:
                return e.regs[name]
        if off >= REG["EDGE"]:
            return e.edges[(off - REG["EDGE"]) // 4]
        return 0

    def write32(self, addr, value):
        self.writes += 1
        e, off = self.engines[addr // ENGINE_STRIDE], addr % ENGINE_STRIDE
        if off == REG["CTRL"]:
            if value & 1:
                e.start()
            if value & 2:
                e.busy, e.pending = 0, deque()
                e.done = True
            return
        for name in ("ORDER", "OPTIONS", "EDGE_EN"):
            if off == REG[name]:
                e.regs[name] = value
                return
        if off >= REG["EDGE"]:
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
