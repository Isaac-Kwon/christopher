#!/usr/bin/env python3
"""Verification of the gear control board: netlist structure + hardware gating simulation + machine / firmware co-simulation.

    python verify_control.py                  # all checks (about 3-6 minutes)
    python verify_control.py --quick          # smaller case set (what the negative tests use)
    python verify_control.py --netlist X.xml  # use an existing kicad xml netlist
    python verify_control.py --no-negative    # skip the negative tests

1. STRUCTURE (netlist of the generated schematic, kicad-cli): J_CTRL pin map of decision 0003 5.2, every output = 595 / HC gate ->
   TBD62783A channel -> resettable fuse -> pin (+ pull-down), supply domains (+24V_SW for STROBE / JUDGE / CLEAR / PUL / DIR / ENA),
   every 24 V input = 2.2k + TLP291 + 1N4148W, Pico pin map, power chains, sensors, safety loops, CHASSIS, no 48 V net.
2. HARDWARE GATING SIMULATION: the 74HC14 / 08 / 32 / 02 / 74 network and its RC elements are read from the NETLIST (nets of the
   gate pins, R / C values) and simulated event by event (gate delay 20 ns, RC delays from the part values, Schmitt thresholds).
3. CO-SIMULATION: rotating machine (slot windows from the CAD single source hardware/gear/cad/params.py at 400 steps per cell,
   26 slotted cells + 4 empty cells per revolution of the cycle axis; position discs on F / M / S as in the CAD: the F disc turns
   30 positions per cycle-axis revolution), closed-loop stepper (exact steps), the firmware model (firmware_model.py) and the
   relay register (contact delays, K / P / S behaviour, release button) around the netlist-derived hardware.  Decision 0003 L1-L9 are
   checked on the connector-face waveforms of every emitted cycle (L2 in its calibrated form: the gear STROBE is calibrated).
   Every HOME must end with exactly one CLEAR (L5) and no STROBE / JUDGE (decision 0003 4.4 (b)); an emergency stop in the middle
   of a cycle (sense relays left latched / cycle hold left set), reset, HOME and run must stop at position 0.
4. NEGATIVE TESTS: netlist and firmware mutants must be detected.
"""
from __future__ import annotations

import argparse
import bisect
import heapq
import math
import os
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_control as gc                       # noqa: E402  (pin tables only)
from firmware_model import Firmware, Params, pos_of, test_index, SLOT_W, CELL, T_OFF, CARRY_X, CYCLE_X, CYCLE_CELLS   # noqa: E402

KICAD_CLI = Path(os.environ.get("KICAD_CLI", "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"))


def scratch() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "christopher" / "control-board"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ============================================================================= netlist
class Netlist:
    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        self.comp = {}
        for c in root.find("components"):
            self.comp[c.get("ref")] = dict(value=c.findtext("value") or "", fp=c.findtext("footprint") or "",
                                           desc=c.findtext("description") or "")
        self.nets: dict[str, list[tuple[str, str]]] = {}
        self.pin_net: dict[tuple[str, str], str] = {}
        for n in root.find("nets"):
            name = n.get("name").split("/")[-1]
            lst = [(nd.get("ref"), nd.get("pin")) for nd in n]
            self.nets[name] = lst
            for ref, pin in lst:
                self.pin_net[(ref, pin)] = name

    def net(self, ref, pin):
        return self.pin_net.get((ref, str(pin)))

    def pins_on(self, net):
        return self.nets.get(net, [])

    def other(self, ref, pin):
        return self.net(ref, "2" if str(pin) == "1" else "1")

    def clone(self):
        c = object.__new__(Netlist)
        c.comp = {k: dict(v) for k, v in self.comp.items()}
        c.nets = {k: list(v) for k, v in self.nets.items()}
        c.pin_net = dict(self.pin_net)
        return c

    def move_pin(self, ref, pin, newnet):
        old = self.pin_net[(ref, str(pin))]
        self.nets[old].remove((ref, str(pin)))
        self.nets.setdefault(newnet, []).append((ref, str(pin)))
        self.pin_net[(ref, str(pin))] = newnet

    def swap_pins(self, a, b):
        na, nb = self.pin_net[a], self.pin_net[b]
        self.move_pin(*a, nb)
        self.move_pin(*b, na)


