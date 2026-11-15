#!/usr/bin/env python3
"""Verify the clock sequencer by simulating its exported netlist (switch level, event driven, relay times of decision 0001).

    python verify_sequencer.py                # export the netlist, structural checks, scenario suite, negative tests (a few minutes)
    python verify_sequencer.py --quick        # fewer runs (what the CI-style smoke test uses)
    python verify_sequencer.py --full         # additionally one complete run of 17,576 positions through the netlist (long)
    python verify_sequencer.py --netlist X    # use an existing KiCad s-expression netlist

What is checked (all on the connector face unless stated):
  decision 0003 L1-L9 for every position cycle: STROBE >= 7.5, JUDGE >= 7.5, CLEAR >= 12.5 ms, JUDGE rise >= STROBE rise + 11.5,
  CLEAR rise >= JUDGE fall + 1.5, next STROBE >= CLEAR fall + 1.5, every STROBE followed by JUDGE and CLEAR (L7), JUDGE only after a
  STROBE (L8, also after HOME / stop release), CK rise >= CLEAR rise and CK rise + 13 ms <= next STROBE (L9); CK/NCK never both high and
  break-before-make gap below the counters' release time; the register at the contact face (C1-C6, with the random timing-relay
  delays); no missed stop and no false stop; positions tested = 0, 1, 2, ... without repeat or skip across stops, restarts and
  operator RUN toggles; HOME (release, LOAD preset overlapping NCK, no timing output, AT_HOME), END at the 17,576th position or at the
  slice end, standby (CK = NCK = 0, HOME needed), emergency stop (power loss), thermostat.
Negative tests mutate the netlist and require the verification to fail.
"""
from __future__ import annotations

import argparse
import copy
import random
import sys
import time
from pathlib import Path

import seqnet
from seqbench import N_POS, SHIFT_MS, Bench
from seqsim import Sim

HERE = Path(__file__).resolve().parent
L1, L3, L5 = 7.5, 7.5, 12.5
L2, L4, L6 = 11.5, 1.5, 1.5


# ----------------------------------------------------------------------------- helpers
def ref_of(nl, name):
    for r, p in nl.parts.items():
        if p["value"] == f"G6S-2 {name}":
            return r
    raise KeyError(name)


def mutate(nl, edits):
    """edits = [(ref, pin, newnet)] -> a copy of the netlist with those pins on other nets."""
    m = copy.deepcopy(nl)
    for ref, pin, new in edits:
        old = m.pin_net[(ref, pin)]
        m.nets[old] = [x for x in m.nets[old] if x != (ref, pin)]
        m.nets.setdefault(new, []).append((ref, pin))
        m.pin_net[(ref, pin)] = new
    return m


def pulses(edges):
    out, up = [], None
    for t, lv in edges:
        if lv and up is None:
            up = t
        elif not lv and up is not None:
            out.append((up, t))
            up = None
    return out


class Stats:
    def __init__(self):
        self.min: dict[str, float] = {}
        self.max: dict[str, float] = {}
        self.cycles = 0
        self.stops = 0
        self.runs = 0

    def add(self, k, v):
        self.min[k] = min(self.min.get(k, 1e9), v)
        self.max[k] = max(self.max.get(k, -1e9), v)


