"""Test bench around the sequencer netlist: register, operator, counter board, idle timer, thermostat (stdlib only).

The bench replaces what is NOT on the sequencer board:
  * the test register (decision 0001 / 0003): timing relays K27-K29 behind the connector (d_on 2.5-4 ms, d_off 1.5-4 ms),
    sense relay / pass relay / stop relay S at the contact face (operate times 2.5-5 ms), RUN / STOP_LATCH outputs,
    RELEASE button (50 ms or longer), start switch;
  * the counter board (counter-board README 2.5 / 4): CK/NCK/LOAD at the connector face, advance on a CK pulse of
    5 ms or longer, POS lines of the r digit and the m-carry rail A1B, HOME preset rules, shifter transition windows;
  * the idle timer (external on-delay relay, 120 s +-10 %) and the thermostat.
The stop decision of the register is replaced by an ORACLE (set of positions where the register must stop).
"""
from __future__ import annotations

N_POS = 17576
SHIFT_MS = 13.0        # CK rise -> shifter contacts settled: slave 4 + relay 4 + shifter 5 (counter README 4, shifter README)
CK_MIN = 5.0
WATCH = ("STROBE", "JUDGE", "CLEAR", "STOPPED", "AT_HOME", "END", "CK_A", "NCK_A", "LOAD_A", "CK_B", "NCK_B", "LOAD_B", "TMRC")
EXT = ("RUN", "STOP_LATCH", "HOME", "POS10_IN", "POS13_IN", "POS14_IN", "A1B", "TMR_RET", "HT_C")


class TimingRelay:
    """K27 / K28 / K29 of the register: connector level -> contact level with random delays."""

    def __init__(self, bench, cb, on=(2.5, 4.0), off=(1.5, 4.0)):
        self.b, self.cb, self.on, self.off = bench, cb, on, off
        self.level = 0
        self.conn = 0
        self.pend = None

    def set(self, t, lv):
        self.conn = lv
        if lv == self.level:
            self.pend = None
            return
        d = self.b.sim.draw(*(self.on if lv else self.off))
        tok = object()
        self.pend = tok

        def fire(tok=tok, lv=lv):
            if self.pend is tok:
                self.pend = None
                self.level = lv
                self.cb(self.b.sim.t, lv)
        self.b.sim.schedule(t + d, "cb", fire)


