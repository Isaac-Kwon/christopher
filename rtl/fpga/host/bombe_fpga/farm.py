"""Distribute many wheel orders over all engines of the array."""

from __future__ import annotations

import time
from collections import deque

from bombe_fpga.driver import FpgaBombe, config_from


class Farm:
    """Keeps every engine busy: when one finishes, the next pending wheel order is loaded into it."""

    def __init__(self, fpga: FpgaBombe, poll_delay: float = 0.0):
        self.fpga = fpga
        self.poll_delay = poll_delay
        self.positions_tested = 0     #: sum of the engines' POSITIONS counters (cf. Bombe.positions_tested)
        self.max_cycles = 0           #: longest single sweep in clock cycles

    def run_raw(self, menu, orders, diagonal_board=True, turnover_at=None, timeout=None, progress=None):
        """Run each WheelOrder; returns ``[[(core_position, register), ...] per order]``."""
        engines = self.fpga.engines
        if not engines:
            raise RuntimeError("array reports 0 engines")
        results = [[] for _ in orders]
        pending = deque(enumerate(orders))
        active = {}                      # engine index -> order index
        done_n = 0
        self.positions_tested = self.max_cycles = 0
        t0 = time.monotonic()
        while pending or active:
            for eng in engines:
                if eng.index not in active and pending:
                    k, order = pending.popleft()
                    eng.configure(config_from(menu, order, diagonal_board, turnover_at, self.fpga.max_edges))
                    eng.start()
                    active[eng.index] = k
            for eng in engines:
                k = active.get(eng.index)
                if k is None:
                    continue
                stops, fin = eng.poll()
                results[k].extend(stops)
                if fin:
                    c = eng.counters()
                    self.positions_tested += c["positions"]
                    self.max_cycles = max(self.max_cycles, c["cycles"])
                    del active[eng.index]
                    done_n += 1
                    if progress:
                        progress(done_n, len(orders))
            if timeout and time.monotonic() - t0 > timeout:
                for eng in engines:
                    eng.abort()
                raise TimeoutError(f"farm timeout after {timeout}s ({done_n}/{len(orders)} orders done)")
            if self.poll_delay:
                time.sleep(self.poll_delay)
        return results

    def run(self, menu, wheel_orders="all", model="enigma-i", diagonal_board=True, turnover_at=None,
            timeout=None, progress=None):
        """Like ``christopher.Bombe.run``: stops of every order, in the order of ``wheel_orders``."""
        from christopher.bombe import Bombe, _expand_orders

        bombe = Bombe(model, diagonal_board).plug_up(menu)
        orders = _expand_orders(wheel_orders, bombe.model)
        raw = self.run_raw(menu, orders, diagonal_board, turnover_at, timeout, progress)
        return [bombe._stop(o, pos, reg, turnover_at) for o, stops in zip(orders, raw) for pos, reg in stops]
