#!/usr/bin/env python3
"""Scenario suite + negative tests of verify_control.py (separate file only to keep both readable)."""
from __future__ import annotations

import math
import random
import sys
import time

from firmware_model import Params, pos_of, test_index


def fmt(x):
    return f"{x:.3f}"


def start_run(H, t_wait=80.0):
    """power-up RUN sequence: RUN low for a while (arms the 48 V rule), then the start switch is turned on"""
    H.reg.start_on = False
    H.run(H.t + t_wait)
    H.reg.start_on = True


def run_stops(v, nl, rep, seed, cells, p_stop, early=False, a_scale=1.0, mut=(), dt=0.1, tag=None):
    tag = tag or f"stops seed {seed}"
    rng = random.Random(seed * 7919 + 13)
    stop_set = {n for n in range(cells) if rng.random() < p_stop}

    def release_fn(H, t):
        if H.reg.latched and not H.reg.pressing and not H._pressed:
            if early:
                if t >= H.reg.stop_latch_hist[-1][0] + H._d_early:
                    H.reg.press(t)
                    H._pressed = True
            elif H.fw.stopped and H.fw.state == "REST":
                if H._t_stopped is None:
                    H._t_stopped = t
                if t - H._t_stopped >= H._d_rel:
                    H.reg.press(t)
                    H._pressed = True
        if not H.reg.latched:
            H._pressed = False
            H._t_stopped = None
            H._d_rel = rng.uniform(80.0, 400.0)
            H._d_early = rng.uniform(0.0, 150.0)

    H = v.Harness(nl, seed=seed, dt=dt, mut=mut, stop_fn=lambda n: n in stop_set, release_fn=release_fn, a_scale=a_scale)
    H._d_rel, H._d_early, H._pressed, H._t_stopped = 120.0, 30.0, False, None
    start_run(H)
    t_end = H.t + 1000.0 * cells / 26.0 * 1.2 + 500.0 + 900.0 * len(stop_set)
    H.run(t_end)
    return H, stop_set, tag


def analyze(v, H, stop_set, rep, tag):
    fw, reg = H.fw, H.reg
    stats = v.check_waveforms(H, rep, tag)
    v.check_contact_face(stats, rep, tag)
    C = rep.check
    C(reg.false_stops == 0, f"{tag}: {reg.false_stops} false stops (JUDGE with an empty register: L8 broken)")
    C(reg.missed_stops == 0, f"{tag}: {reg.missed_stops} missed stops")
    C(reg.not_cleared == 0 and reg.bad_strobe == 0, f"{tag}: register not cleared {reg.not_cleared} / bad STROBE {reg.bad_strobe}")
    lat = reg.stop_latch_hist

    def latched_at(t):
        val = 0
        for tt, s in lat:
            if tt <= t:
                val = s
            else:
                break
        return val
    cellseq = [test_index(*tr) for te, nm, pos, tr in H.pos_samples if nm == "STROBE" and tr is not None and not latched_at(te)]
    C(bool(cellseq) and cellseq[0] == 0 and all(b - a == 1 for a, b in zip(cellseq, cellseq[1:])),
      f"{tag}: effective tested positions are not 0,1,2,... without repeat or gap: {cellseq[:10]}")
    ran = max(cellseq, default=-1)
    exp = {n for n in stop_set if n < ran}
    C(exp <= set(reg.stops), f"{tag}: expected stops {sorted(exp)[:6]} got {reg.stops[:6]}")
    bad = [(round(te, 1), nm, pos, tr) for te, nm, pos, tr in H.pos_samples if tr is not None and pos != tr]
    C(not bad, f"{tag}: POS bus != true position at {bad[:3]}")
    # no timing output may START during HOME or a reverse RETURN
    viol = [(round(t, 1), nm, st) for t, nm, st, tx, hm, lv in H.ev_state if lv and nm != "CLEAR" and (hm or st == "RETREV")]
    C(not viol, f"{tag}: timing output started during HOME / RETURN: {viol[:3]}")
    # stop zone: after every return the shaft stands between the end of CLEAR and the start of the next STROBE (v.ZONE)
    zones = []
    for rel, pos, tr in H.zone_log:
        zones.append(rel)
        C(v.ZONE[0] <= rel < v.ZONE[1], f"{tag}: stopped at {rel:.0f} steps from the cell origin, outside the stop zone {v.ZONE}")
        C(tr is not None and pos == tr, f"{tag}: POS while standing {pos} != stop position {tr}")
    return stats, cellseq, zones, len(reg.stops)