class Bench:
    def __init__(self, sim, oracle=None, auto_release=True, release_delay=(80.0, 400.0)):
        self.sim = sim
        self.rng = sim.rng
        self.oracle = oracle if oracle is not None else set()
        self.auto_release = auto_release
        self.release_delay = release_delay
        self.errors: list[str] = []
        self.notes: list[str] = []
        sim.watch_names = set(WATCH) | {"RUN", "STOP_LATCH", "HOME"}
        sim.on_change = self.on_change
        for k in EXT:
            sim.ext[k] = False
        # register
        self.tr = {"STROBE": TimingRelay(self, self.s_contact), "JUDGE": TimingRelay(self, self.j_contact),
                   "CLEAR": TimingRelay(self, self.c_contact)}
        self.start = False
        self.stopped = False          # S latched
        self.k34 = False
        self.valid_strobe = False
        self.s_rise = self.s_fall = self.j_rise = self.j_fall = self.c_rise = self.c_fall = None
        self.k_ok = False
        # sense relays K (on +24V_L: they survive an emergency stop): held since the last effective CLEAR / release, and
        # "full" = every one of the 26 lines latched (a position without a stop).  A held "full" set that was never cleared
        # makes the next position look full as well -> its stop is MISSED (sequencer README 11).
        self.k_held = False
        self.k_full = False
        self.stale_missed = 0
        self.p_time = None
        self.stop_pending = None
        self.stops: list[dict] = []
        self.false_stops = 0
        self.missed_stops = 0
        self.l8_false = 0
        self.contact_viol: list[str] = []
        self.release_events = 0
        self.stop_latch_rise_t = None
        # counters
        self.n = 0
        self.valid = False
        self.n_pending = None
        self.ck_rise = None
        self.nck = 1
        self.ck = 0
        self.lowlow = 0.0
        self.load = 0
        self.load_rise = None
        self.load_fall = None
        self.released = True          # power-up: the counters' state is undefined until a HOME preset
        self.preset_ok = False
        self.pos_changes: list[tuple] = []
        self.shift_windows: list[tuple] = []
        self.ck_pulses: list[tuple] = []
        self.advances = 0
        self.tested: list[tuple] = []
        self.home_log: list[tuple] = []
        self.pos_events: list[tuple] = []
        self.strobe_high_since = None
        self.timer_tok = None
        self.hot = False
        self.update_run()
        self.drive_pos()

    # ------------------------------------------------------------------ register
    def update_run(self):
        self.sim.ext["RUN"] = bool(self.start and not self.stopped and not self.k34)
        self.sim.ext["STOP_LATCH"] = bool(self.stopped) and not getattr(self, "sl_hold_low", False)

    def set_start(self, on):
        self.start = on
        self.update_run()

    def s_contact(self, t, lv):
        if lv:
            if self.stopped:
                return
            if self.c_fall is not None and self.c_rise is not None and t < self.c_fall:
                self.contact_viol.append(f"t={t:.1f} C6: STROBE contact rise before the CLEAR contact fall")
            self.s_rise = t
        else:
            if self.stopped or self.s_rise is None:
                return
            w = t - self.s_rise
            kop = self.sim.draw(2.5, 5.0)
            self.k_ok = w >= kop
            if w < 5.0:
                self.contact_viol.append(f"t={t:.1f} C1: STROBE contact width {w:.2f} < 5.0")
            self.p_time = self.s_rise + kop + self.sim.draw(2.5, 5.0)
            self.valid_strobe = self.k_ok
            if self.k_ok:
                full_here = self.current_test_pos not in self.oracle
                self.k_full = (self.k_full if self.k_held else False) or full_here
                self.k_held = True
            self.s_fall = t

    def j_contact(self, t, lv):
        if lv:
            self.j_rise = t
            if self.stopped:
                return
            if self.valid_strobe and self.s_rise is not None and t < self.s_rise + 10.0 - 1e-9:
                self.contact_viol.append(f"t={t:.1f} C2: JUDGE contact starts {t - self.s_rise:.2f} after the STROBE contact (< 10)")
            pos = self.current_test_pos
            oracle_stop = pos in self.oracle
            line_stop = oracle_stop
            if oracle_stop and self.valid_strobe and self.k_held and self.k_full:
                # sense relays still latched from an earlier position: the register sees 26 live lines -> no stop
                line_stop = False
                self.stale_missed += 1
                self.missed_stops += 1
                self.errors.append(f"t={t:.1f} MISSED STOP at position {pos}: sense relays still latched from an earlier "
                                   f"position (no CLEAR since)")
            stop = line_stop or (not self.valid_strobe) or (self.p_time is not None and t < self.p_time)
            if not oracle_stop and stop:
                if not self.valid_strobe and self.empty_since_clear:
                    self.l8_false += 1
                    self.errors.append(f"t={t:.1f} L8: JUDGE on an empty register (no STROBE since the last clear) -> false stop")
                else:
                    self.false_stops += 1
                    self.errors.append(f"t={t:.1f} false stop at position {pos} (K/P not ready: STROBE or JUDGE timing)")
            if stop:
                sop = self.sim.draw(2.5, 5.0)
                tok = object()
                self.stop_pending = (tok, oracle_stop, pos)

                def op(tok=tok):
                    if self.stop_pending and self.stop_pending[0] is tok:
                        self.stop_pending = None
                        self.s_operated(oracle_stop, pos)
                self.sim.schedule(t + sop, "cb", op)
        else:
            self.j_fall = t
            if self.stop_pending is not None:
                tok, oracle_stop, pos = self.stop_pending
                self.stop_pending = None
                if oracle_stop:
                    self.missed_stops += 1
                    self.errors.append(f"t={t:.1f} MISSED STOP at position {pos}: JUDGE contact too short for the stop relay")
            if self.j_rise is not None and not self.stopped and t - self.j_rise < 5.0:
                self.contact_viol.append(f"t={t:.1f} C3: JUDGE contact width {t - self.j_rise:.2f} < 5.0")

    def c_contact(self, t, lv):
        if lv:
            self.c_rise = t
            if self.j_fall is not None and self.j_rise is not None and self.j_fall < self.j_rise:
                self.contact_viol.append(f"t={t:.1f} C4: CLEAR contact starts while the JUDGE contact is still closed")
            self.valid_strobe = False
            self.empty_since_clear = True
        else:
            self.c_fall = t
            if self.c_rise is not None and t - self.c_rise < 10.0 and not self.stopped:
                self.contact_viol.append(f"t={t:.1f} C5: CLEAR contact width {t - self.c_rise:.2f} < 10.0")
            elif self.c_rise is not None and not self.stopped:
                self.k_held = self.k_full = False        # hold bus was open long enough: K and P released

    empty_since_clear = True
    current_test_pos = 0

    def s_operated(self, oracle_stop, pos):
        t = self.sim.t
        self.stopped = True
        self.stop_latch_rise_t = t + self.rng.uniform(0.0, 0.8)
        self.stops.append(dict(t=t, pos=pos, oracle=oracle_stop))
        self.sim.ext["RUN"] = False
        self.sim.schedule(self.stop_latch_rise_t, "cb", self.update_run)
        if self.auto_release:
            d = self.rng.uniform(*self.release_delay)
            self.sim.schedule(t + d, "cb", self.press_release)

    def press_release(self):
        t = self.sim.t
        self.k34 = True
        self.update_run()
        self.release_events += 1
        dur = self.rng.uniform(50.0, 120.0)
        k34_op = self.sim.draw(2.5, 5.0)

        def clear_all():
            self.stopped = False
            self.valid_strobe = False
            self.k_held = self.k_full = False
            self.empty_since_clear = True
            self.stop_pending = None
            self.sl_hold_low = False
            self.sim.ext["STOP_LATCH"] = False
            self.sim.ext["RUN"] = False
        self.sim.schedule(t + k34_op + self.sim.draw(3.0, 10.0), "cb", clear_all)

        def let_go():
            self.k34 = False
            self.sim.schedule(self.sim.t + self.sim.draw(3.0, 10.0), "cb", self.update_run)
        self.sim.schedule(t + dur, "cb", let_go)

    # ------------------------------------------------------------------ counters
    def drive_pos(self):
        r = (self.n // 676) % 26
        e = self.sim.ext
        e["POS10_IN"] = bool(r & 1)
        e["POS13_IN"] = bool(r & 8)
        e["POS14_IN"] = bool(r & 16)

    def jump(self, n):
        """BACKDOOR (tests only): put the counters at position n."""
        self.n = n % N_POS
        self.drive_pos()
        self.pos_events.append((self.sim.t, self.n))

    armed = True

    def on_ck_edge(self, t, bus, name, lv):
        if not self.armed:
            if name == "NCK_A" and lv:
                self.armed = True
                self.lowlow = None
            return
        ck, nck = self.sim.levels_prev.get("CK_A", 0), self.sim.levels_prev.get("NCK_A", 0)
        if bus != "A":
            return
        if ck and nck:
            self.errors.append(f"t={t:.2f} CK and NCK are both high")
        both_low = (not ck) and (not nck)
        if both_low:
            if self.lowlow is None:
                self.lowlow = t
        else:
            if self.lowlow is not None:
                gap = t - self.lowlow
                self.lowlow = None
                self.on_gap_end(t, gap, name)

    def on_gap_end(self, t, gap, name):
        if gap >= 3.0:
            if gap >= 10.0:
                self.released = True
                self.preset_ok = False
                self.valid = False
                self.pos_unknown = True
            self.notes.append(f"t={t:.1f} clock supply was off for {gap:.1f} ms")
            self.gaps.append((t, gap))
        else:
            self.gaps_short.append(gap)
        if name == "NCK_A" and self.released:
            ok = self.load and self.load_rise is not None and t - self.load_rise >= 5.0
            if not ok and self.load_fall is not None and t - self.load_fall < 3.0 and self.load_len >= 5.0:
                ok = True
            if ok:
                self.valid = True
                self.released = False
                self.n = 0
                self.drive_pos()
                self.pos_events.append((t, 0))
                self.shift_windows.append((t, t + SHIFT_MS))
                self.home_log.append((t, "preset ok"))
            else:
                self.errors.append(f"t={t:.1f} counters restarted after a release without a valid LOAD preset")
                self.released = False
                self.valid = False

    gaps: list = []
    gaps_short: list = []
    load_len = 0.0
    pos_unknown = False

    def on_change(self, t, name, lv):
        if name in self.tr:
            self.tr[name].set(t, lv)
            if name == "STROBE":
                if lv:
                    self.strobe_high_since = t
                    if self.sim.levels_prev.get("CK_A", 0):
                        self.errors.append(f"t={t:.1f} STROBE rises while CK is high")
                    for (a, b) in self.shift_windows[-3:]:
                        if t < b and t >= a - 1e-9:
                            self.errors.append(f"t={t:.1f} STROBE rises {b - t:.1f} ms before the shifters are settled (L9)")
                    if self.n_pending is not None:
                        self.errors.append(f"t={t:.1f} STROBE rises while the position is changing")
                    self.current_test_pos = self.n
                    self.tested.append((t, self.n))
                    self.empty_since_clear = False
                else:
                    self.strobe_high_since = None
        elif name in ("CK_A", "NCK_A", "LOAD_A", "CK_B", "NCK_B", "LOAD_B"):
            bus = name[-1]
            if name.startswith("CK_") and bus == "A":
                if lv:
                    self.ck_rise = t
                    self.a1b_on = (self.n % 26 == 25 and (self.n // 26) % 26 == 25)
                    if self.a1b_on:
                        self.sim.ext["A1B"] = True
                    self.shift_windows.append((t, t + SHIFT_MS))
                    if self.sim.levels_prev.get("STROBE", 0):
                        self.errors.append(f"t={t:.1f} CK rises while STROBE is high")
                    # position changes after the slave operate time
                    d = self.sim.draw(2.5, 5.0)
                    tok = object()
                    self.n_pending = tok

                    def adv(tok=tok):
                        if self.n_pending is tok:
                            self.n_pending = None
                            self.n = (self.n + 1) % N_POS
                            self.advances += 1
                            self.drive_pos()
                    self.sim.schedule(t + d, "cb", adv)
                elif self.ck_rise is not None:
                    w = t - self.ck_rise
                    self.ck_pulses.append((self.ck_rise, w))
                    self.sim.ext["A1B"] = False
                    if w < CK_MIN:
                        self.errors.append(f"t={t:.1f} CK pulse {w:.2f} ms < 5 ms")
            if name == "LOAD_A":
                if lv:
                    self.load = 1
                    self.load_rise = t
                    if self.sim.levels_prev.get("CK_A", 0):
                        self.errors.append(f"t={t:.1f} LOAD rises while CK is high")
                else:
                    self.load = 0
                    self.load_fall = t
                    self.load_len = t - (self.load_rise or t)
            self.on_ck_edge(t, bus, name, lv)
        elif name == "TMRC":
            self.timer_tok = None
            if lv:
                tok = object()
                self.timer_tok = tok
                d = 120000.0 * self.rng.uniform(0.9, 1.1)

                def fire(tok=tok):
                    if self.timer_tok is tok:
                        self.sim.ext["TMR_RET"] = True
                self.sim.schedule(t + d, "cb", fire)
            else:
                self.sim.ext["TMR_RET"] = False
        elif name == "STOP_LATCH":
            pass

    # ------------------------------------------------------------------ panel actions (scenarios)
    def home_press(self, t, dur=150.0):
        self.sim.schedule(t, "cb", lambda: self.sim.ext.__setitem__("HOME", True))
        self.sim.schedule(t + dur, "cb", lambda: self.sim.ext.__setitem__("HOME", False))

    def at(self, t, fn):
        self.sim.schedule(t, "cb", fn)
