#!/usr/bin/env python3
"""Executable specification of the Pico firmware of the gear control board (drive control only).

The firmware is NOT in the STROBE / JUDGE / CLEAR path (that path is the hardware gating of the LOGIC sheet). It
  * generates the stepper step ramp (enable / step / dir of a closed-loop drive: the drive has no ramp of its own),
  * runs the state machine  REST / RUN / STOPDEC / CREEPOUT / RET / HOME*  (stop zone, return, restart, HOME, END),
  * counts the position-disc edges (T = one pulse per step of an axis, I = index sector) into l, m, r -> POS0-14,
  * enforces the 48 V restart rule (RUNARM) and inhibits the timing outputs (TXEN = 0) during RETURN and HOME, except the
    HOME flush: the final HOME approach (HFLUSH) starts one slotted cell before home and enables TXEN from the end of that
    cell's JUDGE slot to the end of its CLEAR slot -> exactly one CLEAR, no STROBE / JUDGE (decision 0003 4.4).

Units: steps, steps/s, ms.  `Firmware.tick(t_ms, dt_ms)` returns the signed number of step pulses of this tick; the
harness delivers sensor edges through `on_edge(sig, level, t_ms, s_edge)` in the order of the motion (like GPIO
interrupts).  `mut` is a set of mutation names used only by the negative tests of verify_control.py.

Geometry (README 1.2, hardware/gear/layout.md 3.7 / 3.8, CAD hardware/gear/cad):
  * x = cycle-shaft angle in motor steps, 400 steps per position (cell), 30 cells per cycle-shaft revolution (12 deg = 400
    steps, 0.03 deg per step).  x = 400 c is the STROBE CONTACT START of cell c (t = 0 of decision 0001; the pad centre is at
    +31.2 steps).  Cell c = 30 Q + k: k = 0..25 have slots (tested), k = 26..29 are the empty carry cells.
  * Slot windows (connector face): from the CAD single source hardware/gear/cad/params.py (SLOT_STEPS), read below.
  * Position discs (CAD: one 26-slot + index disc on each of F, M, S):
      F turns continuously, 30 positions per cycle-shaft revolution (26:30), so T_L gives one edge in EVERY cell (also the
      empty ones) at x = 400 c - 120 (= +280 of the previous cell) and I_L is high while the fast drum shows letter 0
      (cells c = 0 mod 26).  The fast drum of tested cell (Q, k) is l = (30 Q + k) mod 26 = (4 m + k) mod 26: its starting
      letter moves by 4 every cycle (concept.md 5.2, the "30-point" order).
      M steps once per revolution during carry 1 (cycle shaft 330 deg = cell 27.5): T_M edge at x = 12000 j + 11000, which
      is also the phase mark of the cycle shaft for the cell counter k.  S steps when M passes 25 -> 0.
  * Test order: n = 26 Q + k (Q = m + 26 r), the order in which the machine tests the 17,576 positions.  END and the
    stop oracle use n; POS0-14 carries the core position (l, m, r) of decision 0003 4.5.
"""
from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path