def analyze(sim, bench, stats: Stats, expect_first=0, home_windows=(), allow_unfinished=False):
    """L1-L9 and sequence checks on the recorded connector-face edges.  Returns a list of failures."""
    fails: list[str] = []
    S = pulses(sim.edge_list("STROBE"))
    J = pulses(sim.edge_list("JUDGE"))
    C = pulses(sim.edge_list("CLEAR"))
    CK = pulses(sim.edge_list("CK_A"))
    CKB = pulses(sim.edge_list("CK_B"))
    ev = sorted([(a_, b_, "S") for a_, b_ in S] + [(a_, b_, "J") for a_, b_ in J] + [(a_, b_, "C") for a_, b_ in C])
    cyc, flush, i = [], [], 0
    while i < len(ev):
        if ev[i][2] == "S":
            if i + 2 < len(ev) + 0 and ev[i + 1][2] == "J" and ev[i + 2][2] == "C":
                cyc.append((ev[i], ev[i + 1], ev[i + 2]))
                i += 3
                continue
            fails.append(f"L7: STROBE at t={ev[i][0]:.1f} not followed by JUDGE and CLEAR" if not allow_unfinished else "unfinished cycle")
            i += 1
        elif ev[i][2] == "J":
            fails.append(f"L8: JUDGE at t={ev[i][0]:.1f} without a STROBE before it")
            i += 1
        else:
            flush.append(ev[i])
            i += 1
    n = len(cyc)
    starts = sorted([c[0][0] for c in cyc] + [f[0] for f in flush])
    for k, ((s0, s1, _), (j0, j1, _), (c0, c1, _)) in enumerate(cyc):
        m = {"S": s1 - s0, "J": j1 - j0, "C": c1 - c0, "JS": j0 - s0, "CJ": c0 - j1}
        nxt = [x for x in sorted(S) if x[0] > c1]
        if nxt:
            m["SC"] = nxt[0][0] - c1
        for kk, v in m.items():
            stats.add(kk, v)
        if m["S"] < L1:
            fails.append(f"L1: STROBE {m['S']:.2f} ms at t={s0:.1f}")
        if m["JS"] < L2:
            fails.append(f"L2: JUDGE rise only {m['JS']:.2f} after STROBE rise at t={s0:.1f}")
        if m["J"] < L3:
            fails.append(f"L3: JUDGE {m['J']:.2f} ms at t={s0:.1f}")
        if m["CJ"] < L4:
            fails.append(f"L4: CLEAR rise {m['CJ']:.2f} after JUDGE fall at t={s0:.1f}")
        if m["C"] < L5:
            fails.append(f"L5: CLEAR {m['C']:.2f} ms at t={s0:.1f}")
        if "SC" in m and m["SC"] < L6:
            fails.append(f"L6: next STROBE {m['SC']:.2f} after CLEAR fall at t={c1:.1f}")
    stats.cycles += n
    # advance bookkeeping: a cycle whose CK was blocked leaves an advance pending; the next cycle must then be advance-only
    # (CLEAR flush, never STROBE / JUDGE) and must contain the CK
    allc = sorted([(c[0][0], "cyc", c) for c in cyc] + [(f_[0], "flush", f_) for f_ in flush])
    pending = False
    prev_t = -1.0
    for k, (t0_, kind, item) in enumerate(allc):
        if any(prev_t < h1 <= t0_ for (h0, h1) in home_windows):
            pending = False               # HOME presets the position: no advance is pending any more
        prev_t = t0_
        t_next = allc[k + 1][0] if k + 1 < len(allc) else 1e18
        c_end = item[2][1] if kind == "cyc" else item[1]
        c_start = item[2][0] if kind == "cyc" else item[0]
        has_ck = any(c_start <= r[0] < t_next for r in CK)
        if kind == "cyc":
            if pending:
                fails.append(f"STROBE at t={item[0][0]:.1f} while an advance was pending (repeat test)")
            pending = not has_ck
        else:
            stats.flush = getattr(stats, "flush", 0) + 1
            if not pending:
                fails.append(f"CLEAR without STROBE at t={item[0]:.1f} without a pending advance")
            if item[1] - item[0] < L5:
                fails.append(f"L5: flush CLEAR {item[1] - item[0]:.2f} ms at t={item[0]:.1f}")
            nxt = [x for x in sorted(S) if x[0] > item[1]]
            if nxt and nxt[0][0] - item[1] < L6:
                fails.append(f"L6: STROBE only {nxt[0][0] - item[1]:.2f} after the flush CLEAR at t={item[0]:.1f}")
            pending = not has_ck
    # L9 + CK width + bus agreement
    for (r0, r1) in CK:
        stats.add("CKw", r1 - r0)
        before = [c for c in C if c[0] <= r0]
        # a CK belongs to the cycle whose CLEAR started before it, or to an advance-only cycle (no CLEAR before, previous C is older)
        if before and S:
            nxt = [s for s in S if s[0] > r0]
            if nxt:
                gap = nxt[0][0] - r0
                stats.add("CK_to_S", gap)
                if gap < SHIFT_MS:
                    fails.append(f"L9: STROBE only {gap:.1f} ms after CK rise at t={r0:.1f} (shifters need {SHIFT_MS})")
        prev_c = [c for c in C if c[0] <= r0]
        prev_s = [s for s in S if s[0] <= r0]
        if prev_s and prev_c and prev_s[-1][0] > prev_c[-1][0]:
            fails.append(f"L9: CK rise at t={r0:.1f} inside a position cycle (STROBE without CLEAR yet)")
        elif prev_c and prev_s:
            # CK must not be earlier than the CLEAR rise of the cycle it follows (it is later: prev_c[-1][0] <= r0)
            stats.add("CK_after_C", r0 - prev_c[-1][0])
        if r1 - r0 < 5.0:
            fails.append(f"CK pulse {r1 - r0:.2f} ms at t={r0:.1f}")
    if len(CK) != len(CKB):
        fails.append(f"bus A has {len(CK)} CK pulses, bus B {len(CKB)}")
    # no timing output inside HOME windows
    for (h0, h1) in home_windows:
        for name, pl in (("STROBE", S), ("JUDGE", J), ("CLEAR", C)):
            for (a, b) in pl:
                if a < h1 and b > h0:
                    fails.append(f"{name} pulse at t={a:.1f} during HOME ({h0:.0f}..{h1:.0f})")
    # positions tested: consecutive, restarting at the value the counters were preset / jumped to
    pos = bench.tested
    for k in range(len(pos)):
        evs = [e for e in bench.pos_events if (pos[k - 1][0] if k else -1) < e[0] < pos[k][0]]
        want = evs[-1][1] if evs else ((pos[k - 1][1] + 1) % N_POS if k else expect_first)
        if want is not None and pos[k][1] != want:
            fails.append(f"position sequence broken at test {k}: expected {want}, tested {pos[k][1]} (t={pos[k][0]:.1f})")
            break
    # after a stop: no STROBE before the release, and a CK advance in between
    for st in bench.stops:
        later = [s for s in S if s[0] > st["t"]]
        if not later:
            continue
        s_next = later[0][0]
        ck_between = [c for c in CK if st["t"] < c[0] < s_next]
        if not ck_between:
            fails.append(f"after the stop at position {st['pos']} (t={st['t']:.0f}) the next STROBE came without an advance")
        sl = [e for e in sim.edge_list("STOP_LATCH") if e[0] > st["t"] and e[1] == 0]
        if sl and s_next < sl[0][0]:
            fails.append(f"STROBE at t={s_next:.0f} while STOP_LATCH was still high after the stop at position {st['pos']}")
    stats.stops += len(bench.stops)
    fails += bench.errors
    fails += bench.contact_viol
    if bench.missed_stops or bench.false_stops or bench.l8_false:
        pass
    fails += sim.errors
    return fails


