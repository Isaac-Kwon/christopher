#!/usr/bin/env python3
"""Verify the schematics of the gear column wiring: ERC, netlists, negative tests.

    python verify_contact.py [--build-dir DIR]

  * ERC (kicad-cli, all severities) must report 0 violations for contact_board, column_terminal, reflector_B/C;
  * contact_board netlist: 104 Omniball contacts P<ring><letter>, 4 headers J1..J4; every contact is on a net with exactly
    one header pin: contact of ring r and letter index j  <->  pin j+1 of header Jr; nothing else is connected;
    the 26-line headers carry no 0 V (decision 0003 5.2);
  * column_terminal: J1 pin k <-> K1 pin k (IN), J2 pin k <-> K2 pin k (OUT), pins 27-37 not connected, shells = CHASSIS;
  * reflector cards B, C: K1 pin j+1 <-> K2 pin R[j]+1 with R from data/wirings/enigma.txt;
  * negative tests: swapped pins / a wrong reflector card in a netlist copy are detected.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gen_contact as C
import ki_common as K

HERE = Path(__file__).resolve().parent


def partner_map(nl: K.Netlist, a: str, b: str) -> dict[int, set[int]] | None:
    """pin of header a -> set of pins of header b on the same net."""
    out = {}
    for name, nodes in nl.nets.items():
        pa = [int(p) for r, p in nodes if r == a and p.isdigit()]
        pb = [int(p) for r, p in nodes if r == b and p.isdigit()]
        for p in pa:
            out.setdefault(p, set()).update(pb)
    return out


def check_station(nl: K.Netlist) -> list[str]:
    errs = []
    contacts = sorted(r for r in nl.parts if r.startswith("P"))
    if len(contacts) != 104:
        errs.append(f"{len(contacts)} contacts, expected 104")
    if sorted(r for r in nl.parts if r.startswith("J")) != ["J1", "J2", "J3", "J4"]:
        errs.append("headers J1..J4 expected")
    for r, p in nl.parts.items():
        if r.startswith("P") and "Omniball_7845-0-15-20-52-14-11-0" not in p["footprint"]:
            errs.append(f"{r}: footprint {p['footprint']}")
    for ring in (1, 2, 3, 4):
        for j in range(26):
            ref = f"P{ring}{chr(65 + j)}"
            net = nl.node_net.get((ref, "1"))
            nodes = set(nl.nets.get(net, [])) if net else set()
            if nodes != {(ref, "1"), (f"J{ring}", str(j + 1))}:
                errs.append(f"{ref}: net {net} = {sorted(nodes)}, expected {ref} + J{ring} pin {j + 1}")
    for h in ("J1", "J2", "J3", "J4"):
        for k in range(1, 27):
            if (h, str(k)) not in nl.node_net:
                errs.append(f"{h} pin {k} unconnected")
        for k in range(27, 65):
            net = nl.node_net.get((h, str(k)))
            if net is not None and len(nl.nets[net]) > 1:
                errs.append(f"{h} pin {k} should not exist")
    if any("GND" in n.upper() or "0V" in n.upper() for n in nl.nets):
        errs.append("a 0 V net exists on the scrambler side")
    return errs


def check_terminal(nl: K.Netlist) -> list[str]:
    errs = []
    for jack, hdr in (("J1", "K1"), ("J2", "K2")):
        pm = partner_map(nl, jack, hdr)
        for k in range(1, 27):
            if pm.get(k) != {k}:
                errs.append(f"{jack} pin {k} -> {hdr} {pm.get(k)}")
        for k in range(27, 38):
            net = nl.node_net.get((jack, str(k)))
            if net is not None and len(nl.nets[net]) > 1:
                errs.append(f"{jack} pin {k} must be unconnected (scrambler side)")
    for k in range(1, 27):          # IN and OUT must not touch
        a, b = nl.node_net.get(("J1", str(k))), nl.node_net.get(("J2", str(k)))
        if a == b:
            errs.append(f"IN and OUT pin {k} on one net")
    sh = {nl.node_net.get((j, "SH")) for j in ("J1", "J2")}
    if len(sh) != 1 or "CHASSIS" not in str(next(iter(sh))):
        errs.append(f"shells on {sh}, expected CHASSIS")
    return errs


def check_card(nl: K.Netlist, name: str) -> list[str]:
    R = C.reflector(name)
    pm = partner_map(nl, "K1", "K2")
    errs = []
    for j in range(26):
        if pm.get(j + 1) != {R[j] + 1}:
            errs.append(f"K1 pin {j + 1} -> K2 {pm.get(j + 1)}, expected {R[j] + 1}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("contact-board")
    work.mkdir(parents=True, exist_ok=True)
    allerr = []
    nls = {}
    for proj in ("contact_board", "column_terminal", "reflector_B", "reflector_C"):
        sch = HERE / f"{proj}.kicad_sch"
        erc = K.run_erc(sch, work)
        nls[proj] = K.Netlist.export(sch, work)
        print(f"{proj}: ERC {erc['count']} violations; {len(nls[proj].parts)} parts, {len(nls[proj].nets)} nets")
        if erc["count"]:
            allerr.append(f"{proj}: ERC {erc['count']}")
    checks = [("contact_board", check_station), ("column_terminal", check_terminal),
              ("reflector_B", lambda n: check_card(n, "B")), ("reflector_C", lambda n: check_card(n, "C"))]
    for proj, fn in checks:
        e = fn(nls[proj])
        print(f"netlist check {proj}: {'OK' if not e else f'{len(e)} problems {e[:3]}'}")
        allerr += e
    # negative tests on netlist copies
    texts = {p: (work / f"{p}.net").read_text(encoding="utf8") for p in nls}
    neg = [("contact_board: two header pins swapped (J4 pins 1, 2)", "contact_board", "J4", "1", "2", check_station),
           ("contact_board: two header pins of ring 2 swapped (J2 pins 25, 26)", "contact_board", "J2", "25", "26", check_station),
           ("column_terminal: two IN pins swapped (K1 pins 3, 4)", "column_terminal", "K1", "3", "4", check_terminal),
           ("reflector_B: two card pins swapped (K2 pins 1, 2)", "reflector_B", "K2", "1", "2", lambda n: check_card(n, "B"))]
    for title, proj, ref, pa, pb, fn in neg:
        bad = fn(K.Netlist(K.swap_pins(texts[proj], ref, pa, pb)))
        print(f"  negative test {'detected' if bad else 'MISSED  '}: {title} ({len(bad)} problems)")
        if not bad:
            allerr.append(f"negative test missed: {title}")
    wrong = check_card(nls["reflector_C"], "B")
    print(f"  negative test {'detected' if wrong else 'MISSED  '}: reflector C card checked against reflector B ({len(wrong)} problems)")
    if not wrong:
        allerr.append("negative test missed: C vs B")
    print("RESULT:", "PASS" if not allerr else "FAIL")
    return 1 if allerr else 0


if __name__ == "__main__":
    sys.exit(main())
