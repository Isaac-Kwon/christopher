#!/usr/bin/env python3
"""Executable specification of the Pico firmware of the gear control board (drive control only).

The firmware is NOT in the STROBE / JUDGE / CLEAR path (that path is the hardware gating of the LOGIC sheet). It
  * generates the stepper step ramp (enable / step / dir of a closed-loop drive: the drive has no ramp of its own),
  * runs the state machine  REST / RUN / STOPDEC / CREEPOUT / RET / HOME*  (stop zone, return, restart, HOME, END),
  * counts the position-disc edges (T = one pulse per step of an axis, I = index sector) into l, m, r -> POS0-14,
  * enforces the 48 V restart rule (RUNARM) and inhibits the timing outputs (TXEN = 0) during RETURN and HOME.

Units: steps, steps/s, ms.  `Firmware.tick(t_ms, dt_ms)` returns the signed number of step pulses of this tick; the
harness delivers sensor edges through `on_edge(sig, level, t_ms, s_edge)` in the order of the motion (like GPIO
interrupts).  `mut` is a set of mutation names used only by the negative tests of verify_control.py.

Geometry assumptions (README 2): 400 steps per cell, pad centre of cell c at x = 400 c, the connector-face slot windows
STROBE [-26, +47), JUDGE [+99, +182), CLEAR [+198, +333) steps around the centre (0.312 deg/ms, 10.4 steps/ms at 60 rpm).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


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
    ret_offset: int = 20            # steps after the end of CLEAR (zone: +333 .. +374 of the next cell, 41 steps wide)
    cell: int = 400
    period_x: int = 12000 * 676     # one full period of the position discs (17,576 positions + carry cells)
    home_offset: int = 50           # steps after the AT_HOME rising edge (sector start = x -120) -> x = -70
    end_pos: int = 17575            # last tested position (slice end: any 26 k + 25 ... only at the end of a revolution)
    home_hold_ms: float = 100.0     # HOME level must be held this long (decision 0003 4.3)


def pos_of(l: int, m: int, r: int) -> int:
    return l + 26 * m + 676 * r


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

    def x_now(self):
        if self.x_ref is None or not all(self.sync):
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
            if self._all_i() and level and self.state == "HSEARCH":
                self.hsearch_edge_s = s_e
                self.state = "HDEC"
        elif sig in ("T_L", "T_M", "T_S"):
            ax = "LMS".index(sig[2])
            ilev = self.lv["I_" + sig[2]]
            if fwd and level:
                if not ilev:
                    self.cnt[ax] = (self.cnt[ax] + 1) % 26
                if ax == 0:
                    self._xref(self.cnt[0] if not ilev else 0, s_e)
            elif (not fwd) and (not level):
                old = self.cnt[ax]
                if not ilev:
                    self.cnt[ax] = (self.cnt[ax] - 1) % 26
                if ax == 0:
                    self._xref(old if not ilev else 1, s_e)
        elif sig == "SLOT_S" and level and self.txen:
            self.in_cycle = True
        elif sig == "SLOT_C":
            if not level:
                self.in_cycle = False
            if level:
                self.cell_pos = tuple(self.cnt)
            else:
                if self.cap_armed and self.s_clear_end is None:
                    self.s_clear_end = s_e
                if (fwd and self.cell_pos is not None and all(self.sync) and not self.end_flag
                        and pos_of(*self.cell_pos) == self.p.end_pos):
                    self.end_pending = True

    def _xref(self, kb, s_e):
        if not all(self.sync):
            return
        l, m, r = self.cnt
        self.x_ref = (self.p.cell * (30 * (m + 26 * r) + kb) - 120, s_e)

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
        elif st == "HBACK":
            n = self._motion(-p.v_hcreep, p.a_home, dt)
            if not self._all_i():
                self.state = "HSEARCH"
        elif st == "HSEARCH":
            n = self._motion(p.v_home, p.a_home, dt)
        elif st == "HDEC":
            n = self._motion(0.0, p.a_home, dt)
            if self.v == 0.0:
                self.target = self.hsearch_edge_s + p.home_offset
                self.state = "HGO"
        elif st == "HGO":
            n, arrived = self._goto(self.target, p.v_hcreep, p.a_home, dt)
            if arrived:
                self._home_done()
        elif st == "HMOVE":
            n, arrived = self._goto(self.target, p.v_home, p.a_home, dt)
            if arrived:
                if self._all_i():
                    self._home_done()
                else:
                    self.home_how += "+search"
                    self.state = "HSEARCH"
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
        self.homing = False

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
        xn = self.x_now()
        self.home_how = ""
        if xn is not None and "forward_only" not in self.mut:
            x_home = -120 + self.p.home_offset
            fwd = (x_home - xn) % self.p.period_x
            if fwd <= self.p.period_x // 2:
                self.target = self.s + fwd
                self.home_how = "fwd"
            else:
                self.target = self.s - (self.p.period_x - fwd)
                self.home_how = "rev"
            self.state = "HMOVE"
            return
        self.home_how = "search"
        self.state = "HBACK" if self._all_i() else "HSEARCH"

    def _home_done(self):
        self.v = 0.0
        self.state = "REST"
        self.homing = False
        self.txen = True
        self.stopped = True
        self.at_home = True
        self.need_home = False
        self.cnt = [0, 0, 0]
        self.sync = [True, True, True]
        self.x_ref = (-120, self.s - self.p.home_offset)