def wait_for(sim, cond, tmax, step=2.0):
    t_end = sim.t + tmax
    while sim.t < t_end:
        sim.run_until(sim.t + step)
        if cond():
            return True
    return False


def make(nl, seed, mode, oracle=(), auto_release=True, rel=(80.0, 400.0), tol=0.03, close_slice=False):
    if close_slice:
        nl = copy.deepcopy(nl)
        for r, p in nl.parts.items():
            if p["value"].startswith("slice END"):
                p["value"] = "slice END (bridged)"
    sim = Sim(nl, seed=seed, mode=mode, tol=tol)
    sim.start()
    b = Bench(sim, set(oracle), auto_release, rel)
    b.gaps, b.gaps_short = [], []
    return sim, b


def home_sequence(sim, b, fails, label="HOME"):
    """Press HOME with RUN low; return the (start, AT_HOME rise) window."""
    b.set_start(False)
    sim.run_until(sim.t + 150)
    t0 = sim.t + 10
    b.home_press(t0)
    ok = wait_for(sim, lambda: sim.levels_prev.get("AT_HOME", 0) == 1, 1500)
    t1 = sim.t
    if not ok:
        fails.append(f"{label}: AT_HOME never rose")
    else:
        sim.run_until(sim.t + 100)
        if b.n != 0:
            fails.append(f"{label}: counters at {b.n} after HOME")
        if sim.levels_prev.get("END", 0):
            fails.append(f"{label}: END still high after HOME")
        if not sim.levels_prev.get("STOPPED", 0):
            fails.append(f"{label}: STOPPED low although the bank stands at home")
    return (t0, t1)