def scenario_stops(v, nl, rep, quick):
    acc = dict(S=[], J=[], C=[], JS=[], CJ=[], SC=[], zone=[], stops=0, cycles=0, runs=0)
    n_seeds = 5 if quick else 16
    for seed in range(1, n_seeds + 1):
        H, ss, tag = run_stops(v, nl, rep, seed, 60 if quick else 82, 0.2, early=(seed % 3 == 0))
        st, cs, zn, ns = analyze(v, H, ss, rep, tag)
        for k in ("S", "J", "C", "JS", "CJ", "SC"):
            acc[k] += st[k]
        acc["zone"] += zn
        acc["stops"] += ns
        acc["cycles"] += len(cs)
        acc["runs"] += 1
    return acc


def scenario_decel(v, nl, rep, quick):
    """stop distance versus the deceleration (hard rule: the shaft must stop before the STROBE of the next cell)"""
    res = []
    for scale in (0.3, 0.5, 0.7, 1.0, 1.3):
        rep2 = v.Report()
        H, ss, tag = run_stops(v, nl, rep2, 100, 30, 0.35, a_scale=scale, dt=0.1, tag=f"decel x{scale}")
        v.check_waveforms(H, rep2, tag)
        viol = [(round(t, 1), nm) for t, nm, st, tx, hm, lv in H.ev_state if lv and nm != "CLEAR" and (hm or st == "RETREV")]
        zn = [round(z) for z, p, t in H.zone_log]
        ok = all(v.ZONE[0] <= z < v.ZONE[1] for z in zn) and not viol and not rep2.fail
        res.append((scale, ok, zn[:6], len(viol), rep2.fail[:2]))
    nominal = [r for r in res if r[0] == 1.0][0]
    rep.check(nominal[1], f"decel x1.0 must be clean: {nominal}")
    rep.check([r for r in res if r[0] == 0.7][0][1], "decel x0.7 must still be clean")
    return res


def scenario_weak(v, nl, rep, quick):
    """very weak deceleration: the shaft overruns into the cycle of the next cell; release pressed early.
    Hard rule of decision 0003 4.2 / 4.4: STOP_LATCH falls while a cycle is running -> no JUDGE before its STROBE (L8), a started
    cycle is finished (L7), nothing starts while the shaft reverses (4.4 (2))."""
    res = []
    straddle = 0
    for seed in range(3, 8 if quick else 12):
        rng = random.Random(seed)
        stop_set = {4, 11}
        def rel(H, t):
            if H.reg.latched and not H.reg.pressing and not H._pressed and t >= H.reg.stop_latch_hist[-1][0] + H._dl:
                H.reg.press(t)
                H._pressed = True
            if not H.reg.latched:
                H._pressed = False
        H = v.Harness(nl, seed=seed, dt=0.1, stop_fn=lambda n: n in stop_set, release_fn=rel, a_scale=0.1)
        H._dl, H._pressed = rng.uniform(18.0, 55.0), False
        start_run(H)
        H.run(H.t + 1800.0)
        tag = f"weak decel seed {seed}"
        st = v.check_waveforms(H, rep, tag)
        rep.check(H.reg.false_stops == 0, f"{tag}: false stops {H.reg.false_stops} (L8)")
        straddle += H.reg.false_straddle
        viol = [(round(t, 1), nm) for t, nm, s_, tx, hm, lv in H.ev_state if lv and nm != "CLEAR" and (hm or s_ == "RETREV")]
        rep.check(not viol, f"{tag}: timing output started while the shaft reverses {viol[:2]}")
        res.append((seed, round(H._dl), len(H.reg.stops), H.reg.false_stops, H.reg.false_straddle))
    return res