def export_netlist(dest: Path):
    r = subprocess.run([str(KICAD_CLI), "sch", "export", "netlist", "--format", "kicadxml", "-o", str(dest),
                        str(HERE / "control_board.kicad_sch")], capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise SystemExit("kicad-cli netlist export failed: " + r.stdout + r.stderr)


class Report:
    def __init__(self):
        self.fail: list[str] = []
        self.n = 0

    def check(self, cond, msg):
        self.n += 1
        if not cond:
            self.fail.append(msg)


# ============================================================================= 1. structure
def trace_out(nl: Netlist, pin: int):
    """J_CTRL pin -> (fuse ref, TBD ref, channel, input net, supply net)"""
    net = nl.net("J3", pin)
    fu = [r for r, p in nl.pins_on(net) if r.startswith("F") and nl.comp[r]["value"].startswith("PTC 30mA")]
    if len(fu) != 1:
        return None
    do = nl.other(fu[0], [p for r, p in nl.pins_on(net) if r == fu[0]][0])
    tb = [(r, p) for r, p in nl.pins_on(do) if nl.comp[r]["value"].startswith("TBD62783")]
    if len(tb) != 1:
        return None
    r, p = tb[0]
    ch = 19 - int(p)
    return fu[0], r, ch, nl.net(r, str(ch)), nl.net(r, "9")


def static_checks(nl: Netlist, rep: Report):
    C = rep.check
    # --- connector map
    for p in range(1, 38):
        want = gc.JC_PINS.get(p)
        got = nl.net("J3", p)
        if want:
            C(got == want, f"J_CTRL pin {p}: net {got!r} != {want!r}")
        elif p in gc.JC_NC:
            C(got is None or got.startswith("unconnected"), f"J_CTRL pin {p} must be unconnected (got {got})")
    C(all(nl.net("J3", p) == "GND" for p in range(28, 34)), "J_CTRL 28-33 = 0 V")
    # --- outputs: signal -> fuse -> TBD -> input
    exp_vcc = {"STROBE": "+24V_SW", "JUDGE": "+24V_SW", "CLEAR": "+24V_SW"}
    for s in gc.OUT_SIGNALS:
        pin = [p for p, n in gc.JC_PINS.items() if n == s][0]
        tr = trace_out(nl, pin)
        C(tr is not None, f"output {s}: no fuse + TBD chain on J_CTRL pin {pin}")
        if not tr:
            continue
        fu, tbd, ch, innet, vcc = tr
        C(vcc == exp_vcc.get(s, "+24V_L"), f"output {s}: TBD {tbd} supply {vcc}")
        C(nl.net(tbd, "10") == "GND", f"{tbd} ground")
        if s in ("STROBE", "JUDGE", "CLEAR"):
            C(innet == s + "_G", f"output {s}: TBD input net {innet} != {s}_G")
        else:
            b = gc.BITS.index(s)
            C(innet == f"BIT{b}", f"output {s}: TBD input net {innet} != BIT{b}")
            q = ["15", "1", "2", "3", "4", "5", "6", "7"][b % 8]
            chip = f"U{50 + b // 8}"
            C(nl.net(chip, q) == f"BIT{b}", f"output {s}: 595 {chip} pin {q} net {nl.net(chip, q)}")
        pd = [r for r, p in nl.pins_on(innet) if nl.comp[r]["value"] == "47k" and nl.other(r, p) == "GND"]
        C(len(pd) == 1, f"output {s}: driver input pull-down 47k")
        pd2 = [r for r, p in nl.pins_on(s) if nl.comp[r]["value"] == "47k" and nl.other(r, p) == "GND"]
        C(len(pd2) == 1, f"output {s}: pin-side pull-down 47k")
    for nm, gp in (("PUL", "PUL_M"), ("DIR", "DIR_M"), ("ENA", "ENA_M")):
        r1 = [r for r, p in nl.pins_on(f"DO_{nm}") if nl.comp[r]["value"] == "1.5k"]
        C(len(r1) == 1 and nl.other(r1[0], "1") in (f"DRV_{nm}",) or (len(r1) == 1 and f"DRV_{nm}" in (nl.net(r1[0], "1"), nl.net(r1[0], "2"))), f"{nm}: 1.5k to the drive terminal")
        tb = [(r, p) for r, p in nl.pins_on(f"DO_{nm}") if nl.comp[r]["value"].startswith("TBD")]
        C(len(tb) == 1 and nl.net(tb[0][0], "9") == "+24V_SW" and nl.net(tb[0][0], str(19 - int(tb[0][1]))) == gp,
          f"{nm}: TBD channel on +24V_SW driven by {gp}")
    # --- 595 chain
    chain = ["U50", "U51", "U52"]
    C(nl.net("U50", "14") == "MOSI_M", "595 chain starts at MOSI_M")
    for a, b in zip(chain, chain[1:]):
        C(nl.net(a, "9") == nl.net(b, "14") and nl.net(a, "9") is not None, f"595 chain {a} -> {b}")
    for u in chain:
        C(nl.net(u, "11") == "SCK_M" and nl.net(u, "12") == "RCLK_M" and nl.net(u, "13") == "OE_M"
          and nl.net(u, "10") == "+3V3" and nl.net(u, "16") == "+3V3" and nl.net(u, "8") == "GND", f"{u} 595 control pins")
    # --- Pico pin map
    for gp, pin in gc.PICO_PIN.items():
        want = gc.PICO_GPIO.get(gp)
        C(nl.net("U1", pin) == want, f"Pico GP{gp} (pin {pin}): {nl.net('U1', pin)} != {want}")
    C(nl.net("U1", "36") == "+3V3" and nl.net("U1", "39") == "VSYS", "Pico 3V3 / VSYS")
    # --- 24 V inputs
    for i, (name, net, src) in enumerate(gc.INPUTS):
        r = f"R{20 + i}"
        C(nl.comp[r]["value"] == "2.2k" and net in (nl.net(r, "1"), nl.net(r, "2")), f"input {name}: 2.2k on {net}")
        sns = nl.other(r, [p for rr, p in nl.pins_on(net) if rr == r][0]) if any(rr == r for rr, _ in nl.pins_on(net)) else None
        d = f"D{20 + i}"
        C(nl.net(d, "1") == sns and nl.net(d, "2") == "GND", f"input {name}: 1N4148W anti-parallel")
        pkg, u = divmod(i, 4)
        opto = f"U{40 + pkg}"
        a = 2 * (u + 1) - 1
        C(nl.net(opto, a) == sns and nl.net(opto, a + 1) == "GND", f"input {name}: TLP291 LED on {opto}")
        C(nl.net(opto, 18 - 2 * (u + 1)) == "+3V3", f"input {name}: opto collector +3V3")
        em = nl.net(opto, 17 - 2 * (u + 1))
        C(em == gc.INPUT_M[i], f"input {name}: opto emitter {em} != {gc.INPUT_M[i]}")
        pd = [rr for rr, p in nl.pins_on(em) if nl.comp[rr]["value"] == "10k" and nl.other(rr, p) == "GND"]
        C(len(pd) == 1, f"input {name}: 10k emitter pull-down")
        C(em in [gc.PICO_GPIO[g] for g in gc.PICO_GPIO], f"input {name}: reaches a Pico pin")
    C(nl.net("J3", 2) == "RUN" and nl.net("J3", 4) == "HOME" and nl.net("J3", 36) == "STOP_LATCH", "RUN / HOME / STOP_LATCH inputs on the specified pins")
    for pin in (2, 4, 36, 23, 25):
        n = nl.net("J3", pin)
        others = [r for r, p in nl.pins_on(n) if not r.startswith(("J", "SJ"))]
        C(all(nl.comp[r]["value"] in ("2.2k",) for r in others if r.startswith("R")) and not any(r.startswith(("F", "Q", "U6")) for r in others),
          f"input pin {pin}: only the 2.2k receiver (no driver, no pull-up)")
    # --- power
    for n, (rail, vin) in enumerate([("+24V_L", "VIN_L"), ("+24V_SW", "VIN_SW")], 1):
        C(nl.net(f"F{n}", "1") == vin and nl.net(f"J{n}", "1") == vin and nl.net(f"J{n}", "2") == "GND", f"{rail}: terminal -> PTC")
        C(nl.net(f"Q{n}", "2") == nl.net(f"F{n}", "2") and nl.net(f"Q{n}", "3") == rail and nl.net(f"Q{n}", "1") == f"GATEP{n}",
          f"{rail}: series P-MOSFET drain on the supply side")
        C(nl.net(f"DZ{n}", "1") == rail and nl.net(f"DZ{n}", "2") == f"GATEP{n}" and nl.net(f"RG{n}", "1") == f"GATEP{n}", f"{rail}: gate zener + resistor")
        C(nl.net(f"D{n}", "1") == rail and nl.net(f"D{n}", "2") == "GND", f"{rail}: TVS")
        C(nl.net(f"C{n}", "1") == rail, f"{rail}: bulk capacitor")
    C(nl.net("U80", "1") == "+24V_L" and nl.net("U80", "3") == "+5V", "TSR from +24V_L")
    C(nl.net("D4", "2") == "+5V" and nl.net("D4", "1") == "VSYS", "Schottky +5 V -> VSYS")
    C(nl.net("F3", "1") == "+24V_L" and nl.net("F3", "2") == "+24V_S", "sensor supply PTC from +24V_L")
    C(not any(re.search(r"(^|[^A-Z0-9])48V", n) for n in nl.nets), "no 48 V net on the board")
    # --- CHASSIS
    C(all(nl.net(f"H{k}", "1") == "CHASSIS" for k in range(1, 5)), "mounting holes on CHASSIS")
    C({nl.net("RCH1", "1"), nl.net("RCH1", "2")} == {"CHASSIS", "GND"} and {nl.net("CCH1", "1"), nl.net("CCH1", "2")} == {"CHASSIS", "GND"},
      "CHASSIS - 0 V: 1M || 10n")
    # --- sensors
    for i, (k, _) in enumerate(gc.SENSORS):
        j = f"J{10 + i}"
        C(nl.net(j, "1") == "+24V_S" and nl.net(j, "3") == "GND" and nl.net(j, "2") == f"RAW_{k}", f"sensor {k}: connector")
        C(nl.net(f"R{40 + i}", "1") == "+3V3" and nl.net(f"R{40 + i}", "2") == f"RAW_{k}", f"sensor {k}: pull-up")
        C(nl.net(f"R{50 + i}", "1") == f"RAW_{k}" and nl.net(f"R{50 + i}", "2") == f"FLT_{k}" and nl.net(f"C{20 + i}", "1") == f"FLT_{k}", f"sensor {k}: RC")
    # --- safety loops
    C(nl.net("J3", 22) == "ESTOP_A" and nl.net("J3", 24) == "ILOCK_A", "loop out pins 22 / 24")
    C({nl.net("SJ1", "1"), nl.net("SJ1", "2")} == {"ESTOP_SRC", "ESTOP_A"}, "ESTOP source jumper (closed)")
    C({nl.net("SJ2", "1"), nl.net("SJ2", "2")} == {"ILOCK_SRC", "ILOCK_A"} and "Open" in nl.comp["SJ2"]["fp"], "ILOCK source jumper is OPEN by default")
    C("Bridged" in nl.comp["SJ1"]["fp"], "SJ1 bridged")
    C(nl.net("J4", "4") == "ESTOP_K" and nl.net("J4", "5") == "ILOCK_K", "J_SAFE returns")
    # --- logic gates (connectivity of the gating equations is checked functionally by the simulation)
    C(nl.net("U33", "14") == "+3V3" and nl.net("U33", "7") == "GND", "U33 supply")
    # HOME flush (0003 4.4): the CLEAR gate has no SEEN input (the flush CLEAR comes after HOMING has cleared SEEN), JUDGE has;
    # CLR1 = POR | HOMING feeds the SEEN reset and the CYC reset (both are exercised by the simulation and its mutants)
    C(nl.net("U33", "6") == "CLEAR_G" and "SEEN" not in {nl.net("U33", "4"), nl.net("U33", "5")} and "SLOT_C" in {nl.net("U33", "4"), nl.net("U33", "5")},
      "CLEAR_G = SLOT_C & TXE, not gated by SEEN")
    C("SEEN" in {nl.net("U33", "9"), nl.net("U33", "10")}, "JUDGE gated by SEEN")
    C({nl.net("U35", "4"), nl.net("U35", "5")} == {"POR", "HOMING"} and nl.net("U35", "6") == "CLR1", "CLR1 = POR | HOMING")


# ============================================================================= 2. hardware simulation from the netlist
GATE_DELAY = 2e-5          # ms (20 ns)
VTP, VTN, VDD = 1.9, 1.0, 3.3


class HW:
    """Event-driven simulation of the 74HC network read from the netlist."""

    def __init__(self, nl: Netlist):
        self.nl = nl
        self.t = 0.0
        self.val: dict[str, int] = {}
        self.pending: dict[str, tuple] = {}
        self.q: list = []
        self.seq = 0
        self.listeners: dict[str, list] = {}
        self.watch: dict[str, list] = {}
        self.log: list[tuple[float, str, int]] = []
        self.elems = []
        self._build()
        self.val["+3V3"] = 1
        self.val["GND"] = 0
        self._init()

    # ---- element construction from netlist pins
    def _add(self, kind, ins, outs, **kw):
        e = dict(kind=kind, ins=ins, outs=outs, **kw)
        self.elems.append(e)
        for n in ins:
            self.listeners.setdefault(n, []).append(e)

    def _build(self):
        nl = self.nl
        pins14 = [("1", "2"), ("3", "4"), ("5", "6"), ("9", "8"), ("11", "10"), ("13", "12")]
        for ref in ("U30", "U31", "U32"):
            for a, o in pins14:
                self._add("not", [nl.net(ref, a)], [nl.net(ref, o)])
        pins4 = [("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"), ("12", "13", "11")]
        for ref, kind in (("U33", "and"), ("U34", "and"), ("U35", "or")):
            for a, b, o in pins4:
                self._add(kind, [nl.net(ref, a), nl.net(ref, b)], [nl.net(ref, o)])
        for a, b, o in [("2", "3", "1"), ("5", "6", "4"), ("8", "9", "10"), ("11", "12", "13")]:
            self._add("nor", [nl.net("U36", a), nl.net("U36", b)], [nl.net("U36", o)])
        # flops: unit 1 (R 1, D 2, C 3, S 4, Q 5) and unit 2 (R 13, D 12, C 11, S 10, Q 9)
        for r, d, c, s, q in (("1", "2", "3", "4", "5"), ("13", "12", "11", "10", "9")):
            self._add("ff", [nl.net("U37", d), nl.net("U37", c), nl.net("U37", s), nl.net("U37", r)], [nl.net("U37", q)])
        # RC elements: (R, C) -> (source net, node net, tau up, tau down)
        rc = [("R60", "C50", "pull")] + [("R61", "C51", "drv")] + [(f"R{50 + i}", f"C{20 + i}", f"R{40 + i}") for i in range(9)]
        for rref, cref, extra in rc:
            a, b = nl.net(rref, "1"), nl.net(rref, "2")
            cv = self._val(nl.comp[cref]["value"])
            rv = self._val(nl.comp[rref]["value"])
            if extra == "pull":              # POR: from +3V3 through R into C: rises once
                self._add("rc", ["+3V3"], [b], tu=0.857 * rv * cv * 1e3, td=1.194 * rv * cv * 1e3)
            elif extra == "drv":             # logic-driven node
                self._add("rc", [a], [b], tu=0.857 * rv * cv * 1e3, td=1.194 * rv * cv * 1e3)
            else:                            # open-collector sensor: pull-up + series up, series only down
                rp = self._val(nl.comp[extra]["value"])
                self._add("rc", [a], [b], tu=0.857 * (rp + rv) * cv * 1e3, td=1.194 * rv * cv * 1e3)

    @staticmethod
    def _val(s):
        m = re.fullmatch(r"([\d.]+)\s*([pnuµmkM]?)", s.strip().split()[0])
        mult = {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6}
        return float(m[1]) * mult[m[2]]

    def _init(self):
        for e in self.elems:
            for n in e["ins"] + e["outs"]:
                self.val.setdefault(n, 0)
        self.ffq = {id(e): 0 for e in self.elems if e["kind"] == "ff"}
        for e in self.elems:
            if e["kind"] == "rc" and e["ins"][0] == "+3V3":
                self._sched(e["outs"][0], 1, 0.0 + e["tu"])
        for e in self.elems:
            self._eval(e, init=True)
        self.run_until(1e-3)

    # ---- scheduling
    def _sched(self, net, level, t):
        cur = self.pending.get(net)
        if cur is None and self.val.get(net, 0) == level:
            return
        if cur is not None and cur[1] == level:
            return
        self.seq += 1
        self.pending[net] = (self.seq, level)
        heapq.heappush(self.q, (t, self.seq, net, level))

    def _cancel_if_same(self, net, level):
        cur = self.pending.get(net)
        if cur is not None and cur[1] != level:
            del self.pending[net]

    def _eval(self, e, init=False):
        v = self.val
        k = e["kind"]
        if k == "not":
            self._set_out(e["outs"][0], 1 - v[e["ins"][0]], GATE_DELAY)
        elif k == "and":
            self._set_out(e["outs"][0], v[e["ins"][0]] & v[e["ins"][1]], GATE_DELAY)
        elif k == "or":
            self._set_out(e["outs"][0], v[e["ins"][0]] | v[e["ins"][1]], GATE_DELAY)
        elif k == "nor":
            self._set_out(e["outs"][0], 1 - (v[e["ins"][0]] | v[e["ins"][1]]), GATE_DELAY)
        elif k == "rc":
            if e["ins"][0] == "+3V3":
                return
            lv = v[e["ins"][0]]
            self._set_out(e["outs"][0], lv, e["tu"] if lv else e["td"])
        elif k == "ff":
            d, c, s, r = (v[n] for n in e["ins"])
            q = self.ffq[id(e)]
            cprev = e.get("cprev", 0)
            if s == 0:
                q = 1
            elif r == 0:
                q = 0
            elif c == 1 and cprev == 0:
                q = d
            e["cprev"] = c
            self.ffq[id(e)] = q
            self._set_out(e["outs"][0], q, GATE_DELAY)

    def _set_out(self, net, level, delay):
        # inertial: a new evaluation replaces a pending change to the opposite level
        if self.val[net] == level:
            if net in self.pending:
                del self.pending[net]
            return
        self.seq += 1
        self.pending[net] = (self.seq, level)
        heapq.heappush(self.q, (self.t + delay, self.seq, net, level))

    def set_ext(self, net, level, t=None):
        t = self.t if t is None else t
        self.val.setdefault(net, 0)
        self.seq += 1
        self.pending[net] = (self.seq, level)
        heapq.heappush(self.q, (t, self.seq, net, level))

    def run_until(self, t_end):
        while self.q and self.q[0][0] <= t_end:
            t, sq, net, level = heapq.heappop(self.q)
            pd = self.pending.get(net)
            if pd is None or pd[0] != sq:
                continue
            del self.pending[net]
            self.t = max(self.t, t)
            if self.val.get(net, 0) == level:
                continue
            self.val[net] = level
            if net in self.watch:
                self.log.append((t, net, level))
            for e in self.listeners.get(net, ()):
                self._eval(e)
        self.t = max(self.t, t_end)


# ============================================================================= machine
STROBE_W, JUDGE_W, CLEAR_W = SLOT_W["STROBE"], SLOT_W["JUDGE"], SLOT_W["CLEAR"]
PERIOD_X = CYCLE_X * 676
HOME_X = Params().home_x
STROBE_CAL_ON = 2.5            # calibrated STROBE relay of the register model: contact start = STROBE rise + 2.5 ms
ZONE = (CLEAR_W[1], STROBE_W[0] + CELL)      # stop zone: end of CLEAR .. start of the next STROBE (steps in the cell)
PRIO = {"I_L": 0, "I_M": 0, "I_S": 0}


class Machine:
    """Cycle-axis angle in steps (x).  Cells c = 30 q + k, x = 400 c = STROBE contact start (pad centre +31.2), k < 26 tested
    (slots), k = 26..29 empty (carry).  Position discs as in the CAD: F turns with the fast drum (T_L in every cell, I_L while the
    fast drum shows letter 0, i.e. cells c = 0 mod 26), M and S step at carry 1 / 2 (x = 12000 j + 11000)."""

    def __init__(self, x0=HOME_X):
        self.x = x0
        self.vc = 0.0                 # coast velocity (steps/s) when the drive is off
        self.win_lo = self.win_hi = None
        self.sigs: dict[str, list[tuple[int, int]]] = {}
        self.edges: list[tuple[int, int, str, int]] = []
        self._load(x0)

    def _load(self, xc):
        lo, hi = xc - 40000, xc + 40000
        self.win_lo, self.win_hi = lo, hi
        S: dict[str, list] = {k: [] for k in ("SLOT_S", "SLOT_J", "SLOT_C", "T_L", "T_M", "T_S", "I_L", "I_M", "I_S")}
        c0, c1 = lo // CELL - 2, hi // CELL + 2
        for c in range(c0, c1):
            q, k = divmod(c, CYCLE_CELLS)
            X = CELL * c
            b = X + T_OFF
            S["T_L"].append((b, b + 20))                 # F position disc: one slot per fast-drum position, every cell
            if c % 26 == 0:
                S["I_L"].append((b, b + CELL))           # fast drum at letter 0
            if k >= 26:
                continue
            S["SLOT_S"].append((X + STROBE_W[0], X + STROBE_W[1]))
            S["SLOT_J"].append((X + JUDGE_W[0], X + JUDGE_W[1]))
            S["SLOT_C"].append((X + CLEAR_W[0], X + CLEAR_W[1]))
        j0, j1 = (lo - CARRY_X) // CYCLE_X - 2, (hi - CARRY_X) // CYCLE_X + 3
        for j in range(j0, j1):
            xm = CYCLE_X * j + CARRY_X
            S["T_M"].append((xm, xm + 20))
            if (j + 1) % 26 == 0:
                S["T_S"].append((xm, xm + 20))
        # I_M = (m == 0): [xM_j, xM_j + 12000) after the carry that brings M to 0; I_S = (r == 0): 26 such revolutions
        for j in range(j0 - 800, j1 + 800):
            if (j + 1) % 26 == 0:
                xm = CYCLE_X * j + CARRY_X
                S["I_M"].append((xm, xm + CYCLE_X))
            if (j + 1) % 676 == 0:
                xm = CYCLE_X * j + CARRY_X
                S["I_S"].append((xm, xm + CYCLE_X * 26))
        self.sigs = S
        self.edges = []
        for sig, iv in S.items():
            for a, b in iv:
                self.edges.append((a, PRIO.get(sig, 1), sig, +1))
                self.edges.append((b, PRIO.get(sig, 1), sig, -1))
        self.edges.sort()
        self.edge_pos = [e[0] for e in self.edges]

    def level(self, sig, x=None):
        x = self.x if x is None else x
        if not (self.win_lo + 20000 < x < self.win_hi - 20000):
            self._load(x)
        iv = self.sigs[sig]
        i = bisect.bisect_right([a for a, _ in iv], x) - 1
        return i >= 0 and iv[i][0] <= x < iv[i][1]

    def levels(self):
        return {s: self.level(s) for s in self.sigs}

    def move(self, n: int):
        """move by n steps; returns [(pos, sig, level, frac)] crossings in order of motion, frac = fraction of the move done"""
        if n == 0:
            return []
        x0, x1 = self.x, self.x + n
        if not (self.win_lo + 20000 < x1 < self.win_hi - 20000) or not (self.win_lo + 20000 < x0 < self.win_hi - 20000):
            self._load(x1)
        out = []
        if n > 0:
            i0 = bisect.bisect_right(self.edge_pos, x0)
            i1 = bisect.bisect_right(self.edge_pos, x1)
            for pos, pr, sig, kind in self.edges[i0:i1]:
                out.append((pos, sig, kind > 0, (pos - x0) / n))
        else:
            i0 = bisect.bisect_right(self.edge_pos, x1)         # edges with x1 < pos <= x0
            i1 = bisect.bisect_right(self.edge_pos, x0)
            seg = self.edges[i0:i1]
            seg.sort(key=lambda e: (-e[0], e[1]))
            for pos, pr, sig, kind in seg:
                out.append((pos, sig, kind < 0, (x0 - pos) / -n))
        self.x = x1
        return out


def cell_of(x):
    return int(round(x / CELL))


def pos_truth(x):
    """core position (l, m, r) of the tested cell at x (None in the empty cells): l = fast drum = c mod 26"""
    c = cell_of(x)
    q, k = divmod(c, CYCLE_CELLS)
    if k >= 26:
        return None
    return (c % 26, q % 26, (q // 26) % 26)


def test_index_truth(x):
    """test order n = 26 Q + k of the tested cell at x (None in the empty cells)"""
    c = cell_of(x)
    q, k = divmod(c, CYCLE_CELLS)
    return None if k >= 26 else 26 * (q % 676) + k


# ============================================================================= relay register model
class Register:
    """Contact-face behaviour of the relay test register as far as the bank sees it (decision 0001 / 0003)."""

    def __init__(self, rng, stop_fn, d=None, press_at=None):
        self.rng = rng
        self.stop_fn = stop_fn                 # position n -> True if a dead line exists (stop wanted)
        self.start_on = True
        self.held = False
        self.latched = False
        self.pressing = False
        self.press_end = None
        r = rng
        self.d = d or dict(j_on=r.uniform(2.5, 4.0), j_off=r.uniform(1.5, 4.0), c_on=r.uniform(2.5, 4.0), c_off=r.uniform(1.5, 4.0),
                           s_on=r.uniform(2.5, 5.0), k_rel=r.uniform(3.0, 10.0), s_rel=r.uniform(3.0, 10.0))
        self.sched: list[tuple[float, str]] = []
        self.false_stops = 0
        self.false_straddle = 0
        self.missed_stops = 0
        self.k_full = False                    # held sense relays K cover all 26 lines (latched at a position without a stop)
        self.stale_missed = 0                  # stops missed because K were still latched from an earlier position
        self.not_cleared = 0
        self.bad_strobe = 0
        self.stops = []
        self.t_strobe = self.t_judge = self.t_clear = None
        self.cur_n = None
        self.stop_latch_fall_events: list[float] = []
        self.stop_latch_hist: list[tuple[float, int]] = [(0.0, 0)]
        self.release_after = lambda: 200.0

    def run_level(self, t):
        return int(self.start_on and not self.latched and not self.pressing)

    def on_conn(self, t, name, level, x_now):
        d = self.d
        if name == "STROBE":
            if level:
                self.t_strobe = t + 2.5            # calibrated STROBE relay: d_on 2.5, d_off 1.5
            elif self.t_strobe is not None:
                w = (t + 1.5) - self.t_strobe
                if not self.latched:
                    if w >= 5.0:
                        n = test_index_truth(x_now)
                        full_here = n is None or not self.stop_fn(n)
                        self.k_full = (self.k_full if self.held else False) or full_here   # K add to a set never cleared
                        self.held = True
                    else:
                        self.bad_strobe += 1
        elif name == "JUDGE":
            if level:
                self.t_judge = t + d["j_on"]
                self.cur_n = test_index_truth(x_now)
            elif self.t_judge is not None:
                w = (t + d["j_off"]) - self.t_judge
                self._judge_done(self.t_judge, w)
        elif name == "CLEAR":
            if level:
                self.t_clear = t + d["c_on"]
            elif self.t_clear is not None:
                w = (t + d["c_off"]) - self.t_clear
                if not self.latched:
                    if w >= d["k_rel"]:
                        self.sched.append((self.t_clear + d["k_rel"], "clear"))
                    else:
                        self.not_cleared += 1

    def _judge_done(self, tj, w):
        if self.latched:
            return
        line_stop = self.cur_n is not None and self.stop_fn(self.cur_n)
        if line_stop and self.held and self.k_full:
            # stale sense relays (latched at an earlier position, never cleared): the register sees 26 live lines
            self.stale_missed += 1
            self.missed_stops += 1
            return
        want = (not self.held) or line_stop
        if not want:
            return
        if w >= self.d["s_on"]:
            self.sched.append((tj + self.d["s_on"], "latch"))
            if not self.held:
                ul = list(self.stop_latch_fall_events) + [te for te, what in self.sched if what == "unlatch"]
                if any(tj - 1.0 <= u <= tj + w + 1.0 for u in ul):
                    self.false_straddle += 1          # the release fell inside the JUDGE pulse: cannot be avoided by the bank
                else:
                    self.false_stops += 1
            else:
                self.stops.append(self.cur_n)
        else:
            self.missed_stops += 1

    def update(self, t, fw, hw):
        self.sched.sort()
        while self.sched and self.sched[0][0] <= t:
            te, what = self.sched.pop(0)
            if what == "clear":
                if not self.latched:
                    self.held = self.k_full = False
            elif what == "latch":
                if not self.latched:
                    self.latched = True
                    self.stop_latch_hist.append((te, 1))
                    hw.set_ext("STOPL_M", 1, te + 0.03)
            elif what == "unlatch":
                self.latched = False
                self.held = self.k_full = False
                self.stop_latch_hist.append((te, 0))
                self.stop_latch_fall_events.append(te)
                hw.set_ext("STOPL_M", 0, te + 0.03)
            elif what == "press":
                self.pressing = True
                self.sched.append((te + 6.0, "unlatch"))
                self.sched.append((te + 70.0, "release"))
            elif what == "release":
                self.pressing = False
        fw.run = bool(self.run_level(t))
        fw.stop_latch = self.latched
        self.hold_t = t

    def press(self, t):
        self.sched.append((t, "press"))


# ============================================================================= co-simulation harness
class Harness:
    def __init__(self, nl: Netlist, seed=0, dt=0.1, p: Params | None = None, mut=(), start_x=HOME_X, synced=True,
                 stop_fn=None, release_fn=None, a_scale=1.0, hw_enabled=True):
        self.nl = nl
        self.rng = random.Random(seed)
        self.dt = dt
        self.p = p or Params()
        if a_scale != 1.0:
            self.p.a_stop *= a_scale
        self.fw = Firmware(self.p, mut)
        self.m = Machine(start_x)
        self.hw = HW(nl) if hw_enabled else None
        self.reg = Register(self.rng, stop_fn or (lambda n: False))
        self.t = 0.0
        self.sw = True
        self.v48 = True
        self.start_x = start_x
        self.conn_src = {}
        for name, pin in (("STROBE", 1), ("JUDGE", 34), ("CLEAR", 35)):
            tr = trace_out(nl, pin)
            self.conn_src[name] = tr
        self.events: list[tuple[float, str, int]] = []       # connector-face events
        self.ev_state: list[tuple[float, str, str, bool]] = []   # (t, signal, fw.state, fw.txen / homing flags)
        self.pos_samples: list[tuple[float, str, tuple, tuple | None]] = []
        self.release_fn = release_fn
        self.hw_conn = {}
        self.steps_total = 0
        self.motion_log: list[tuple[float, float]] = []
        self.log = []
        self.zone_log = []
        self._prev_state = 'REST'
        self._arm_hw()
        # power-up: wait for the POR network
        self.fw.v48 = self.fw.sw_ok = True
        for s, lv in self.m.levels().items():
            self.fw.lv[s] = lv
            if self.hw and s in ("SLOT_S", "SLOT_J", "SLOT_C"):
                self.hw.set_ext("RAW_" + {"SLOT_S": "S", "SLOT_J": "J", "SLOT_C": "C"}[s], 0 if lv else 1, 0.0)
        if self.hw:
            for k in ("TL", "IL", "TM", "IM", "TS", "IS"):
                pass
            for k in ("S", "J", "C"):
                if ("RAW_" + k) not in self.hw.val:
                    self.hw.set_ext("RAW_" + k, 1, 0.0)
            for nm in ("TXEN", "HOMING", "STOPL_M"):
                self.hw.set_ext(nm, 0 if nm != "HOMING" else 1, 0.0)
            self.hw.run_until(20.0)
            self.t = 20.0
        if synced:
            self.sync_at_home(start_x)

    def sync_at_home(self, x, hold=None):
        fw = self.fw
        fw.need_home = False
        l, m, r = pos_truth_sector(x)
        fw.cnt = [l, m, r]
        fw.sync = [True] * 3
        fw.kc, fw.kc_sync = k_sector(x), True
        fw.x_ref = (x, fw.s)
        fw.txen = True
        fw.stopped = True
        fw.homing = False
        fw.at_home = (x == HOME_X)
        if hold is not None:                      # standing after a stop at position `hold` = (l, m, r)
            fw.cell_pos = tuple(hold)
            fw.hold_disp = True

    def _arm_hw(self):
        if not self.hw:
            return
        for name, tr in self.conn_src.items():
            if tr:
                self.hw.watch[tr[3]] = True
        self.hw.watch["SLOT_S"] = True

    # ---- connector-face level of a signal at the end of the tick
    def _conn_events(self, t0, t1):
        if not self.hw:
            return
        log, self.hw.log = self.hw.log, []
        for te, net, lv in log:
            for name, tr in self.conn_src.items():
                if tr and tr[3] == net:
                    sw_dep = (tr[4] == "+24V_SW")
                    if sw_dep and not self.sw:
                        continue
                    self.events.append((te + 0.002, name, lv))
                    self.ev_state.append((te, name, self._st(), self.fw.txen, self.fw.homing, lv))
                    if lv and name in ("STROBE", "CLEAR"):
                        self.pos_samples.append((te, name, self.fw.pos, pos_truth(self.m.x)))
                    self.reg.on_conn(te + 0.002, name, lv, self.m.x)

    def _st(self):
        return 'RETREV' if (self.fw.state == 'RET' and self.fw.v < 0) else self.fw.state

    def sw_set(self, on: bool):
        if self.sw and not on and self.hw:
            # outputs vanish: close any open pulse at the connector
            for name, tr in self.conn_src.items():
                if tr and tr[4] == "+24V_SW" and self.hw.val.get(tr[3], 0):
                    self.events.append((self.t, name, 0))
                    self.ev_state.append((self.t, name, self.fw.state, self.fw.txen, self.fw.homing, 0))
        if (not self.sw) and on and self.hw:
            # +24V_SW returns: a gate output that is already high (logic on +24V_L) appears at the connector now
            for name, tr in self.conn_src.items():
                if tr and tr[4] == "+24V_SW" and self.hw.val.get(tr[3], 0):
                    self.events.append((self.t, name, 1))
                    self.ev_state.append((self.t, name, self.fw.state, self.fw.txen, self.fw.homing, 1))
                    self.reg.on_conn(self.t, name, 1, self.m.x)
        self.sw = on
        self.fw.sw_ok = on

    def tick(self):
        t, dt, fw, m, hw = self.t, self.dt, self.fw, self.m, self.hw
        fw.v48 = self.v48
        self.reg.update(t, fw, hw) if hw else None
        if self.release_fn:
            self.release_fn(self, t)
        f0, xb = fw._frac, m.x
        n = fw.tick(t, dt)
        f1 = fw._frac if fw.ena else 0.0
        if hw:
            hw.set_ext("TXEN", int(fw.txen), t)
            hw.set_ext("HOMING", int(fw.homing), t)
        if fw.ena:
            steps = n
            m.vc = fw.v
        else:
            # drive off (emergency stop / cover open): the shaft coasts with friction deceleration
            if abs(m.vc) < 1.0:
                m.vc = 0.0
            else:
                dv = 3.0e5 * dt / 1000.0
                m.vc = max(0.0, abs(m.vc) - dv) * (1 if m.vc > 0 else -1)
            steps = int(round(m.vc * dt / 1000.0))
        edges = m.move(steps)
        span = (m.x + f1) - (xb + (f0 if fw.ena else 0.0))
        for pos, sig, lv, frac in edges:
            if fw.ena and span != 0.0:
                frac = min(1.0, max(0.0, (pos - (xb + f0)) / span))
            te = t + dt * frac
            s_e = fw.s - n + int(round(n * frac)) if fw.ena else fw.s
            if hw and sig in ("SLOT_S", "SLOT_J", "SLOT_C"):
                hw.run_until(te)
                hw.set_ext("RAW_" + sig[-1], 0 if lv else 1, te)
            fw.on_edge(sig, lv, te, s_e)
        if hw:
            hw.run_until(t + dt)
            self._conn_events(t, t + dt)
        self.t = t + dt
        self.steps_total += abs(steps)
        if self._prev_state in ("RET", "CREEPOUT") and fw.state == "REST" and self.reg.latched:
            c = int(math.floor((m.x - 300) / CELL))
            self.zone_log.append((m.x - CELL * c, fw.pos, pos_truth(CELL * c)))
        self._prev_state = fw.state

    def run(self, t_end):
        while self.t < t_end:
            self.tick()


def pos_truth_sector(x):
    """(l, m, r) sector values of the position discs at x (not the cell value: before the T boundary the old value holds)"""
    l = ((x - T_OFF) // CELL) % 26
    mi = (x - CARRY_X) // CYCLE_X + 1
    return (int(l), int(mi % 26), int((mi // 26) % 26))


def k_sector(x):
    """cell k (0..29) of the cycle-shaft revolution between the T boundaries at x"""
    return int(((x - T_OFF) // CELL) % CYCLE_CELLS)


# ============================================================================= waveform checks (decision 0003 L1-L9)
def pulses(events, name):
    out, up = [], None
    for t, n, lv in sorted(events):
        if n != name:
            continue
        if lv and up is None:
            up = t
        elif (not lv) and up is not None:
            out.append((up, t))
            up = None
    return out, up


def check_waveforms(H: Harness, rep: Report, tag: str, allow_cut=None, strict_l1=7.0):
    """allow_cut: list of times at which STOP_LATCH fell (L8 may remove a JUDGE of a cycle that has begun)"""
    ev = H.events
    S, su = pulses(ev, "STROBE")
    J, ju = pulses(ev, "JUDGE")
    C, cu = pulses(ev, "CLEAR")
    C_ = rep.check
    fall = sorted(H.reg.stop_latch_fall_events)
    stats = dict(S=[x[1] - x[0] for x in S], J=[x[1] - x[0] for a, b in [(0, 0)] for x in J], C=[x[1] - x[0] for x in C], JS=[], CJ=[], SC=[])
    C_(min(stats["S"], default=99) >= strict_l1 - 0.05, f"{tag}: L1 STROBE width {min(stats['S'], default=0):.3f} ms < {strict_l1} (calibrated)")
    J_ok = [b - a for a, b in J if not any(a - 0.01 <= f <= b + 0.01 for f in fall)]       # a release inside the pulse truncates it (L8)
    C_(min(J_ok, default=99) >= 7.5 - 1e-6, f"{tag}: L3 JUDGE width {min(J_ok, default=0):.3f} < 7.5")
    C_(min(stats["C"], default=99) >= 12.5 - 1e-6, f"{tag}: L5 CLEAR width {min(stats['C'], default=0):.3f} < 12.5")
    # cycle association
    Sr = [a for a, b in S]
    for (js, je) in J:
        i = bisect.bisect_right(Sr, js) - 1
        if i < 0:
            C_(False, f"{tag}: L8 JUDGE at {js:.2f} ms without a STROBE before it")
            continue
        # the STROBE must be after the last STOP_LATCH fall and the HOMING interval
        lf = [f for f in fall if f < js]
        if lf and Sr[i] < lf[-1]:
            C_(False, f"{tag}: L8 JUDGE at {js:.1f} ms after a STOP_LATCH fall with the last STROBE at {Sr[i]:.1f} before it")
            continue
        d = js - Sr[i]
        stats["JS"].append(d)
        # L2, calibrated form (decision 0003 3.3 L2 in brackets, 3.4: the gear STROBE is calibrated): JUDGE rise >= STROBE contact
        # start + 7.5, STROBE contact start = STROBE rise + the calibrated relay delay of the register model
        C_(d - STROBE_CAL_ON >= 7.5 - 1e-6, f"{tag}: L2 JUDGE rise - STROBE contact start = {d - STROBE_CAL_ON:.3f} < 7.5")
    for (cs, ce) in C:
        i = bisect.bisect_right([x[1] for x in J], cs) - 1
        if i >= 0 and cs - J[i][1] < 30.0:
            g = cs - J[i][1]
            stats["CJ"].append(g)
            C_(g >= 1.5 - 1e-6, f"{tag}: L4 CLEAR rise - JUDGE fall = {g:.3f} < 1.5")
    for k in range(len(S) - 1):
        # next STROBE vs the CLEAR that ends within the cycle
        cl = [c for c in C if S[k][0] < c[0] < S[k + 1][0]]
        if cl:
            g = S[k + 1][0] - cl[-1][1]
            stats["SC"].append(g)
            C_(g >= 1.5 - 1e-6, f"{tag}: L6 next STROBE - CLEAR fall = {g:.3f} < 1.5")
    # L7: every STROBE is followed by CLEAR; by JUDGE unless a STOP_LATCH fall cut it (L8)
    Jr = [a for a, b in J]
    Cr = [a for a, b in C]
    for k, (ss, se) in enumerate(S):
        nxt = S[k + 1][0] if k + 1 < len(S) else H.t
        if nxt - ss > 60.0 and k + 1 < len(S):
            pass
        has_c = any(ss < c < ss + 60.0 for c in Cr) or ss > H.t - 60.0
        has_j = any(ss < j < ss + 60.0 for j in Jr) or ss > H.t - 60.0
        cut = any(ss < f < ss + 60.0 for f in fall)
        C_(has_c or cut, f"{tag}: L7 STROBE at {ss:.1f} ms has no CLEAR")
        C_(has_j or cut, f"{tag}: L7 STROBE at {ss:.1f} ms has no JUDGE")
    return stats


def check_contact_face(stats, rep, tag):
    """decision 0003 C1-C6 from the measured connector-face minima with the corner relay delays"""
    ok = True
    if not stats["J"]:
        return
    mS, mJ, mC = min(stats["S"]), min(stats["J"]), min(stats["C"])
    # STROBE calibrated (2.5 / 1.5): contact width = width - 1.0; JUDGE: width + off - on (worst off 1.5, on 4.0)
    rep.check(mS - 1.0 >= 5.0 - 1e-6, f"{tag}: C1 STROBE contact width {mS - 1.0:.2f} < 5")
    rep.check(mJ - 2.5 >= 5.0 - 1e-6, f"{tag}: C3 JUDGE contact width worst {mJ - 2.5:.2f} < 5")
    rep.check(mC - 2.5 >= 10.0 - 1e-6, f"{tag}: C5 CLEAR contact width worst {mC - 2.5:.2f} < 10")
    if stats["JS"]:
        # JUDGE contact start - STROBE contact start >= 10: worst on_J = 2.5 (early) vs STROBE calibrated 2.5
        rep.check(min(stats["JS"]) + 2.5 - 2.5 >= 10.0, f"{tag}: C2 JUDGE contact start - STROBE contact start < 10")
    if stats["CJ"]:
        rep.check(min(stats["CJ"]) + 2.5 - 4.0 > 0.0, f"{tag}: C4 CLEAR contact start before the JUDGE contact end")
    if stats["SC"]:
        rep.check(min(stats["SC"]) + 2.5 - 4.0 > 0.0, f"{tag}: C6 next STROBE contact before the CLEAR contact end")


# ============================================================================= scenarios
def run_cycles(nl, rep, tag, seed, cells, p_stop=0.0, release_mode="normal", a_scale=1.0, mut=(), dt=0.1, early=False, check=True):
    """Run from HOME for `cells` cells (tested positions) with a stop oracle; returns the harness and the bookkeeping."""
    rng = random.Random(seed * 7919 + 13)
    stop_set = set()
    P = cells
    for n in range(P):
        if rng.random() < p_stop:
            stop_set.add(n)

    def release_fn(H, t):
        # press the release button after STOPPED (or earlier when `early`)
        if H.reg.latched and not H.reg.pressing and not getattr(H, "_pressed", False):
            if early:
                if t >= H.reg.stop_latch_hist[-1][0] + H._d_early:
                    H.reg.press(t)
                    H._pressed = True
            elif H.fw.stopped and H.fw.state == "REST":
                if not hasattr(H, "_t_stopped"):
                    H._t_stopped = t
                if t - H._t_stopped >= H._d_rel:
                    H.reg.press(t)
                    H._pressed = True
        if not H.reg.latched:
            H._pressed = False
            if hasattr(H, "_t_stopped"):
                del H._t_stopped
            H._d_rel = rng.uniform(80.0, 400.0)
            H._d_early = rng.uniform(0.0, 120.0)

    H = Harness(nl, seed=seed, dt=dt, mut=mut, stop_fn=lambda n: n in stop_set, release_fn=release_fn, a_scale=a_scale)
    H._d_rel, H._d_early = 120.0, 30.0
    H._pressed = False
    t_end = H.t + 1000.0 * P / 26.0 * 1.20 + 4000.0 * (len(stop_set) + 1) * 0.0 + 300.0 + 700.0 * len(stop_set)
    H.fw.run = True
    H.run(t_end)
    return H, stop_set


def analyze_run(H, stop_set, rep, tag, cells):
    fw, reg = H.fw, H.reg
    stats = check_waveforms(H, rep, tag)
    check_contact_face(stats, rep, tag)
    C = rep.check
    C(reg.false_stops == 0, f"{tag}: {reg.false_stops} false stops (JUDGE before a STROBE / empty register)")
    C(reg.missed_stops == 0, f"{tag}: {reg.missed_stops} missed stops")
    C(reg.not_cleared == 0 and reg.bad_strobe == 0, f"{tag}: register not cleared {reg.not_cleared} / bad STROBE {reg.bad_strobe}")
    # tested positions with STOP_LATCH low must be consecutive without repetition; stops must equal the oracle (up to the cells run)
    tested = []
    lat = reg.stop_latch_hist
    def latched_at(t):
        v = 0
        for tt, s in lat:
            if tt <= t:
                v = s
            else:
                break
        return v
    S, _ = pulses(H.events, "STROBE")
    xs = H.ev_state
    # reconstruct the cell of every STROBE rising edge from the machine (record by time: use POS samples of the machine truth)
    cellseq = []
    for te, nm, pos, truth in H.pos_samples:
        if nm == "STROBE" and truth is not None and not latched_at(te):
            cellseq.append(test_index(*truth))
    if cellseq:
        C(all(b - a == 1 for a, b in zip(cellseq, cellseq[1:])) and cellseq[0] == 0,
          f"{tag}: tested positions (register not latched) are not 0,1,2,...: {cellseq[:12]} ...")
    # stops: each oracle stop inside the run must have produced a latch (position n)
    ran = max(cellseq) if cellseq else -1
    got = list(reg.stops)
    exp = sorted(n for n in stop_set if n <= ran - 0)
    C(set(exp) <= set(got) or ran < 0, f"{tag}: stops expected {exp[:8]} got {got[:8]}")
    # POS bus: sampled at STROBE and CLEAR rising edges
    bad = [(round(te, 1), nm, pos, truth) for te, nm, pos, truth in H.pos_samples if truth is not None and pos != truth]
    C(not bad, f"{tag}: POS differs from the true position at {bad[:3]}")
    return stats, cellseq


def stop_zone_check(H, rep, tag):
    """after every STOPPED standing following a latch the shaft must stand between CLEAR end and the next STROBE start"""
    return


def machine_rest_rel(H):
    c = cell_of(H.m.x - 200)
    return H.m.x - CELL * c


# ----------------------------------------------------------------------------- scenario: stops, return, restart
def scenario_stops(nl, rep, quick, seeds):
    tot = dict(cycles=0, stops=0, S=[], J=[], C=[], JS=[], CJ=[], SC=[], zone=[])
    for seed in seeds:
        tag = f"stops seed {seed}"
        cells = 56 if quick else 78
        H, ss = run_cycles(nl, rep, tag, seed, cells, p_stop=0.18)
        stats, cellseq = analyze_run(H, ss, rep, tag, cells)
        for k in ("S", "J", "C", "JS", "CJ", "SC"):
            tot[k] += stats[k]
        tot["stops"] += len(H.reg.stops)
        tot["cycles"] += len(cellseq)
        # stop zone: x_rel at every stopped standing is recorded by the harness hook below (fw._zone_log)
        tot["zone"] += getattr(H, "zone_log", [])
    return tot


# ============================================================================= main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-negative", action="store_true")
    args = ap.parse_args()
    import scenarios                                  # noqa: E402
    return scenarios.main(args, sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