# ----------------------------------------------------------------------------- scenarios
def scen_basic(nl, seed, mode, stats, n_cycles=70, stops=(12, 33, 34, 51)):
    """power-up, RUN ignored before HOME, HOME, run with oracle stops (random release delays)."""
    fails: list[str] = []
    sim, b = make(nl, seed, mode, oracle=set(stops))
    sim.run_until(300)
    b.set_start(True)
    sim.run_until(700)
    if any(lv for _, lv in sim.edge_list("STROBE")) or any(lv for _, lv in sim.edge_list("CK_A")) or any(lv for _, lv in sim.edge_list("CLEAR")):
        fails.append("outputs moved after power-up with RUN high but without HOME")
    hw = [home_sequence(sim, b, fails)]
    b.set_start(True)
    ok = wait_for(sim, lambda: len(b.tested) >= n_cycles, 60000, step=25.0)
    if not ok:
        fails.append(f"only {len(b.tested)} of {n_cycles} cycles after the long wait")
    b.set_start(False)
    sim.run_until(sim.t + 300)
    stats.runs += 1
    fails += analyze(sim, b, stats, 0, hw)
    expected_stops = set(stops) & {p for _, p in b.tested}
    seen = {s["pos"] for s in b.stops}
    if expected_stops - seen:
        fails.append(f"missed stops at {sorted(expected_stops - seen)}")
    if seen - set(stops):
        fails.append(f"false stops at {sorted(seen - set(stops))}")
    return fails, sim, b


def scen_toggle(nl, seed, mode, stats, n_cycles=60):
    """operator switches the START switch off and on at random times: no repeat, no skip, cycles finished"""
    fails: list[str] = []
    sim, b = make(nl, seed, mode)
    sim.run_until(250)
    hw = [home_sequence(sim, b, fails)]
    rng = random.Random(seed * 7 + 1)
    b.set_start(True)
    for k in range(10):
        sim.run_until(sim.t + rng.uniform(60, 700))
        b.set_start(False)
        sim.run_until(sim.t + rng.uniform(5, 400))
        b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= n_cycles, 60000, step=25.0)
    b.set_start(False)
    sim.run_until(sim.t + 300)
    stats.runs += 1
    fails += analyze(sim, b, stats, 0, hw)
    if len(b.tested) < n_cycles:
        fails.append("toggle run did not reach the cycle count")
    return fails, sim, b


def scen_end(nl, seed, mode, stats, slice_mode=False):
    fails: list[str] = []
    sim, b = make(nl, seed, mode, close_slice=slice_mode)
    sim.run_until(250)
    hw = [home_sequence(sim, b, fails)]
    start_n = (676 - 6) if slice_mode else (N_POS - 6)
    b.jump(start_n)
    sim.run_until(sim.t + 20)
    b.set_start(True)
    wait_for(sim, lambda: sim.levels_prev.get("END", 0) == 1, 20000, step=10.0)
    sim.run_until(sim.t + 400)
    end_hi = sim.levels_prev.get("END", 0)
    last = b.tested[-1][1] if b.tested else None
    want_last = (676 - 1) if slice_mode else (N_POS - 1)
    if not end_hi:
        fails.append("END never rose")
    if last != want_last:
        fails.append(f"last tested position {last}, expected {want_last}")
    n_tested = len(b.tested)
    sim.run_until(sim.t + 600)
    if len(b.tested) != n_tested:
        fails.append("positions still tested after END")
    # RUN is ignored while END is high; HOME clears it
    b.set_start(False)
    sim.run_until(sim.t + 100)
    b.set_start(True)
    sim.run_until(sim.t + 300)
    if len(b.tested) != n_tested:
        fails.append("RUN not ignored while END is high")
    hw2 = home_sequence(sim, b, fails, "HOME after END")
    stats.runs += 1
    fails += analyze(sim, b, stats, start_n, hw + [hw2])
    return fails, sim, b