def scenario_cyc(v, nl, rep):
    """L7: once a STROBE has been emitted the cycle is finished (JUDGE, CLEAR) even if TXEN falls in the middle of it"""
    tag = "cycle hold (TXEN dropped inside a cycle)"
    H = v.Harness(nl, seed=12, dt=0.1)
    start_run(H)
    done = False
    t_lim = H.t + 600.0
    while H.t < t_lim and not done:
        H.tick()
        if H.events and H.events[-1][1] == "STROBE" and H.events[-1][2] == 1:
            H.fw.txen = False
            done = True
    rep.check(done, f"{tag}: no STROBE seen")
    H.run(H.t + 60.0)
    H.fw.txen = True
    H.run(H.t + 500.0)
    S, _ = v.pulses(H.events, "STROBE")
    J, _ = v.pulses(H.events, "JUDGE")
    C, _ = v.pulses(H.events, "CLEAR")
    rep.check(S and J and C and S[0][1] - S[0][0] >= 7.0 and J[0][1] - J[0][0] >= 7.5 and C[0][1] - C[0][0] >= 12.5,
              f"{tag}: the cycle that had started was not completed (S {len(S)} J {len(J)} C {len(C)})")
    v.check_waveforms(H, rep, tag)


def scenario_v48(v, nl, rep):
    tag = "48 V restart rule"
    H = v.Harness(nl, seed=5, dt=0.1)
    start_run(H)
    H.run(H.t + 600.0)
    moving = abs(H.fw.v) > 1000
    rep.check(moving, f"{tag}: the bank is not running before the test")
    H.v48 = False                      # cover opened: K_I drops, 48 V gone, RUN stays high
    H.run(H.t + 300.0)
    x1 = H.m.x
    H.v48 = True                       # cover closed, 48 V back, RUN still high
    H.run(H.t + 1200.0)
    moved = abs(H.m.x - x1)
    rep.check(moved <= 2, f"{tag}: the bank moved {moved} steps after 48 V returned with RUN still high")
    rep.check(not H.fw.runarm or H.fw.state == "REST", f"{tag}: rule flag")
    H.reg.start_on = False             # RUN falls ...
    H.run(H.t + 60.0)
    H.reg.start_on = True              # ... and rises
    H.run(H.t + 400.0)
    rep.check(abs(H.m.x - x1) > 100, f"{tag}: the bank does not restart after RUN fell and rose")
    # the motion before 48 V loss is continuous, hardware cycles stayed correct
    st = v.check_waveforms(H, rep, tag)
    return dict(moved=moved, x_after=H.m.x - x1)


def scenario_estop(v, nl, rep):
    tag = "emergency stop"
    H = v.Harness(nl, seed=6, dt=0.1)
    start_run(H)
    H.run(H.t + 700.0)
    n0 = len(H.events)
    H.sw_set(False)                    # K_E opens: +24V_SW and the 48 V disappear together
    H.v48 = False
    H.run(H.t + 800.0)
    late = [e for e in H.events[n0:] if e[2] == 1 and e[0] > H.t - 700.0]
    rep.check(not late, f"{tag}: timing outputs rose while +24V_SW was off: {late[:3]}")
    rep.check(H.fw.need_home, f"{tag}: the firmware does not require HOME after an emergency stop")
    x1 = H.m.x
    H.sw_set(True)
    H.v48 = True
    H.run(H.t + 300.0)
    H.reg.start_on = False
    H.run(H.t + 100.0)
    H.reg.start_on = True
    H.run(H.t + 800.0)
    rep.check(abs(H.m.x - x1) <= 2, f"{tag}: the bank moved after the reset without HOME ({abs(H.m.x - x1)} steps)")
    return dict(coast=abs(x1 - 0))


def home_run(v, nl, rep, x0, synced, seed, mut=(), dt=1.0):
    tag = f"HOME from x={x0}{' (synced)' if synced else ' (unsynced)'}"
    H = v.Harness(nl, seed=seed, dt=dt, mut=mut, start_x=x0, synced=False)
    if synced:
        H.sync_at_home(x0)
    else:
        H.fw.need_home = True
    H.fw.stopped = True
    H.reg.start_on = False
    H.run(H.t + 30.0)
    H.fw.home = True
    t_lim = H.t + 1500.0 + 1000.0 * (v.PERIOD_X / 20800.0) * 1.1
    while H.t < t_lim and not (H.fw.at_home and H.fw.state == "REST"):
        H.tick()
    H.fw.home = False
    H.run(H.t + 50.0)
    return H, tag