def _cad_params():
    """hardware/gear/cad/params.py (pure Python, no CadQuery needed): the single source of the slot angles."""
    path = Path(__file__).resolve().parent.parent / "cad" / "params.py"
    spec = importlib.util.spec_from_file_location("gear_cad_params", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CAD = _cad_params()
CELL = CAD.STEPS_PER_POS                  # 400 motor steps per position
CYCLE_CELLS = CAD.CYCLE_N                 # 30 cells per cycle-shaft revolution, k = 26..29 empty
SLOT_W = dict(CAD.SLOT_STEPS)             # {"STROBE": (a, b), "JUDGE": ..., "CLEAR": ...} steps from the STROBE contact start
T_OFF = -120                              # position-disc T edge of cell c at x = CELL c + T_OFF (+280 of the previous cell)
CARRY_X = 27 * CELL + CELL // 2           # 11000: T_M edge (carry 1 at 330 deg) inside the cycle-shaft revolution
CYCLE_X = CYCLE_CELLS * CELL              # 12000 steps per cycle-shaft revolution


@dataclass
class Params:
    v_run: float = 10400.0          # 60 rpm of the fast axis: 26 positions/s x 400 steps (calculated)
    a_run: float = 8.0e5            # steps/s^2 (about 1.1 N.m at the motor shaft, estimated)
    a_stop: float = 1.2e6
    v_creep: float = 2000.0
    a_creep: float = 2.0e5
    v_ret: float = 3000.0
    a_ret: float = 2.0e5
    v_home: float = 20800.0         # HOME speed: 2 x the run speed (parameter)
    a_home: float = 8.0e5
    v_hcreep: float = 3000.0
    ret_offset: int = 10            # steps after the sensed end of CLEAR (zone CLEAR end .. next STROBE rise = 330 .. 374,
                                    # 44 steps; worst case about 20 steps with the slowest calibrated STROBE relay)
    cell: int = CELL
    period_x: int = CYCLE_X * 676   # one full period: 676 cycle-shaft revolutions = 17,576 tested positions + carry cells
    home_x: int = -70               # HOME position: inside cell 0 of cycle 0, before its STROBE slot
    flush_back: int = (CYCLE_CELLS - 25) * CELL   # HOME flush: the final approach starts this far before home_x, i.e. at the
                                    # same place in the last slotted cell before home (k = 25 of the previous revolution, 2000 steps)
    v_flush: float = 3000.0         # final approach speed: the flush CLEAR slot (136 steps) lasts 45 ms (L5 needs <= 10,880 pps)
    x_strobe: int = SLOT_W["STROBE"][0]   # STROBE slot rise inside a cell (-26)
    end_pos: int = 17575            # last tested position in test order n (slice end: 26 Q + 25 at the end of a revolution)
    home_hold_ms: float = 100.0     # HOME level must be held this long (decision 0003 4.3)


def pos_of(l: int, m: int, r: int) -> int:
    """lexicographic index of the core position (l, m, r)"""
    return l + 26 * m + 676 * r


def test_index(l: int, m: int, r: int) -> int:
    """test order n = 26 Q + k of the tested cell whose core position is (l, m, r): Q = m + 26 r, l = (4 m + k) mod 26"""
    return 26 * (m + 26 * r) + (l - 4 * m) % 26


class Firmware:
    def __init__(self, p: Params | None = None, mut=()):
        self.p = p or Params()
        self.mut = set(mut)
        # ---- inputs (set by the harness)
        self.run = self.stop_latch = self.home = False
        self.v48 = self.sw_ok = False
        self.alm = False
        self.lv = {"SLOT_S": False, "SLOT_J": False, "SLOT_C": False, "I_L": False, "I_M": False, "I_S": False,
                   "T_L": False, "T_M": False, "T_S": False}
        # ---- outputs
        self.ena = False
        self.txen = False
        self.homing = False
        self.stopped = False
        self.at_home = False
        self.end = False
        self.dir_fwd = True
        # ---- motion
        self.v = 0.0
        self.s = 0
        self._frac = 0.0
        self.state = "REST"
        # ---- bookkeeping
        self.runarm = False
        self.need_home = True
        self.cnt = [0, 0, 0]
        self.sync = [False, False, False]
        self.kc = 0                  # cell k (0..29) inside the cycle-shaft revolution: +1 per T_L edge, = 27 at the T_M edge
        self.kc_sync = False
        self.x_ref = None            # (x of the last T_L boundary, s at that edge)
        self.stop_kind = None        # 'latch' | 'oper' | 'end'
        self.cap_armed = False
        self.s_clear_end = None
        self.cell_pos = None         # (l, m, r) latched at the rising edge of CLEAR
        self.end_flag = False
        self.end_pending = False
        self.hold_disp = False
        self.in_cycle = False
        self.ret_target = None
        self.home_t = 0.0
        self.home_req_since = None
        self.hsearch_edge_s = None
        self.target = None
        self.move_v = 0.0
        self.move_a = 0.0
        self.home_how = ""
        self.flushed = False         # HOME flush CLEAR done on the final approach (decision 0003 4.4)
        self.goal_x = None           # x the current HOME move (HMOVE / HFLUSH) must reach (step target corrected at T_L edges)
        self.log: list[str] = []

    # ------------------------------------------------------------------ helpers
    @property
    def pos(self) -> tuple[int, int, int]:
        """POS bus value (decision 0003 4.5): while standing after a stop / END it is the stop position n (the T boundary of
        the next cell lies before the stop zone), while moving it is the live counters (valid STROBE-1 ms .. CLEAR rise)."""
        if self.hold_disp and self.cell_pos is not None and self.state == "REST":
            return tuple(self.cell_pos)
        return tuple(self.cnt)

    def _all_i(self) -> bool:
        return self.lv["I_L"] and self.lv["I_M"] and self.lv["I_S"]

    def _in_cycle0(self) -> bool:
        """inside cycle-shaft revolution 0 (M = S = 0): an unsynced HOME search backs out of it first"""
        if "home_index_only" in self.mut:            # negative test: the rule of the 26-cell model (three index sectors)
            return self._all_i()
        return self.lv["I_M"] and self.lv["I_S"]

    def x_now(self):
        if self.x_ref is None or not all(self.sync) or not self.kc_sync:
            return None
        return self.x_ref[0] + (self.s - self.x_ref[1])

    # ------------------------------------------------------------------ sensor edges (interrupts)
    def on_edge(self, sig: str, level: bool, t: float, s_e: int):
        self.lv[sig] = level
        fwd = self.v >= 0 if abs(self.v) > 1e-9 else self.dir_fwd
        if sig in ("I_L", "I_M", "I_S"):
            ax = "LMS".index(sig[2])
            if level:
                self.cnt[ax] = 0
                self.sync[ax] = True
            if "home_index_only" in self.mut and level and fwd and self.state == "HSEARCH" and self._all_i():
                self.hsearch_edge_s = s_e + (self.p.x_strobe - T_OFF)     # negative test: home on the index sectors alone
                self.state = "HDEC"
        elif sig in ("T_L", "T_M", "T_S"):
            ax = "LMS".index(sig[2])
            ilev = self.lv["I_" + sig[2]]
            if fwd and level:
                if not ilev:
                    self.cnt[ax] = (self.cnt[ax] + 1) % 26
                if ax == 0:
                    self.kc = (self.kc + 1) % CYCLE_CELLS
                    self._xref(self.kc, s_e)
                    self._retarget(s_e)
                elif ax == 1:                       # carry 1 = cycle shaft at 330 deg, inside cell k = 27
                    self.kc, self.kc_sync = 27, True
            elif (not fwd) and (not level):
                if not ilev:
                    self.cnt[ax] = (self.cnt[ax] - 1) % 26
                if ax == 0:
                    old = self.kc                   # leaving cell `old` backwards
                    self.kc = (self.kc - 1) % CYCLE_CELLS
                    self._xref(old, s_e)
                    self._retarget(s_e)
                elif ax == 1:
                    self.kc, self.kc_sync = 27, True
        elif sig == "SLOT_S":
            if level and self.txen:
                self.in_cycle = True
            # HOME mark = STROBE slot rising while the three index sectors are high.  The F index alone is ambiguous inside
            # cycle 0: the fast drum shows letter 0 in cell 0 AND in the empty cell 26 (30 cells per revolution); only cell 0
            # carries a slot.
            if level and fwd and self.state == "HSEARCH" and self._all_i() and "home_index_only" not in self.mut:
                self.hsearch_edge_s = s_e
                self.state = "HDEC"
        elif sig == "SLOT_J":
            # HOME flush (decision 0003 4.4): on the final approach the timing outputs are enabled from the end of the JUDGE slot
            # of the last slotted cell (its STROBE slot has passed with TXEN = 0; HOMING keeps SEEN cleared) ...
            if (not level) and fwd and self.state == "HFLUSH" and not self.flushed and "no_flush" not in self.mut:
                self.txen = True
        elif sig == "SLOT_C":
            if not level and fwd and self.state == "HFLUSH" and self.txen and not self.flushed:
                self.txen = False           # ... to the end of its CLEAR slot: exactly one CLEAR, then nothing until AT_HOME
                self.flushed = True
            if not level:
                self.in_cycle = False
            if level:
                self.cell_pos = tuple(self.cnt)
            else:
                if self.cap_armed and self.s_clear_end is None:
                    self.s_clear_end = s_e
                if (fwd and self.cell_pos is not None and all(self.sync) and not self.end_flag and not self.homing
                        and test_index(*self.cell_pos) == self.p.end_pos):      # (the HOME flush passes the last cell: not END)
                    self.end_pending = True

    def _xref(self, k, s_e):
        """absolute x of the T_L edge that bounds cell k (entered forwards / left backwards) from the counters"""
        if not (all(self.sync) and self.kc_sync):
            return
        l, m, r = self.cnt
        q = (m + 26 * r - (1 if k >= 28 else 0)) % 676       # cells 28, 29 lie after carry 1: M already counts Q + 1
        self.x_ref = (self.p.cell * (CYCLE_CELLS * q + k) + T_OFF, s_e)

    def _retarget(self, s_e):
        """HOME moves (HMOVE, HFLUSH): correct the step target at every T_L boundary.  After an emergency stop the shaft coasted
        with the drive off (steps not counted), so the step reference is off by up to the coast distance; the first boundary of
        the HOME move gives the true x again.  Only small corrections (< one cell) are taken: a larger one means the counters are
        wrong, which the index check at the end of HOME catches."""
        if self.state not in ("HMOVE", "HFLUSH") or self.goal_x is None or self.x_ref is None or "no_retarget" in self.mut:
            return
        per = self.p.period_x
        rem_x = self.goal_x - self.x_ref[0]
        rem_s = self.target - s_e
        corr = (rem_x - rem_s + per // 2) % per - per // 2
        if abs(corr) < self.p.cell:
            self.target += corr

    # ------------------------------------------------------------------ the 1 ms ... 50 us control tick
    def _motion(self, vd: float, a: float, dt: float) -> int:
        dv = vd - self.v
        lim = a * dt / 1000.0
        self.v += max(-lim, min(lim, dv))
        if abs(self.v) < 1e-6 and vd == 0.0:
            self.v = 0.0
        self._frac += self.v * dt / 1000.0
        n = int(self._frac)
        self._frac -= n
        self.s += n
        if n != 0:
            self.dir_fwd = n > 0
        return n

    def _goto(self, target: int, vmax: float, a: float, dt: float):
        """move to the absolute step target with a trapezoid profile; returns (steps, arrived)"""
        rem = target - self.s
        if rem == 0 and abs(self.v) < 1.0:
            self.v = 0.0
            return 0, True
        d = 1 if rem > 0 else -1
        vd = d * min(vmax, math.sqrt(2.0 * a * max(abs(rem) - 0.5, 0.0)))
        n = self._motion(vd, a, dt)
        if (target - self.s) * d < 0:          # never overshoot (closed-loop drive: exact step count)
            n -= self.s - target
            self.s = target
            self.v = 0.0
            self._frac = 0.0
        if self.s == target and abs(self.v) < 0.5 * a * dt / 1000.0 + 1.0:
            self.v = 0.0
            return n, True
        return n, False

    def tick(self, t: float, dt: float) -> int:
        p = self.p
        ok48 = self.v48 and self.sw_ok
        was_sw = getattr(self, "_was_sw", False)
        was_48 = getattr(self, "_was_48", False)
        # ---- 48 V restart rule
        if "no_runarm" in self.mut:
            self.runarm = True
        elif not ok48:
            self.runarm = False
        elif not self.run:
            self.runarm = True
        # ---- power loss: drive torque gone, firmware forgets the motion
        self.ena = ok48 and not self.alm
        if (was_sw and not self.sw_ok) or (was_48 and not self.v48):
            self._lost(sw=(was_sw and not self.sw_ok))
        self._was_sw, self._was_48 = self.sw_ok, self.v48
        if not self.ena:
            self.v = 0.0
            self._frac = 0.0
            return 0
        n = 0
        st = self.state
        pr = self
        # ---- stop-latch handling (level, rising edge seen as STOP_LATCH level change)
        if self.stop_latch and not getattr(self, "_sl_prev", False):
            if self.state in ("RUN", "STOPDEC", "CREEPOUT"):
                self.stop_kind = "latch"
                self.cap_armed = True
                self.s_clear_end = None
                if self.state == "RUN":
                    self.state = "STOPDEC"
        self._sl_prev = self.stop_latch
        # ---- HOME request: HOME held >= 100 ms with RUN = STOP_LATCH = 0 while standing
        if self.home and not self.run and not self.stop_latch and self.state == "REST":
            if self.home_req_since is None:
                self.home_req_since = t
            if t - self.home_req_since >= p.home_hold_ms:
                self._start_home()
                self.home_req_since = None
        else:
            self.home_req_since = None
        st = self.state
        if st == "REST":
            if (self.run and self.runarm and not self.end_flag and not self.need_home and not self.stop_latch
                    ):
                if self.end_pending and not self.end_flag:
                    self.end_flag, self.end, self.end_pending = True, True, False
                else:
                    self.state = "RUN"
                    self.hold_disp = False
                    self.stopped = False
                    self.at_home = False
                    self.dir_fwd = True
                    self.txen = True
            n = self._motion(0.0, p.a_stop, dt) if self.v != 0.0 else 0
        elif st == "RUN":
            if not self.run or not ok48:
                self.state = "STOPDEC"
                self.stop_kind = self.stop_kind or "oper"
            elif self.end_pending:
                self.state = "STOPDEC"
                self.stop_kind = "end"
            n = self._motion(p.v_run, p.a_run, dt)
        elif st == "STOPDEC":
            if self.end_pending and self.stop_kind != "latch":
                self.stop_kind = "end"
            n = self._motion(0.0, p.a_stop, dt)
            if self.v == 0.0:
                self.state = "CREEPOUT"
        elif st == "CREEPOUT":
            done_clear = (self.stop_kind != "latch") or (self.s_clear_end is not None)
            slot_any = self.lv["SLOT_S"] or self.lv["SLOT_J"] or self.lv["SLOT_C"]
            if self.stop_kind == "latch":
                if self.s_clear_end is None:
                    n = self._motion(p.v_creep, p.a_creep, dt)       # forward until CLEAR has ended
                else:
                    self.ret_target = self.s_clear_end + p.ret_offset
                    if "no_txen_return" not in self.mut and self.ret_target < self.s:
                        self.txen = False                              # reverse: no timing outputs (4.4 (2))
                    self.state = "RET"
            else:
                if slot_any or self.in_cycle:                     # operator stop / END: stand between two cycles
                    n = self._motion(p.v_creep, p.a_creep, dt)
                else:
                    n = self._motion(0.0, p.a_creep, dt)
                    if self.v == 0.0:
                        self._rest_after_stop()
        elif st == "RET":
            n, arrived = self._goto(self.ret_target, p.v_ret, p.a_ret, dt)
            if arrived:
                self._rest_after_stop()
        elif st == "HBACK":                          # started inside cycle 0: back out of it (at most one revolution)
            n = self._motion(-p.v_hcreep, p.a_home, dt)
            if not self._in_cycle0():
                self.state = "HSEARCH"
        elif st == "HSEARCH":
            n = self._motion(p.v_home, p.a_home, dt)
        elif st == "HDEC":
            n = self._motion(0.0, p.a_home, dt)
            if self.v == 0.0:
                self.target = self.hsearch_edge_s + (p.home_x - p.flush_back - p.x_strobe)
                self.state = "HGO"
        elif st == "HGO":
            n, arrived = self._goto(self.target, p.v_hcreep, p.a_home, dt)
            if arrived:
                self._start_flush()
        elif st == "HMOVE":
            n, arrived = self._goto(self.target, p.v_home, p.a_home, dt)
            if arrived:
                self._start_flush()
        elif st == "HFLUSH":
            # final approach: one slotted cell forward at <= 60 rpm; TXEN only from the JUDGE end to the CLEAR end (on_edge)
            n, arrived = self._goto(self.target, p.v_flush, p.a_home, dt)
            if arrived:
                self.txen = False
                if not self._all_i():                   # counters were wrong: search the home mark
                    self.home_how += "+search"
                    self.state = "HBACK" if self._in_cycle0() else "HSEARCH"
                elif self.flushed or "no_flush" in self.mut:
                    self._home_done()
                else:                                   # the slot edges of the flush were not seen: pass that cell again
                    self.home_how += "+reflush"
                    self.target = self.s - p.flush_back
                    self.state = "HGO"
        # ---- outputs updated every tick
        return n

    # ------------------------------------------------------------------ transitions
    def _lost(self, sw: bool):
        self.v = 0.0
        self._frac = 0.0
        self.state = "REST"
        self.stopped = True
        self.stop_kind = None
        self.cap_armed = False
        self.s_clear_end = None
        if sw:
            self.need_home = True        # emergency stop: the cycle may be cut anywhere -> HOME first
            self.txen = False
        # after an emergency stop HOMING stays high until HOME is done: it holds SEEN and the cycle hold CYC cleared, so a cycle
        # cut by the stop emits nothing when +24V_SW returns or while HOME moves (the HOME flush CLEAR replaces its CLEAR)
        self.homing = (sw or self.need_home) and "estop_no_homing" not in self.mut

    def _rest_after_stop(self):
        self.v = 0.0
        self.txen = True
        self.stopped = True
        self.hold_disp = self.stop_kind in ("latch", "end") or self.end_pending
        if self.stop_kind == "end" or self.end_pending:
            self.end_flag = self.end = True
            self.end_pending = False
        self.stop_kind = None
        self.cap_armed = False
        self.s_clear_end = None
        self.state = "REST"

    def _start_home(self):
        self.hold_disp = False
        self.homing = True
        self.txen = False
        self.stopped = False
        self.at_home = False
        self.end = False
        self.end_flag = self.end_pending = False
        self.need_home = True
        self.flushed = False
        xn = self.x_now()
        self.home_how = ""
        if xn is not None and "forward_only" not in self.mut:
            x_home = self.p.home_x - self.p.flush_back          # the final approach (HFLUSH) starts here
            fwd = (x_home - xn) % self.p.period_x
            if fwd <= self.p.period_x // 2:
                self.target = self.s + fwd
                self.home_how = "fwd"
            else:
                self.target = self.s - (self.p.period_x - fwd)
                self.home_how = "rev"
            self.goal_x = x_home
            self.state = "HMOVE"
            return
        self.goal_x = None
        self.home_how = "search"
        self.state = "HBACK" if self._in_cycle0() else "HSEARCH"

    def _start_flush(self):
        """standing one slotted cell before home (same place in the cell): creep forward to home with HOMING = 1 and TXEN = 0;
        on_edge enables the outputs for the CLEAR slot of that cell only (HOME flush CLEAR, decision 0003 4.4)"""
        self.txen = False
        self.flushed = False
        self.target = self.s + self.p.flush_back
        self.goal_x = self.p.home_x
        self.state = "HFLUSH"

    def _home_done(self):
        self.v = 0.0
        self.state = "REST"
        self.homing = False
        self.txen = True
        self.stopped = True
        self.at_home = True
        self.need_home = False
        self.end_pending = False
        self.cnt = [0, 0, 0]
        self.sync = [True, True, True]
        self.kc, self.kc_sync = 0, True
        self.x_ref = (self.p.home_x, self.s)