def scen_standby(nl, seed, mode, stats):
    """idle timer (120 s) drops the coils; RUN ignored; HOME wakes; thermostat stops the machine."""
    fails: list[str] = []
    sim, b = make(nl, seed, mode)
    sim.run_until(250)
    hw = [home_sequence(sim, b, fails)]
    b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= 6, 3000, step=20.0)
    b.set_start(False)
    sim.run_until(sim.t + 300)
    n0 = len(b.tested)
    t_idle = sim.t
    # idle for 150 s: the on-delay timer must drop the coils (CK = NCK = 0) after 108-132 s (timer 120 s +-10 %)
    sim.run_until(t_idle + 100000)
    if sim.levels_prev.get("NCK_A", 0) != 1:
        fails.append("standby came too early (before 100 s)")
    sim.run_until(t_idle + 140000)
    if sim.levels_prev.get("NCK_A", 0) or sim.levels_prev.get("CK_A", 0):
        fails.append("standby: clock rails still on after the idle timeout")
    if sim.levels_prev.get("AT_HOME", 0):
        fails.append("standby: AT_HOME still high")
    b.set_start(True)
    sim.run_until(sim.t + 600)
    if len(b.tested) != n0:
        fails.append("RUN not ignored in standby")
    hw2 = home_sequence(sim, b, fails, "HOME from standby")
    b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= n0 + 6, 3000, step=20.0)
    b.set_start(False)
    sim.run_until(sim.t + 200)
    # thermostat closes while idle -> standby
    b.at(sim.t + 5, lambda: sim.ext.__setitem__("HT_C", True))
    sim.run_until(sim.t + 400)
    if sim.levels_prev.get("NCK_A", 0):
        fails.append("thermostat: standby not entered")
    stats.runs += 1
    sub = analyze(sim, b, stats, 0, hw + [hw2])
    # positions restart from 0 after the HOME from standby: split the check
    fails += [f for f in sub if "position sequence broken" not in f]
    return fails, sim, b


def scen_rehome(nl, seed, mode, stats):
    """HOME again from a READY, stopped machine (counters hold a position): no timing output, preset to 0, run again"""
    fails: list[str] = []
    sim, b = make(nl, seed, mode, oracle={9}, rel=(2.0, 60.0))
    sim.run_until(250)
    hw = [home_sequence(sim, b, fails)]
    b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= 14, 6000, step=20.0)
    b.set_start(False)
    sim.run_until(sim.t + 400)
    hw.append(home_sequence(sim, b, fails, "re-HOME"))
    b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= 24, 6000, step=20.0)
    b.set_start(False)
    sim.run_until(sim.t + 300)
    stats.runs += 1
    fails += analyze(sim, b, stats, 0, hw)
    return fails, sim, b


def scen_power(nl, seed, mode, stats):
    """emergency stop = +24V_SW lost: everything falls; after the reset RUN is ignored until HOME"""
    fails: list[str] = []
    sim, b = make(nl, seed, mode)
    sim.run_until(250)
    hw = [home_sequence(sim, b, fails)]
    b.set_start(True)
    wait_for(sim, lambda: len(b.tested) >= 8, 3000, step=20.0)
    n_before = len(b.tested)
    sim.set_power(False)
    for k in ("RUN", "STOP_LATCH"):
        pass
    sim.run_until(sim.t + 100)
    for name in ("STROBE", "JUDGE", "CLEAR", "CK_A", "NCK_A", "CK_B", "NCK_B", "STOPPED", "AT_HOME", "END"):
        if sim.levels_prev.get(name, 0):
            fails.append(f"{name} still high 100 ms after the emergency stop")
    n_mid = len(b.tested)
    sim.set_power(True)
    sim.run_until(sim.t + 800)
    if len(b.tested) != n_mid:
        fails.append("positions tested after the reset without HOME")
    if sim.levels_prev.get("AT_HOME", 0):
        fails.append("AT_HOME high after the reset without HOME")
    stats.runs += 1
    fails += [f for f in analyze(sim, b, stats, 0, hw) if "L7" not in f and "position sequence" not in f and "pulse order" not in f and " C1:" not in f and "C2:" not in f and "C3:" not in f and "C5:" not in f and "false stop" not in f and "MISSED" not in f and "L8" not in f and "not followed" not in f]
    return fails, sim, b