def scenario_home(v, nl, rep, quick):
    rng = random.Random(77)
    out = []
    cases = [(v.HOME_X, True), (v.HOME_X + 12000 * 3 + 5000, True), (v.HOME_X + 12000 * 90, True), (v.PERIOD_X - 12000 * 40 - 600, True),
             (v.PERIOD_X // 2 + 777, True), (v.PERIOD_X // 2 - 4123, True)]
    n_extra = 3 if quick else 8
    cases += [(v.HOME_X + int(rng.uniform(0, v.PERIOD_X)), True) for _ in range(n_extra)]
    cases += [(v.HOME_X + 12000 * 5 + 1234, False)] + [(v.HOME_X + int(rng.uniform(0, 3.0e6)), False) for _ in range(2 if quick else 3)]
    # inside cycle 0: the fast drum shows letter 0 also in the empty cell 26 (a false home if HOME used the three index sectors only),
    # and just past the home mark (STROBE slot of cell 0)
    cases += [(v.CELL * 26 + 100, False), (v.CELL * 26 + 100, True), (v.CELL * 25 + 100, False), (v.STROBE_W[0] + 30, False)]
    for i, (x0, synced) in enumerate(cases):
        H, tag = home_run(v, nl, rep, x0, synced, seed=200 + i)
        fw = H.fw
        rel = (H.m.x - v.HOME_X) % v.PERIOD_X
        okp = rel <= 2 or rel >= v.PERIOD_X - 2
        rep.check(fw.at_home and fw.state == "REST" and okp, f"{tag}: AT_HOME not reached (x_rel {rel}, state {fw.state})")
        rep.check(H.m.level("I_L") and H.m.level("I_M") and H.m.level("I_S"), f"{tag}: not inside the index sectors at the end")
        rep.check(fw.cnt == [0, 0, 0] and fw.pos == (0, 0, 0), f"{tag}: POS after HOME {fw.pos}")
        rep.check(fw.txen and not fw.homing and fw.stopped and not fw.end, f"{tag}: flags after HOME")
        viol = [(round(t, 1), nm) for t, nm, st, tx, hm, lv in H.ev_state if lv and hm and nm != "CLEAR"]
        rep.check(not viol, f"{tag}: timing output started during HOME ({len(viol)} edges)")
        d = (x0 - v.HOME_X) % v.PERIOD_X
        shorter = min(d, v.PERIOD_X - d)
        if synced:
            rep.check(H.steps_total <= shorter + 600, f"{tag}: path {H.steps_total} steps, shortest {shorter}")
        out.append((x0, synced, fw.home_how, H.steps_total, round(H.t / 1000.0, 1)))
    return out


def scenario_end(v, nl, rep):
    res = []
    for end_pos, label in ((17575, "full run"), (675, "slice of 676 positions")):
        tag = f"END ({label})"
        p = Params(end_pos=end_pos)
        n_start = end_pos - 3                       # test order n = 26 Q + k
        q, k = divmod(n_start, 26)
        c = v.CYCLE_CELLS * q + k
        x = v.CELL * c + v.ZONE[0] + p.ret_offset   # standing in the stop zone after position n_start
        H = v.Harness(nl, seed=9, dt=0.1, p=p, start_x=x, synced=False)
        H.sync_at_home(x, hold=v.pos_truth(v.CELL * c))
        H.fw.stopped = True
        start_run(H)
        H.run(H.t + 1500.0)
        fw = H.fw
        S, _ = v.pulses(H.events, "STROBE")
        cells = [test_index(*tr) for te, nm, pos, tr in H.pos_samples if nm == "STROBE" and tr is not None]
        rep.check(cells == [end_pos - 2, end_pos - 1, end_pos], f"{tag}: tested positions {cells} (expected the last three)")
        rep.check(fw.end and fw.stopped and fw.state == "REST", f"{tag}: END / STOPPED not set (state {fw.state})")
        qe, ke = divmod(end_pos, 26)
        last = v.pos_truth(v.CELL * (v.CYCLE_CELLS * qe + ke))   # core position of the last tested cell (l = (4 m + k) mod 26)
        rep.check(fw.pos == last, f"{tag}: POS at END {fw.pos} != {last}")
        x_end = H.m.x
        H.reg.start_on = False
        H.run(H.t + 100.0)
        H.reg.start_on = True
        H.run(H.t + 600.0)
        rep.check(H.m.x == x_end, f"{tag}: the bank moved although END is set")
        v.check_waveforms(H, rep, tag)
        res.append((label, cells, fw.pos))
    return res


# ----------------------------------------------------------------------------- negative tests
def mutants(nl):
    """netlist mutants: (name, function making the mutated copy)"""
    L = []

    def swap_jc(c):                       # JUDGE and CLEAR drive channels exchanged
        a, b = ("U60", "17"), ("U60", "16")
        c.swap_pins(a, b)
    L.append(("JUDGE and CLEAR TBD output channels exchanged", swap_jc))

    def no_seen_clear(c):                 # SEEN never cleared by the STOP_LATCH edge / HOMING / POR
        c.move_pin("U37", "1", "+3V3")
    L.append(("SEEN flip-flop: clear input tied high (L8 broken)", no_seen_clear))

    def no_txe(c):                        # TXE gating removed: STROBE_G = SLOT_S & 1
        for ref in ("U33",):
            for pin in ("2", "5", "13"):
                if c.net(ref, pin) == "TXE":
                    c.move_pin(ref, pin, "+3V3")
    L.append(("TXE gating removed from the three AND gates", no_txe))

    def tbd_wrong_rail(c):
        c.move_pin("U60", "9", "+24V_L")
    L.append(("TBD U60 supplied from +24V_L instead of +24V_SW", tbd_wrong_rail))

    def fuse_gone(c):                     # a resettable fuse replaced by a wire (value changed -> structure)
        c.comp["F25"]["value"] = "0R"
    L.append(("fuse F25 (POS bit) replaced by a wire", fuse_gone))

    def pos_swap(c):                      # POS3 / POS4 exchanged on the 595
        c.swap_pins(("U50", "5"), ("U50", "6"))
    L.append(("POS bits 0 and 1 of the l digit exchanged at the 595", lambda c: c.swap_pins(("U50", "4"), ("U50", "5"))))

    def cyc_nohold(c):                    # CYC hold removed: TXE = TXEN | 0
        c.move_pin("U35", "2", "GND")
    L.append(("CYC hold removed from TXE", cyc_nohold))

    def estop_ret(c):
        c.swap_pins(("U40", "17"), ("U40", "13"))
    L.append(("RUN and ESTOP optocoupler emitters exchanged", lambda c: c.swap_pins(("U40", "15"), ("U40", "13"))))
    L.append(("opto input resistor R20 changed to 10k", lambda c: c.comp["R20"].__setitem__("value", "10k")))
    L.append(("Pico TXEN and HOMING pins exchanged", lambda c: c.swap_pins(("U1", "5"), ("U1", "6"))))
    return L


def fw_mutants():
    return [("no_runarm", "48 V restart rule removed"),
            ("no_txen_return", "return reverse without TXEN off"),
            ("forward_only", "HOME always searches forward"),
            ("bad_return", "return target before the STROBE of the same cell"),
            ("home_index_only", "HOME on the three index sectors alone (26-cell model; false home in the empty cell 26)")]


def main(args, v):
    t0 = time.time()
    quick = args.quick
    if args.netlist:
        nlp = v.Path(args.netlist)
    else:
        nlp = v.scratch() / "control_board.xml"
        v.export_netlist(nlp)
    nl = v.Netlist(nlp)
    allrep = v.Report()
    print("== 1. structure of the netlist ==")
    rs = v.Report()
    v.static_checks(nl, rs)
    print(f"   {rs.n} checks, {len(rs.fail)} failures", *rs.fail[:10], sep="\n   ")
    allrep.fail += rs.fail
    print("== 2/3. hardware gating (netlist-derived) + machine / firmware / register co-simulation ==")
    rep = v.Report()
    ac = scenario_stops(v, nl, rep, quick)
    mn = lambda a: min(a) if a else float("nan")
    print(f"   {ac['runs']} runs, {ac['cycles']} tested positions, {ac['stops']} stops (random oracle, random relay delays, release at random / early)")
    print(f"   connector face minima: STROBE {mn(ac['S']):.3f} ms (>= 7.0 calibrated), JUDGE {mn(ac['J']):.3f} (>= 7.5), CLEAR {mn(ac['C']):.3f} (>= 12.5),"
          f" JUDGE rise - STROBE contact start {mn(ac['JS']) - v.STROBE_CAL_ON:.3f} (>= 7.5, L2 calibrated; rise - rise {mn(ac['JS']):.3f}),"
          f" CLEAR-JUDGE {mn(ac['CJ']):.3f} (>= 1.5), STROBE-CLEAR {mn(ac['SC']):.3f} (>= 1.5)")
    if ac["zone"]:
        print(f"   stop zone: {len(ac['zone'])} returns, standing at {min(ac['zone']):.0f} .. {max(ac['zone']):.0f} steps after the cell origin (zone {v.ZONE[0]} .. {v.ZONE[1]})")
    print(f"   rules checked per run: L1-L8, C1-C6, stops == oracle, tested positions 0,1,2,... without repeat, POS == truth, no output during HOME / RETURN")
    wk = scenario_weak(v, nl, rep, quick)
    print(f"   very weak deceleration (x0.1: the shaft overruns into the next cycle) with early release: {len(wk)} runs, false stops {sum(w[3] for w in wk)}, {sum(w[4] for w in wk)} releases fell inside a JUDGE pulse (register false stop, unavoidable), nothing started while reversing")
    dec = scenario_decel(v, nl, rep, quick)
    print("   deceleration sweep (x nominal 1.2e6 steps/s^2): " + "; ".join(f"x{s}: {'ok' if ok else 'FAIL'} zone {z[:3]}" for s, ok, z, nv, f in dec))
    scenario_cyc(v, nl, rep)
    print('   cycle hold: a cycle that started is completed (JUDGE, CLEAR) although TXEN falls inside it (L7)')
    d48 = scenario_v48(v, nl, rep)
    print(f"   48 V rule: moved {d48['moved']} steps after 48 V returned with RUN high; restarts after RUN fell and rose")
    scenario_estop(v, nl, rep)
    print("   emergency stop: no timing output while +24V_SW is off, HOME required after the reset")
    hm = scenario_home(v, nl, rep, quick)
    print(f"   HOME: {len(hm)} cases (synced: shortest path, unsynced: forward search): " +
          ", ".join(f"{h[2]}:{h[3] / 1000:.0f}k steps/{h[4]} s" for h in hm[:6]) + " ...")
    en = scenario_end(v, nl, rep)
    print(f"   END: full run and a 676-position slice end after the last tested position (test order) {[e[1] for e in en]},"
          f" POS (l, m, r) at END {[e[2] for e in en]}; RUN ignored afterwards")
    allrep.fail += rep.fail
    print(f"   {rep.n} checks, {len(rep.fail)} failures", *rep.fail[:10], sep="\n   ")
    print(f"   (elapsed {time.time() - t0:.0f} s)")
    n_neg = n_det = 0
    if not args.no_negative:
        print("== 4. negative tests ==")
        for name, fn in mutants(nl):
            c = nl.clone()
            try:
                fn(c)
            except Exception as e:
                print(f"   [mutant could not be built] {name}: {e}")
                continue
            r = v.Report()
            v.static_checks(c, r)
            if not r.fail:
                r2 = v.Report()
                try:
                    H, ss, tag = run_stops(v, c, r2, 3, 40, 0.25, early=True, dt=0.2)
                    analyze(v, H, ss, r2, tag)
                    scenario_weak(v, c, r2, True)
                    if "emergency" in name or "TBD U60" in name or "TXE gating" in name:
                        scenario_estop(v, c, r2)
                    if "TXE gating" in name or "CYC" in name:
                        scenario_cyc(v, c, r2)
                        H2, tg = home_run(v, c, r2, v.HOME_X + 12000 * 3 + 5000, True, 3)
                        viol = [1 for t, nm, st, tx, hm_, lv in H2.ev_state if lv and hm_]
                        r2.check(not viol, "timing output started during HOME")
                except Exception as e:
                    r2.fail.append(f"simulation error {type(e).__name__}: {e}")
                r.fail += r2.fail
            n_neg += 1
            det = bool(r.fail)
            n_det += det
            print(f"   {'detected ' if det else 'NOT DETECTED'}  netlist: {name}   ({r.fail[0][:90] if r.fail else ''})")
        for key, name in fw_mutants():
            r = v.Report()
            try:
                if key == "no_runarm":
                    H = v.Harness(nl, seed=5, dt=0.1, mut=(key,))
                    start_run(H)
                    H.run(H.t + 600.0)
                    H.v48 = False
                    H.run(H.t + 300.0)
                    x1 = H.m.x
                    H.v48 = True
                    H.run(H.t + 1200.0)
                    r.check(abs(H.m.x - x1) <= 2, "bank moved after 48 V returned with RUN high")
                elif key == "forward_only":
                    H, tag = home_run(v, nl, r, v.HOME_X + v.PERIOD_X // 4, True, 4, mut=(key,))
                    d = (H.start_x - v.HOME_X) % v.PERIOD_X
                    r.check(H.steps_total <= min(d, v.PERIOD_X - d) + 600, "HOME path longer than the shortest")
                elif key == "no_txen_return":
                    H = v.Harness(nl, seed=5, dt=0.1, stop_fn=lambda n: n in {4, 11}, a_scale=0.1, mut=(key,),
                                  release_fn=lambda H, t: (H.reg.press(t), setattr(H, "_pr", True))[0] if (H.reg.latched and H.fw.stopped and not H.reg.pressing and not getattr(H, "_pr", False)) else (setattr(H, "_pr", False) if not H.reg.latched else None))
                    start_run(H)
                    H.run(H.t + 1500.0)
                    viol = [1 for t, nm, st, tx, hm_, lv in H.ev_state if lv and nm != "CLEAR" and st == "RETREV"]
                    r.check(not viol, "timing output started during a reverse return")
                elif key == "bad_return":
                    p = Params(ret_offset=-460)
                    rng = random.Random(1)
                    stop_set = {5, 17}
                    H = v.Harness(nl, seed=1, dt=0.1, p=p, stop_fn=lambda n: n in stop_set,
                                  release_fn=lambda H, t: (H.reg.press(t), setattr(H, "_pr", True))[0] if (H.reg.latched and H.fw.stopped and not H.reg.pressing and not getattr(H, "_pr", False)) else (setattr(H, "_pr", False) if not H.reg.latched else None))
                    start_run(H)
                    H.run(H.t + 1800.0)
                    cs = [test_index(*tr) for te, nm, pos, tr in H.pos_samples if nm == "STROBE" and tr is not None]
                    lat = H.reg.stop_latch_hist
                    r.check(not (len(cs) != len(set(cs)) and False) and H.reg.stops.count(5) <= 1 and len(H.reg.stops) == len(set(H.reg.stops)),
                            "the same position stopped again after the restart")
                elif key == "home_index_only":
                    H, tag = home_run(v, nl, r, v.CELL * 25 + 100, False, 5, mut=(key,))
                    rel = (H.m.x - v.HOME_X) % v.PERIOD_X
                    r.check(rel <= 2 or rel >= v.PERIOD_X - 2, f"HOME ended at x_rel {rel} (not cell 0 of cycle 0)")
            except Exception as e:
                r.fail.append(f"simulation error {type(e).__name__}: {e}")
            n_neg += 1
            det = bool(r.fail)
            n_det += det
            print(f"   {'detected ' if det else 'NOT DETECTED'}  firmware: {name}   ({r.fail[0][:90] if r.fail else ''})")
        print(f"   {n_det} / {n_neg} mutants detected")
        if n_det != n_neg:
            allrep.fail.append("negative tests: not all mutants detected")
    print(f"RESULT: {'PASS' if not allrep.fail else 'FAIL'}   (total {time.time() - t0:.0f} s)")
    return 1 if allrep.fail else 0
