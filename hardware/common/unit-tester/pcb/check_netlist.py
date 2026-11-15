#!/usr/bin/env python3
"""Netlist verification of the unit tester main board.

Exports the netlist with kicad-cli (or reads one with --netlist) and checks, independently of the
generator code:

  1. every node channel 0..51: 595 output bit -> TBD62783A input/output -> 1k series R -> NODE net ->
     (connector pin) and (2.2k -> TLP291-4 LED -> emitter -> SENSE net -> 74HC165 bit), with the
     shift-register bit order of firmware/hal_pico.py (chain position, Q pin / D pin);
  2. the Pico pin map against the numbers found in firmware/hal_pico.py;
  3. connector pinout J_A / J_B / J_C against the table in README.md 3.4;
  4. the isolation domains: no net may touch both the FIELD and the MCU domain (only the isolators and
     optocouplers may straddle), passives never bridge domains;
  5. spare / aux bits (POS, flags, JUDGE, CLEAR, STOP_LATCH) reach the HAL bit positions;
  6. decision 0003 items T1-T4, T7, T8: pin connectors, resettable fuses on RUN/HOME, ESTOP/ILOCK loops
     returned by jumpers, STOP_LATCH on 165 bit 74, CHASSIS_F pair, series P-MOSFET at the power input.

    python check_netlist.py [--netlist nl.xml] [--bom] [--selftest]

--selftest runs mutated copies of the netlist through the checks (they must fail).

Exit status 0 = all checks pass (documented HAL deviations are reported as NOTE, not as failures).
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTER = HERE.parent
KICAD_CLI = Path(os.environ.get("KICAD_CLI", "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"))
N_NODES = 52

Q_PINS = ["15", "1", "2", "3", "4", "5", "6", "7"]          # 74HC595 QA..QH
D_PINS = ["11", "12", "13", "14", "3", "4", "5", "6"]       # 74HC165 D0..D7 (A..H)


# ----------------------------------------------------------------------------- netlist model
class Netlist:
    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        self.comp: dict[str, dict] = {}
        for c in root.find("components"):
            self.comp[c.get("ref")] = dict(
                value=(c.findtext("value") or ""), fp=(c.findtext("footprint") or ""),
                sheet=(c.find("sheetpath").get("names") if c.find("sheetpath") is not None else ""),
                desc=(c.findtext("description") or ""), ds=(c.findtext("datasheet") or ""),
                dnp=any(p.get("name") == "dnp" for p in c.findall("property")))
        self.nets: dict[str, list[tuple[str, str, str]]] = {}
        self.pin_net: dict[tuple[str, str], str] = {}
        self.pin_func: dict[tuple[str, str], str] = {}
        for n in root.find("nets"):
            name = n.get("name")
            lst = []
            for nd in n:
                ref, pin, fn = nd.get("ref"), nd.get("pin"), nd.get("pinfunction") or ""
                lst.append((ref, pin, fn))
                self.pin_net[(ref, pin)] = name
                self.pin_func[(ref, pin)] = fn
            self.nets[name] = lst

    def net(self, ref: str, pin: str) -> str | None:
        return self.pin_net.get((ref, pin))

    def pins_on(self, net: str) -> list[tuple[str, str]]:
        return [(r, p) for r, p, _ in self.nets.get(net, [])]

    def other_pin(self, ref: str, pin: str) -> str | None:
        """net on the other pin of a 2-terminal part"""
        o = "2" if pin == "1" else "1"
        return self.net(ref, o)


def export_netlist(dest: Path) -> None:
    sch = HERE / "unit_tester.kicad_sch"
    r = subprocess.run([str(KICAD_CLI), "sch", "export", "netlist", "--format", "kicadxml", "-o", str(dest), str(sch)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise SystemExit(f"kicad-cli netlist export failed: {r.stdout}{r.stderr}")


# ----------------------------------------------------------------------------- reporting
class Report:
    def __init__(self):
        self.fail: list[str] = []
        self.note: list[str] = []
        self.ok = collections.Counter()

    def check(self, cond: bool, section: str, msg: str):
        if cond:
            self.ok[section] += 1
        else:
            self.fail.append(f"[{section}] {msg}")

    def info(self, msg: str):
        self.note.append(msg)


# ----------------------------------------------------------------------------- chain tracing
def shift_chain_595(nl: Netlist, rep: Report) -> list[str]:
    """595 refs ordered from the chip fed by MOSI_F (position 0) outwards."""
    chips = sorted(r for r in nl.comp if nl.comp[r]["value"] == "74HC595")
    first = [r for r in chips if nl.net(r, "14") == "MOSI_F"]
    rep.check(len(first) == 1, "595", f"exactly one 595 with SER on MOSI_F (found {first})")
    order = [first[0]]
    while True:
        qh = nl.net(order[-1], "9")
        nxt = [r for r in chips if r != order[-1] and nl.net(r, "14") == qh and qh and not qh.startswith("unconnected")]
        if not nxt:
            break
        order.append(nxt[0])
    rep.check(len(order) == 7 == len(chips), "595", f"595 chain has 7 chips (found {order})")
    for r in order:
        rep.check(nl.net(r, "11") == "SCK_F" and nl.net(r, "12") == "RCLK_F" and nl.net(r, "13") == "OE_F"
                  and nl.net(r, "10") == "+5V_F", "595", f"{r} SCK/RCLK/OE/SRCLR wiring")
    return order


def shift_chain_165(nl: Netlist, rep: Report) -> list[str]:
    """165 refs ordered from the chip whose Q7 goes to MISO_165 (position 0 = highest bits)."""
    chips = sorted(r for r in nl.comp if nl.comp[r]["value"] == "74HC165")
    last = [r for r in chips if nl.net(r, "9") == "MISO_165"]
    rep.check(len(last) == 1, "165", f"exactly one 165 with Q7 on MISO_165 (found {last})")
    order = [last[0]]
    while True:
        ds = nl.net(order[-1], "10")
        prev = [r for r in chips if r != order[-1] and nl.net(r, "9") == ds]
        if not prev:
            break
        order.append(prev[0])
    rep.check(len(order) == 10 == len(chips), "165", f"165 chain has 10 chips (found {order})")
    rep.check(nl.net(order[-1], "10") == "GND_M", "165", "DS of the last 165 (bits 0-7) is tied low")
    for r in order:
        rep.check(nl.net(r, "1") == "LD_165" and nl.net(r, "2") == "SCK_165" and nl.net(r, "15") == "GND_M"
                  and nl.net(r, "16") == "+3V3", "165", f"{r} PL/CP/CE/VCC wiring")
    return order


# ----------------------------------------------------------------------------- 1. node channels
def check_nodes(nl: Netlist, rep: Report):
    ch595 = shift_chain_595(nl, rep)
    ch165 = shift_chain_165(nl, rep)
    # bit -> 595 chip position k (0 = nearest MOSI): bits 8k..8k+7 ; HAL: first byte sent -> farthest chip
    # bit -> 165 position q from MISO: chip holds bits 8*(9-q)..8*(9-q)+7
    for n in range(N_NODES):
        sec = "node"
        chip = ch595[n // 8]
        qnet = nl.net(chip, Q_PINS[n % 8])
        rep.check(qnet == f"DRV_IN_{n}", sec, f"node {n}: 595 {chip} Q{'ABCDEFGH'[n % 8]} net {qnet} != DRV_IN_{n}")
        # driver
        drv = [(r, p) for r, p in nl.pins_on(f"DRV_IN_{n}") if nl.comp[r]["value"].startswith("TBD62783")]
        ok = len(drv) == 1
        rep.check(ok, sec, f"node {n}: exactly one TBD62783A input on DRV_IN_{n}")
        if not ok:
            continue
        dref, dpin = drv[0]
        ch = int(dpin)                                  # channel 1..8
        rep.check(ch == n % 8 + 1 and dref == f"U{20 + n // 8}", sec, f"node {n}: driver {dref} ch {ch} (expect U{20 + n // 8} ch {n % 8 + 1})")
        rep.check(nl.net(dref, "9") == "+24V_F" and nl.net(dref, "10") == "GND_F", sec, f"node {n}: driver supply")
        onet = nl.net(dref, str(19 - ch))
        rs = [(r, p) for r, p in nl.pins_on(onet) if r.startswith("R")]
        rep.check(len(rs) == 1 and nl.comp[rs[0][0]]["value"].startswith("1k"), sec,
                  f"node {n}: driver output {onet} must feed exactly one 1k series resistor (got {rs})")
        node_net = nl.other_pin(*rs[0]) if rs else None
        rep.check(node_net == f"NODE_{n}", sec, f"node {n}: series resistor output net {node_net}")
        # connector pin
        conn = [(r, p) for r, p in nl.pins_on(f"NODE_{n}") if r in ("J1", "J2")]
        want = ("J1", str(n + 1)) if n < 26 else ("J2", str(n - 25))
        rep.check(conn == [want], sec, f"node {n}: connector pad {conn} != {want}")
        # sense: node -> 2.2k -> opto LED
        s2k = [(r, p) for r, p in nl.pins_on(f"NODE_{n}") if r.startswith("R") and nl.comp[r]["value"] == "2.2k"]
        rep.check(len(s2k) == 1, sec, f"node {n}: exactly one 2.2k sense resistor on NODE_{n}")
        if len(s2k) != 1:
            continue
        sns = nl.other_pin(*s2k[0])
        opto = [(r, p) for r, p in nl.pins_on(sns) if nl.comp[r]["value"] == "TLP291-4"]
        rep.check(len(opto) == 1, sec, f"node {n}: sense net {sns} reaches one TLP291-4 pin")
        oref, apin = opto[0]
        a = int(apin)
        unit = (a + 1) // 2
        rep.check(a % 2 == 1 and nl.net(oref, str(a + 1)) == "GND_F", sec, f"node {n}: opto LED anode/cathode ({oref} {apin})")
        rep.check(any(nl.comp[r]["value"] == "1N4148W" for r, _ in nl.pins_on(sns)), sec, f"node {n}: LED reverse diode")
        coll, emit = str(18 - 2 * unit), str(17 - 2 * unit)
        rep.check(nl.net(oref, coll) == "+3V3", sec, f"node {n}: opto collector on +3V3")
        enet = nl.net(oref, emit)
        rep.check(enet == f"SENSE_{n}", sec, f"node {n}: opto emitter net {enet}")
        pd = [(r, p) for r, p in nl.pins_on(enet) if r.startswith("R") and nl.other_pin(r, p) == "GND_M" and nl.comp[r]["value"] == "10k"]
        rep.check(len(pd) == 1, sec, f"node {n}: 10k pull-down on {enet}")
        # 165 bit position
        ic = [(r, p) for r, p in nl.pins_on(enet) if nl.comp[r]["value"] == "74HC165"]
        rep.check(len(ic) == 1, sec, f"node {n}: {enet} reaches exactly one 74HC165 input")
        if ic:
            r165, p165 = ic[0]
            q = ch165.index(r165)
            bit = 8 * (9 - q) + D_PINS.index(p165)
            rep.check(bit == n, sec, f"node {n}: 165 {r165} pin {p165} = bit {bit}")


# ----------------------------------------------------------------------------- 5. aux bits
def check_aux(nl: Netlist, rep: Report):
    ch165 = shift_chain_165(nl, rep)
    expect = {}
    for k in range(15):
        expect[52 + k] = (f"POS{k}_IN", 7 + k)
    expect.update({67: ("STOPPED_IN", 3), 68: ("AT_HOME_IN", 5), 69: ("END_IN", 6), 70: ("ESTOP_RET", 23),
                   71: ("ILOCK_RET", 25), 72: ("JUDGE_IN", 34), 73: ("CLEAR_IN", 35), 74: ("STOP_LATCH_IN", 36)})
    for bit, (net, jpin) in expect.items():
        rep.check(("J3", str(jpin)) in nl.pins_on(net), "aux", f"bit {bit}: {net} on J_C pin {jpin}")
        s = [(r, p) for r, p in nl.pins_on(net) if r.startswith("R") and nl.comp[r]["value"] == "2.2k"]
        ok = len(s) == 1
        rep.check(ok, "aux", f"bit {bit}: one 2.2k on {net}")
        if not ok:
            continue
        sns = nl.other_pin(*s[0])
        opto = [(r, p) for r, p in nl.pins_on(sns) if nl.comp[r]["value"] == "TLP291-4"]
        oref, apin = opto[0]
        unit = (int(apin) + 1) // 2
        enet = nl.net(oref, str(17 - 2 * unit))
        ic = [(r, p) for r, p in nl.pins_on(enet) if nl.comp[r]["value"] == "74HC165"]
        r165, p165 = ic[0]
        got = 8 * (9 - ch165.index(r165)) + D_PINS.index(p165)
        rep.check(got == bit, "aux", f"{net}: 165 bit {got} != {bit}")
    for bit in range(75, 80):
        c = ch165[9 - bit // 8]
        rep.check(nl.net(c, D_PINS[bit % 8]) == "GND_M", "aux", f"spare bit {bit} tied to GND_M")
    # HAL: flags at FLAG_SHIFT=67 (STOPPED, AT_HOME, END, ESTOP_OK, ILOCK_OK, JUDGE, CLEAR, STOP_LATCH), POS at 52
    hal = (TESTER / "firmware" / "hal_pico.py").read_text(encoding="utf8")
    rep.check(re.search(r"POS_SHIFT = 52", hal) is not None and re.search(r"FLAG_SHIFT = 67", hal) is not None,
              "aux", "hal_pico.py POS_SHIFT=52 / FLAG_SHIFT=67 as assumed")
    rep.check(re.search(r"v >> FLAG_SHIFT & 0xFF", hal) is not None, "aux", "hal_pico.py reads 8 flag bits (67..74, incl. STOP_LATCH on bit 74)")
    # RUN/HOME 24 V outputs (0003 T2: resettable fuse instead of the 1k series resistor)
    for nm, hnet, jp in (("RUN", "RUN_F", 2), ("HOME", "HOME_F", 4)):
        drv = [(r, p) for r, p in nl.pins_on(hnet) if nl.comp[r]["value"].startswith("TBD")]
        rep.check(len(drv) == 1, "aux", f"{nm}: isolator output {hnet} feeds one TBD62783A input")
        iso = [(r, p) for r, p in nl.pins_on(hnet) if r == "U3"]
        rep.check(bool(iso), "aux", f"{nm}: {hnet} driven by the fail-safe-low isolator U3")
        o = nl.net(drv[0][0], str(19 - int(drv[0][1])))
        rs = [(r, p) for r, p in nl.pins_on(o) if r.startswith("F")]
        rep.check(len(rs) == 1 and nl.other_pin(*rs[0]) == f"{nm}_OUT" and ("J3", str(jp)) in nl.pins_on(f"{nm}_OUT"), "aux",
                  f"{nm}: driver -> resettable fuse -> {nm}_OUT -> J_C pin {jp}")
        rep.check(len(rs) == 1 and nl.comp[rs[0][0]]["value"].startswith("PTC 30mA")
                  and "Fuse" in nl.comp[rs[0][0]]["fp"], "aux", f"{nm}: output element is a 30 mA PTC, not a resistor")


# ----------------------------------------------------------------------------- 6. decision 0003
def check_0003(nl: Netlist, rep: Report):
    sec = "0003"
    # T1: board connectors are pins (male)
    for ref in ("J1", "J2", "J3"):
        rep.check("DSUB-37_Pins_Horizontal" in nl.comp[ref]["fp"], sec, f"T1: {ref} footprint is a D-sub PIN connector ({nl.comp[ref]['fp']})")
    # T3: ESTOP / ILOCK loops: bank feeds 22 / 24, jumper returns them to 23 / 25, sensed there; optional feed jumpers
    for a, ret, jp, feed, fr, pa, pr in (("ESTOP_A", "ESTOP_RET", "JP2", "JP4", "R404", 22, 23),
                                         ("ILOCK_A", "ILOCK_RET", "JP3", "JP5", "R405", 24, 25)):
        rep.check(nl.net("J3", str(pa)) == a and nl.net("J3", str(pr)) == ret, sec, f"T3: J_C {pa}/{pr} are {a}/{ret}")
        rep.check({nl.net(jp, "1"), nl.net(jp, "2")} == {a, ret} and "Bridged" in nl.comp[jp]["fp"], sec,
                  f"T3: {jp} is a bridged (default closed) jumper between {a} and {ret}")
        rep.check(nl.net(feed, "2") == a and "Open" in nl.comp[feed]["fp"] and nl.net(feed, "1") == nl.net(fr, "2")
                  and nl.net(fr, "1") == "+24V_F" and nl.comp[fr]["value"] == "2.2k", sec,
                  f"T3: {feed} (default open) feeds {a} from +24V_F through {fr} 2.2k")
        rep.check(not any(r.startswith("R") and nl.comp[r]["value"] == "2.2k" and nl.other_pin(r, p) == "+24V_F"
                          for r, p in nl.pins_on(a)), sec, f"T3: {a} is not fed from +24V_F without the (open) jumper")
    # T4: STOP_LATCH on J_C 36 -> 2.2k -> optocoupler -> 165 bit 74; pin 37 reserved
    rep.check(nl.net("J3", "36") == "STOP_LATCH_IN" and nl.net("J3", "37").startswith("unconnected"), sec,
              "T4: J_C 36 = STOP_LATCH_IN, 37 reserved")
    # T7: CHASSIS_F pair (the shells are footprint pads SH and have no schematic pin; gen_pcb.py puts them on this net)
    members = sorted(nl.pins_on("CHASSIS_F"))
    rep.check(members == [("C237", "1"), ("R408", "1")] and nl.net("R408", "2") == "GND_F" and nl.net("C237", "2") == "GND_F"
              and nl.comp["R408"]["value"] == "1M" and nl.comp["C237"]["value"].startswith("10n"), sec,
              f"T7: CHASSIS_F joined to GND_F by R408 1M || C237 10n only (members {members})")
    # T8: series P-MOSFET: J4 + -> drain; source -> +24V_IN (F1, F2, TVS D1); gate zener + pull-down
    j4 = nl.net("J4", "1")
    plus = nl.net("Q1", "3")
    ok = (nl.net("Q1", "2") == j4 and plus == "+24V_IN" and j4 != plus and nl.net("F1", "1") == plus
          and nl.net("F2", "1") == plus and nl.net("D1", "1") == plus
          and nl.net("DZ1", "1") == plus and nl.net("DZ1", "2") == nl.net("Q1", "1") == nl.net("R412", "1")
          and nl.net("R412", "2") == "GND_F" and "SOT-223" in nl.comp["Q1"]["fp"])
    rep.check(ok, sec, "T8: J4 -> Q1 drain, Q1 source = +24V_IN feeds F1/F2/TVS, gate zener DZ1 + R412 pull-down "
                       f"(J4.1 on {j4}, Q1.3 on {plus})")
    rep.check(all(nl.net(r, p) != "+24V_IN" for r, p in (("J4", "1"),)), sec, "T8: the screw terminal is not on +24V_IN directly")


# ----------------------------------------------------------------------------- 2. Pico vs HAL
def parse_hal() -> dict[str, int]:
    t = (TESTER / "firmware" / "hal_pico.py").read_text(encoding="utf8")
    exp: dict[str, int] = {}

    def pin(pattern, name):
        m = re.search(pattern, t, re.S)
        if not m:
            raise SystemExit(f"hal_pico.py: cannot find {name}")
        exp[name] = int(m[1])
    pin(r"self\.oe = machine\.Pin\((\d+)", "OE_M")
    pin(r"self\.rclk = machine\.Pin\((\d+)", "RCLK_M")
    pin(r"out_spi = machine\.SPI\(0.*?sck=machine\.Pin\((\d+)", "SCK_M")
    pin(r"out_spi = machine\.SPI\(0.*?mosi=machine\.Pin\((\d+)", "MOSI_M")
    pin(r"self\.ld = machine\.Pin\((\d+)", "LD_165")
    pin(r"in_spi = machine\.SPI\(1.*?sck=machine\.Pin\((\d+)", "SCK_165")
    pin(r"in_spi = machine\.SPI\(1.*?miso=machine\.Pin\((\d+)", "MISO_165")
    pin(r"self\.strobe = machine\.Pin\((\d+)", "STROBE_M")
    pin(r"self\.run_pin = machine\.Pin\((\d+)", "RUN_M")
    pin(r"self\.home_pin = machine\.Pin\((\d+)", "HOME_M")
    pin(r"machine\.ADC\((\d+)\)", "ADC_IN")
    m = re.search(r"self\.sel = .*? in \(([\d, ]+)\)", t)
    for i, v in enumerate(int(x) for x in m[1].split(",")):
        exp[f"MUX_S{i}"] = v
    m = re.search(r"self\.en = .*? in \(([\d, ]+)\)", t)
    for i, v in enumerate(int(x) for x in m[1].split(",")):
        exp[f"MUX_EN{i}"] = v
    return exp


def check_pico(nl: Netlist, rep: Report):
    hal = parse_hal()
    # GPIO number -> net, from the netlist pin functions of U1
    gpio_net = {}
    for (ref, pin), fn in nl.pin_func.items():
        if ref == "U1":
            m = re.match(r"GPIO(\d+)", fn)
            if m:
                gpio_net[int(m[1])] = nl.net(ref, pin)
    # the Pico header carries GP0-GP22 and GP26-GP28 only
    for name, gp in sorted(hal.items(), key=lambda kv: kv[1]):
        net = gpio_net.get(gp)
        if net == name:
            rep.check(True, "hal", "")
            continue
        where = [g for g, n in gpio_net.items() if n == name]
        if gp in gpio_net or gp > 28 or gp in (23, 24, 25):
            rep.info(f"HAL MISMATCH: {name}: hal_pico.py uses GP{gp}, board has it on "
                     f"{'GP' + str(where[0]) if where else 'nowhere'}"
                     f"{' (GP%d is not on the Pico header)' % gp if gp in (23, 24, 25) else ''}")
        known = (name == "MUX_S3" and gp == 23 and where == [27])
        rep.check(known, "hal", f"{name}: HAL GP{gp} but board has {where}")
    # boot-safe: OE pulled high on the field side, RUN/HOME isolator default low
    rep.check(any(nl.comp[r]["value"] == "10k" and nl.other_pin(r, p) == "+5V_F" for r, p in nl.pins_on("OE_F") if r.startswith("R")),
              "hal", "OE_F pulled up to +5V_F")
    rep.check(nl.comp["U2"]["value"] == "ISO7340C" and nl.net("U2", "11") == "OE_F" and nl.net("U2", "6") == "OE_M",
              "hal", "OE crosses the barrier on the default-high isolator (drivers off when MCU unpowered)")
    rep.check(nl.comp["U3"]["value"] == "ISO7340FC" and nl.net("U3", "14") == "RUN_F" and nl.net("U3", "13") == "HOME_F",
              "hal", "RUN/HOME cross on the fail-safe-low isolator")
    for net, other, what in (("OE_M", "+3V3", "OE_M pulled up (drivers off while the Pico boots)"),
                             ("RUN_M", "GND_M", "RUN_M pulled down"), ("HOME_M", "GND_M", "HOME_M pulled down")):
        rep.check(any(nl.comp[r]["value"] == "10k" and nl.other_pin(r, p) == other for r, p in nl.pins_on(net) if r.startswith("R")),
                  "hal", what)
    for m_net, f_net, pin_in, pin_out in (("SCK_M", "SCK_F", "3", "14"), ("MOSI_M", "MOSI_F", "4", "13"),
                                          ("RCLK_M", "RCLK_F", "5", "12"), ("OE_M", "OE_F", "6", "11")):
        rep.check(nl.net("U2", pin_in) == m_net and nl.net("U2", pin_out) == f_net, "hal", f"U2 {m_net} -> {f_net}")


# ----------------------------------------------------------------------------- 3. connectors vs README
README_JC = ("1 STROBE, 2 RUN, 3 STOPPED, 4 HOME, 5 AT_HOME, 6 END, 7–21 POS0–POS14, 22–23 ESTOP 체인, "
             "24–25 ILOCK 체인, 26–27 +24 V, 28–33 0 V, 34 JUDGE, 35 CLEAR")


def check_connectors(nl: Netlist, rep: Report):
    readme = (TESTER / "README.md").read_text(encoding="utf8")
    rep.check("1–26 = 선 A–Z, 27–29 = 0 V" in readme and "30–37 예약" in readme, "conn", "README J_A/J_B row text unchanged")
    rep.check(README_JC.split(", 7–21")[0] in readme and "34 JUDGE, 35 CLEAR" in readme, "conn", "README J_C row text unchanged")
    for ref, base in (("J1", 0), ("J2", 26)):
        for i in range(26):
            rep.check(nl.net(ref, str(i + 1)) == f"NODE_{base + i}", "conn", f"{ref} pin {i + 1} -> NODE_{base + i} (line {chr(65 + i)})")
        for p in (27, 28, 29):
            rep.check(nl.net(ref, str(p)) == "GND_F", "conn", f"{ref} pin {p} = 0 V")
        for p in range(30, 38):
            rep.check(nl.net(ref, str(p)).startswith("unconnected"), "conn", f"{ref} pin {p} reserved (unconnected)")
    jc = {1: "STROBE_IN", 2: "RUN_OUT", 3: "STOPPED_IN", 4: "HOME_OUT", 5: "AT_HOME_IN", 6: "END_IN",
          22: "ESTOP_A", 23: "ESTOP_RET", 24: "ILOCK_A", 25: "ILOCK_RET", 26: "+24V_BANK", 27: "+24V_BANK",
          34: "JUDGE_IN", 35: "CLEAR_IN", 36: "STOP_LATCH_IN"}
    for k in range(15):
        jc[7 + k] = f"POS{k}_IN"
    for p in range(28, 34):
        jc[p] = "GND_F"
    for p, net in jc.items():
        rep.check(nl.net("J3", str(p)) == net, "conn", f"J_C pin {p} -> {net} (got {nl.net('J3', str(p))})")
    rep.check(nl.net("J3", "37").startswith("unconnected"), "conn", "J_C pin 37 reserved")
    # STROBE path
    rs = [(r, p) for r, p in nl.pins_on("STROBE_IN") if r.startswith("R")]
    rep.check(len(rs) == 1 and ("J3", "1") in nl.pins_on("STROBE_IN"), "conn", "STROBE_IN: J_C 1 -> one resistor -> 6N137")
    rep.check(nl.net("U5", "4") == "STROBE_M" and nl.net("U4", "6") == nl.net("U5", "2"), "conn", "6N137 -> inverter -> STROBE_M")


# ----------------------------------------------------------------------------- 4. domains
FIELD_SEEDS = {"Q1", "U10", "U11", "U12", "U13", "U14", "U15", "U16", "U20", "U21", "U22", "U23", "U24", "U25", "U26",
               "U80", "J1", "J2", "J3", "J4", "J6", "F1", "F2", "D1"}
MCU_SEEDS = {"U1", "U5", "SW1", "J5"} | {f"U{30 + i}" for i in range(10)}


def seed_domain(ref: str, pin: str) -> str | None:
    if ref in FIELD_SEEDS:
        return "FIELD"
    if ref in MCU_SEEDS:
        return "MCU"
    if ref in ("U2", "U3"):                    # ISO7340: pins 1-8 = side 1 (MCU), 9-16 = side 2 (field)
        return "MCU" if int(pin) <= 8 else "FIELD"
    if ref == "U4":                            # 6N137: 1-4 LED (field), 5-8 output (MCU)
        return "FIELD" if int(pin) <= 4 else "MCU"
    if re.fullmatch(r"U(5\d|6[0-8])", ref):     # TLP291-4: 1-8 LED side, 9-16 output side
        return "FIELD" if int(pin) <= 8 else "MCU"
    return None


def net_domains(nl: Netlist) -> dict[str, str]:
    out = {}
    for name, pins in nl.nets.items():
        d = {seed_domain(r, p) for r, p, _ in pins} - {None}
        out[name] = d.pop() if len(d) == 1 else ("MIXED" if d else "?")
    # nets with no seed: take the domain of the other pins' sibling nets via passives (iterate)
    changed = True
    while changed:
        changed = False
        for ref, c in nl.comp.items():
            if not re.match(r"[RCD]\d|DL|F\d|JP", ref):
                continue
            nets = [nl.net(ref, p) for p in ("1", "2")]
            nets = [n for n in nets if n]
            doms = {out[n] for n in nets} - {"?"}
            if len(doms) == 1:
                for n in nets:
                    if out[n] == "?":
                        out[n] = doms.pop() if False else next(iter(doms))
                        changed = True
    return out


def check_domains(nl: Netlist, rep: Report) -> dict[str, str]:
    dom = net_domains(nl)
    for n, d in dom.items():
        rep.check(d != "MIXED", "domain", f"net {n} touches both FIELD and MCU domain pins")
    for ref in nl.comp:
        if re.match(r"(R|C|D|DL|F|JP)\d", ref):
            ds = {dom[nl.net(ref, p)] for p in ("1", "2") if nl.net(ref, p)} - {"?"}
            rep.check(len(ds) <= 1, "domain", f"{ref} bridges domains {ds}")
    unknown = [n for n, d in dom.items() if d == "?" and not n.startswith("unconnected")]
    rep.check(not unknown, "domain", f"nets without a domain: {unknown[:8]}")
    return dom


# ----------------------------------------------------------------------------- BOM
def bom(nl: Netlist) -> list[tuple[str, str, int]]:
    cnt = collections.Counter()
    for ref, c in nl.comp.items():
        if ref.startswith("#"):
            continue
        cnt[(c["value"], c["fp"].split(":")[-1])] += 1
    return sorted(((v, f, n) for (v, f), n in cnt.items()), key=lambda x: (-x[2], x[0]))


def run(nl: Netlist) -> tuple[Report, dict[str, str]]:
    rep = Report()
    check_nodes(nl, rep)
    check_aux(nl, rep)
    check_pico(nl, rep)
    check_connectors(nl, rep)
    check_0003(nl, rep)
    dom = check_domains(nl, rep)
    return rep, dom


def swap_nets(nl: Netlist, a: tuple[str, str], b: tuple[str, str]) -> Netlist:
    """Copy of the netlist with the nets of two pins exchanged."""
    import copy
    n2 = copy.deepcopy(nl)
    na, nb = n2.pin_net[a], n2.pin_net[b]
    fa, fb = n2.pin_func.get(a, ""), n2.pin_func.get(b, "")
    n2.nets[na] = [x for x in n2.nets[na] if (x[0], x[1]) != a] + [(b[0], b[1], fb)]
    n2.nets[nb] = [x for x in n2.nets[nb] if (x[0], x[1]) != b] + [(a[0], a[1], fa)]
    n2.pin_net[a], n2.pin_net[b] = nb, na
    return n2


def move_pin(nl: Netlist, pin: tuple[str, str], net: str) -> Netlist:
    import copy
    n2 = copy.deepcopy(nl)
    old = n2.pin_net[pin]
    fn = n2.pin_func.get(pin, "")
    n2.nets[old] = [x for x in n2.nets[old] if (x[0], x[1]) != pin]
    n2.nets.setdefault(net, []).append((pin[0], pin[1], fn))
    n2.pin_net[pin] = net
    return n2


def set_comp(nl: Netlist, ref: str, **kw) -> Netlist:
    import copy
    n2 = copy.deepcopy(nl)
    n2.comp[ref].update(kw)
    return n2


def selftest(nl: Netlist) -> bool:
    cases = [
        ("swap the 165 inputs of SENSE_5 <-> SENSE_6 (bits 5, 6)",
         swap_nets(nl, *[next(x for x in nl.pins_on(f"SENSE_{b}") if nl.comp[x[0]]["value"] == "74HC165") for b in (5, 6)])),
        ("swap node nets NODE_30 <-> NODE_31 at the connector", swap_nets(nl, ("J2", "5"), ("J2", "6"))),
        ("T1: J_A fitted as a SOCKET footprint", set_comp(nl, "J1", fp="Connector_Dsub:DSUB-37_Socket_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_MountingHolesOffset11.32mm")),
        ("T2: RUN output element is a 1k resistor again (F3 value/footprint)", set_comp(nl, "F3", value="1k 1W", fp="Resistor_SMD:R_2512_6332Metric")),
        ("T3: ESTOP return jumper JP2 fitted open", set_comp(nl, "JP2", fp="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm")),
        ("T3: ILOCK loop fed from +24V_F by default (JP5 bridged)", set_comp(nl, "JP5", fp="Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm")),
        ("T3: J_C 22/23 swapped (loop direction reversed)", swap_nets(nl, ("J3", "22"), ("J3", "23"))),
        ("T4: STOP_LATCH (J_C 36) and JUDGE (J_C 34) swapped", swap_nets(nl, ("J3", "36"), ("J3", "34"))),
        ("T7: CHASSIS_F resistor R408 returned to 0 V of the MCU side", move_pin(nl, ("R408", "2"), "GND_M")),
        ("T7: CHASSIS_F capacitor missing (C237 pin 1 on GND_F)", move_pin(nl, ("C237", "1"), "GND_F")),
        ("T8: reversed series P-MOSFET (Q1 pins 2 <-> 3)", swap_nets(nl, ("Q1", "2"), ("Q1", "3"))),
        ("T8: terminal J4 straight on +24V_IN (MOSFET bypassed)", move_pin(nl, ("J4", "1"), "+24V_IN")),
        ("T8: gate zener DZ1 reversed (pins 1 <-> 2)", swap_nets(nl, ("DZ1", "1"), ("DZ1", "2"))),
    ]
    ok = True
    print("selftest (mutated netlist copies must fail the checks):")
    for name, m in cases:
        try:
            r, _ = run(m)
            bad = len(r.fail)
        except Exception as ex:      # a mutation that breaks a trace counts as detected
            bad = 1
            name += f" [{type(ex).__name__}]"
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({bad} failures)")
        ok &= bad > 0
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--netlist")
    ap.add_argument("--bom", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.netlist:
        path = Path(a.netlist)
    else:
        path = Path(tempfile.gettempdir()) / "unit_tester_nl.xml"
        export_netlist(path)
    nl = Netlist(path)
    rep, dom = run(nl)
    for k, v in sorted(rep.ok.items()):
        print(f"PASS  {k:8s} {v} checks")
    for m in rep.note:
        print("NOTE ", m)
    for m in rep.fail:
        print("FAIL ", m)
    print(f"{sum(rep.ok.values())} checks passed, {len(rep.fail)} failed, {len(rep.note)} note(s); "
          f"domains: {collections.Counter(dom.values())}")
    if a.bom:
        for v, fp, n in bom(nl):
            print(f"{n:4d}  {v:20s} {fp}")
    st = True
    if a.selftest:
        st = selftest(nl)
        print("selftest:", "ALL DETECTED" if st else "A MUTATION WAS NOT DETECTED")
    sys.exit(1 if rep.fail or not st else 0)


if __name__ == "__main__":
    main()