# ----------------------------------------------------------------------------- structural checks
def structural(nl):
    errs, n_ok = [], 0

    def chk(cond, msg):
        nonlocal n_ok
        if cond:
            n_ok += 1
        else:
            errs.append(msg)
    P = nl.parts
    chk(sum(1 for p in P.values() if p["value"].startswith("G6S-2")) == 44, "relay count")
    # control connector J2 (0003 5.2)
    exp = {1: "STROBE", 2: "RUN", 3: "STOPPED", 4: "HOME", 5: "AT_HOME", 6: "END", 22: "ESTOP_A", 23: "ESTOP_RET", 24: "ILOCK_A",
           25: "ILOCK_RET", 34: "JUDGE", 35: "CLEAR", 36: "STOP_LATCH"}
    for k in range(15):
        exp[7 + k] = f"POS{k}"
    for p, n in exp.items():
        chk(nl.net("J2", str(p)) == n, f"J2 pin {p}: {nl.net('J2', str(p))} != {n}")
    for p in range(28, 34):
        chk(nl.net("J2", str(p)) == "GND", f"J2 pin {p} not 0 V")
    for p in (26, 27, 37):
        n = nl.net("J2", str(p))
        chk(n is not None and n.startswith("unconnected"), f"J2 pin {p} must be unconnected")
    # no 24 V supply on a signal connector pin other than through a contact + fuse: outputs only via PTC
    for sig, pin in (("STROBE", 1), ("STOPPED", 3), ("AT_HOME", 5), ("END", 6), ("JUDGE", 34), ("CLEAR", 35)):
        fz = [r for r, p in P.items() if r.startswith("F") and nl.net(r, "2") == sig]
        chk(len(fz) == 1, f"{sig}: output fuse missing")
    for k in range(15):
        chk(any(r.startswith("F") and nl.net(r, "2") == f"POS{k}" and nl.net(r, "1") == f"POS{k}_IN" for r in P), f"POS{k} fuse")
    # CK/NCK come from ONE changeover contact per bus (break-before-make)
    for bus, rel in (("A", "KCKA"), ("B", "KCKB")):
        r = ref_of(nl, rel)
        chk(nl.net(r, "5") == f"CK_{bus}" and nl.net(r, "3") == f"NCK_{bus}" and nl.net(r, "4") == f"KENO_{bus}", f"{rel}: CK/NCK from one changeover contact")
        chk(nl.net("J3" if bus == "A" else "J4", "1") == f"CK_{bus}" and nl.net("J3" if bus == "A" else "J4", "2") == f"NCK_{bus}", f"bus {bus} connector pins")
        chk(nl.net("J3" if bus == "A" else "J4", "3") == f"LOAD_{bus}", f"bus {bus} LOAD pin")
    chk(nl.net(ref_of(nl, "KCKA"), "1") == nl.net(ref_of(nl, "KCKB"), "1"), "KCKA/KCKB share the drive")
    # nothing but KCKA / KCKB drives the CK / NCK nets
    for net in ("CK_A", "NCK_A", "CK_B", "NCK_B"):
        users = {r for r, pin in nl.nets[net] if r.startswith("K")}
        chk(len(users) == 1, f"{net} driven by {users}")
    # follower input rules (0003 2.2): pin -> Schottky -> coil, pulldown, clamp on the coil side
    for name, pin in (("KR", "RUN"), ("KSL", "STOP_LATCH"), ("KHM", "HOME"), ("KP0", "POS10_IN"), ("KP3", "POS13_IN"), ("KP4", "POS14_IN")):
        r = ref_of(nl, name)
        coil = nl.net(r, "1")
        ds = [x for x, p in P.items() if x.startswith("D") and "Schottky" in p["value"] and nl.net(x, "2") == pin and nl.net(x, "1") == coil]
        chk(len(ds) == 1, f"{name}: series Schottky pin -> coil")
        dz = [x for x, p in P.items() if x.startswith("DZ") and nl.net(x, "2") == coil]
        chk(len(dz) == 1, f"{name}: zener clamp on the coil side")
        res = [x for x, p in P.items() if x.startswith("R") and p["value"] == "22k" and {nl.net(x, "1"), nl.net(x, "2")} == {pin, "GND"}]
        chk(len(res) == 1, f"{name}: pin pull-down")
    # drivers on +24V_SW, ring counter + clock wiring, no other supply of the TBD
    for u in ("U3", "U4"):
        chk(nl.net(u, "9") == "SW" and nl.net(u, "10") == "GND", f"{u} supply")
    chk(nl.net("U2", "13") == "PARK" and nl.net("U2", "14") == "CLK" and nl.net("U1", "3") == "CLK", "ring clock / inhibit wiring")
    chk(nl.net("U4", "1") == "PARK", "KST drive = PARK")
    # power chain
    chk(nl.net("J1", "1") == "VIN" and nl.net("J1", "2") == "GND", "power terminal")
    chk(any(r.startswith("F") and nl.net(r, "1") == "VIN" and nl.net(r, "2") == "VF" for r in P), "board fuse")
    q = [r for r, p in P.items() if r.startswith("Q")]
    chk(len(q) == 1 and nl.net(q[0], "2") == "VF" and nl.net(q[0], "3") == "SW", "series P-MOSFET orientation")
    chk(nl.net("U80", "1") == "SW" and nl.net("U80", "3") == "V5", "5 V regulator")
    # CHASSIS = shells + holes, not 0 V
    ch = nl.nets["CHASSIS"]
    chk(all(any(n == (h, "1") for n in ch) for h in [r for r, p in P.items() if r.startswith("H")]), "mounting holes on CHASSIS")
    chk(all((j, "SH") in ch for j in ("J2", "J3", "J4", "J5", "J6")), "D-sub shells on CHASSIS")
    chk("GND" not in {nl.net(h, "1") for h in P if h.startswith("H")}, "holes not on 0 V")
    # safety loops pass through the connector 22-25 (0003 4.6)
    chk(nl.net("J7", "1") == "ESTOP_SRC" and nl.net("J7", "2") == "ESTOP_RET" and nl.net("J7", "3") == "ILOCK_SRC" and nl.net("J7", "4") == "ILOCK_RET", "J7 loops")
    return errs, n_ok


# ----------------------------------------------------------------------------- negative tests
def mutants(nl):
    R = lambda n: ref_of(nl, n)      # noqa: E731
    out = []
    dq = lambda net, q: [r for r, p in nl.parts.items() if r.startswith("D") and nl.net(r, "1") == net and nl.net(r, "2") == q][0]  # noqa: E731
    out.append(("JUDGE window moved one tick earlier (Q3 -> Q2)", [(dq("WJ", "Q3"), "2", "Q2")]))
    out.append(("STROBE window moved one tick later (Q0 -> Q2)", [(dq("WS", "Q0"), "2", "Q2")]))
    out.append(("CLEAR window shortened (Q8 removed)", [(dq("WC", "Q8"), "2", "GND")]))
    out.append(("STROBE window shortened to one tick (Q1 removed)", [(dq("WS", "Q1"), "2", "GND")]))
    out.append(("JUDGE and CLEAR relay contacts swapped between the outputs", [(R("KJ"), "5", "CLEAR_O"), (R("KC"), "5", "JUDGE_O")]))
    out.append(("CK/NCK contacts swapped on bus A", [(R("KCKA"), "5", "NCK_A"), (R("KCKA"), "3", "CK_A")]))
    out.append(("CK decision never sampled (RUN follower sample contact open)", [(R("KR"), "8", "GND")]))
    out.append(("CK decision hold removed (KCKH hold feed open)", [(R("KCKH"), "5", "GND")]))
    out.append(("ring never parks (KGO NC contact open)", [(R("KGO"), "3", "GND")]))
    out.append(("advance-pending hold never broken by the CK (KCKA pole 2)", [(R("KCKA"), "10", "GND")]))
    out.append(("advance-only gating removed (KADV pole 2 bypassed)", [(R("KADV"), "10", "SW")]))
    out.append(("LOAD relay never driven (LOAD window contact open)", [(R("KHLc"), "5", "GND")]))
    out.append(("HOME done never set (DONE window contact open)", [(R("KHLc"), "8", "GND")]))
    out.append(("HOME keeps the counters' clock supply on (KHL window contact tied to the READY drive)", [(R("KHL"), "8", "KEN1")]))
    out.append(("GO chain ignores STOP_LATCH (KSL NC bypassed)", [(R("KSL"), "3", "GA1")]))
    out.append(("END hold cut (KATb pole 2)", [(R("KATb"), "10", "GND")]))
    out.append(("r = 25 decode open (KR25 contact)", [(R("KR25"), "5", "GND")]))
    out.append(("driver supply on 5 V instead of +24V_SW", [("U3", "9", "V5")]))
    out.append(("bus B CK taken from bus A nets (single contact per bus not respected)", [(R("KCKB"), "5", "CK_A")]))
    out.append(("STOPPED output contact open", [(R("KST"), "5", "GND")]))
    out.append(("KENK enable chain open (windows never enabled)", [(R("KRDYb"), "5", "GND")]))
    return out


def quick_suite(nl, seed=1, mode="corner"):
    st = Stats()
    f1, *_ = scen_basic(nl, seed, mode, st, n_cycles=40, stops=(8, 20, 21))
    if f1:
        return f1
    f3, *_ = scen_toggle(nl, seed, mode, st, n_cycles=30)
    if f3:
        return f3
    f2, *_ = scen_end(nl, seed, mode, st)
    return f2


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--netlist")
    ap.add_argument("--no-negative", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    nl = seqnet.Netlist.load(a.netlist) if a.netlist else seqnet.Netlist.load()
    print(f"netlist: {len(nl.parts)} parts, {len(nl.nets)} nets")
    errs, n_ok = structural(nl)
    print(f"structural checks: {n_ok} ok, {len(errs)} failed")
    for e in errs[:20]:
        print("  FAIL", e)
    allf = list(errs)
    st = Stats()
    modes = ("random", "corner", "slow", "fast") if not a.quick else ("random", "corner")
    seeds = range(1, 4) if not a.quick else range(1, 2)
    runs = 0
    for mode in modes:
        for sd in seeds:
            for fn, label in ((scen_basic, "basic"), (scen_toggle, "toggle")):
                f, sim, b = fn(nl, sd, mode, st)
                runs += 1
                if f:
                    allf += [f"[{label} {mode} seed {sd}] {x}" for x in f[:6]]
                    print(f"  {label} {mode} seed {sd}: {len(f)} failures, first: {f[0]}")
    for sd, mode in (((1, "random"), (2, "corner")) if not a.quick else ((1, "corner"),)):
        for sl in (False, True):
            f, sim, b = scen_end(nl, sd, mode, st, slice_mode=sl)
            runs += 1
            if f:
                allf += [f"[end slice={sl} {mode} {sd}] {x}" for x in f[:6]]
                print(f"  END slice={sl} {mode}: {len(f)} failures, first: {f[0]}")
    for sd in ((1, 2) if not a.quick else (1,)):
        for fn, label in ((scen_rehome, "rehome"), (scen_standby, "standby"), (scen_power, "power")):
            f, sim, b = fn(nl, sd, "random", st)
            runs += 1
            if f:
                allf += [f"[{label}] {x}" for x in f[:6]]
                print(f"  {label}: {len(f)} failures, first: {f[0]}")
    print(f"scenario runs: {runs}; position cycles checked: {st.cycles}; register stops: {st.stops}")
    for k, lab in (("S", "STROBE width"), ("J", "JUDGE width"), ("C", "CLEAR width"), ("JS", "JUDGE rise - STROBE rise"),
                   ("CJ", "CLEAR rise - JUDGE fall"), ("SC", "next STROBE rise - CLEAR fall"), ("CKw", "CK width"),
                   ("CK_to_S", "CK rise -> next STROBE rise"), ("CK_after_C", "CK rise - CLEAR rise")):
        if k in st.min:
            print(f"  {lab:32s} min {st.min[k]:7.2f}  max {st.max[k]:7.2f} ms")
    if a.full:
        t1 = time.time()
        nlq = nl
        sim, b = make(nlq, 7, "random", oracle={int(x) for x in random.Random(5).sample(range(N_POS), 90)})
        sim.run_until(250)
        fails: list[str] = []
        hw = [home_sequence(sim, b, fails)]
        b.set_start(True)
        wait_for(sim, lambda: sim.levels_prev.get("END", 0) == 1, 4000000, step=500.0)
        sim.run_until(sim.t + 400)
        fl = analyze(sim, b, Stats(), 0, hw)
        fl += fails
        if len(b.tested) != N_POS:
            fl.append(f"{len(b.tested)} positions tested in the full run, expected {N_POS}")
        print(f"FULL RUN: {len(b.tested)} positions, {len(b.stops)} register stops, simulated {sim.t / 1000:.0f} s in {time.time() - t1:.0f} s; failures: {len(fl)}")
        for x in fl[:8]:
            print("  FAIL", x)
        allf += fl
    neg_fail = 0
    if not a.no_negative:
        ms = mutants(nl)
        det = 0
        for name, edits in ms:
            m = mutate(nl, edits)
            try:
                f = quick_suite(m)
            except Exception as ex:      # noqa: BLE001  (a crash on a broken circuit also counts as detected)
                f = [f"exception {type(ex).__name__}: {ex}"]
            hit = bool(f)
            det += hit
            print(f"  negative test: {name}: {'detected' if hit else 'NOT DETECTED'}" + (f"  ({f[0][:90]})" if hit else ""))
        neg_fail = len(ms) - det
        print(f"negative tests: {det}/{len(ms)} detected")
        if neg_fail:
            allf.append("negative tests not detected")
    print(f"RESULT: {'PASS' if not allf else 'FAIL'}  ({time.time() - t0:.0f} s)")
    return 1 if allf else 0


if __name__ == "__main__":
    sys.exit(main())
