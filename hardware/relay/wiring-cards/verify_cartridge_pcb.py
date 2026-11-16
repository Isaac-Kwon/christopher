#!/usr/bin/env python3
"""Check the placed PCBs of the rotor cartridge arrangement (placement only, no routing).

    python verify_cartridge_pcb.py [--build-dir DIR]

For rotor_socket and cart_I..VIII (kicad-cli runs on staged copies, gen_cartridge.stage):
  0. ERC of the schematic (all severities): 0;
  1. parts, footprints and pad nets equal the schematic (netlist and IPC-D-356); D-sub shells and M3 holes = CHASSIS;
  2. DRC (all severities): 0 violations; the unconnected items are exactly the ratsnest (pcbnew count = sum(pads - 1));
  3. sides and outline: socket 116 x 166 with every spring pin on the front and every D-sub / header on the back,
     no D-sub pad nearer than 2.5 mm to a spring pin; cartridge 34 x 118 with every pad on the back;
  4. keys and mating from the two board files (verify_cards.key_fit / mating): every cartridge in every bay seats
     centred only the right way round and its 106 pads meet the 106 pins (same names);
  5. determinism: regenerating schematic and board gives byte-identical files;
  6. negative tests: two pad nets swapped; a spring pin moved 0.5 mm; a keying hole moved 0.2 mm; the 3.05 hole
     enlarged to 4.05 (a cartridge turned 180 deg would then fit); the outline 1 mm larger.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
import gen_cartridge as G  # noqa: E402
import gen_cartridge_pcb as GP  # noqa: E402
import ki_common as K  # noqa: E402
import verify_cards as VC  # noqa: E402
import verify_cards_pcb as VP  # noqa: E402

HERE = Path(__file__).resolve().parent


def pad_nets(pcb: Path, work: Path) -> dict:
    return G.staged_pad_nets(pcb, work)


def drc(pcb: Path, work: Path) -> dict:
    return G.staged_drc(pcb, work)


def geometry_errors(sock: dict, carts: dict) -> list[str]:
    errs = []
    bays = {b: VC.bay_geometry(sock, b) for b in G.BAYS}
    for p, cg in carts.items():
        for b, bg in bays.items():
            fits = {o: VC.key_fit(bg, cg, o) for o in VC.ORIENTS}
            if fits["as made"] is None or max(map(abs, fits["as made"])) > 0.05:
                errs.append(f"{p} / bay {b}: not seated centred by the keys ({fits['as made']})")
            wrong = [o for o in VC.ORIENTS[1:] if fits[o] is not None]
            if wrong:
                errs.append(f"{p} / bay {b}: also fits {wrong}")
            mt = VC.mating(bg, cg, "as made")
            bad = [pn for pn in bg["pins"] if mt.get(pn) != pn]
            if bad:
                errs.append(f"{p} / bay {b}: {len(bad)} pins miss their pad ({bad[:3]})")
    return errs


def erc(sch: Path, work: Path) -> list:
    s = G.stage(sch, work)
    out = work / (sch.stem + "_erc.json")
    G.cli("sch", "erc", "--severity-all", "--format", "json", "-o", str(out), str(s))
    d = json.loads(out.read_text(encoding="utf8"))
    return [v for sh in d.get("sheets", []) for v in sh.get("violations", [])]


def check_board(proj: str, name, work: Path) -> tuple[list[str], str]:
    pcb = HERE / f"{proj}.kicad_pcb"
    text = pcb.read_text(encoding="utf8")
    errs: list[str] = []
    ev = erc(HERE / f"{proj}.kicad_sch", work)
    errs += [f"ERC {v.get('type')}: {v.get('description')}" for v in ev[:5]]
    nl = G.export_netlist(HERE / f"{proj}.kicad_sch", work)
    pads = pad_nets(pcb, work)
    errs += K.compare_pcb(nl, pads, pcb, allowed_extra=lambda r: bool(re.fullmatch(r"([LMR]DW\d|K\d|NH\d)", r)))
    brd = VP.parse_board(text)
    d = drc(pcb, work)
    errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
    want = VP.expected_ratsnest(text)
    rn = K.ratsnest_count(d["staged"])
    if rn is not None and rn != want:
        errs.append(f"ratsnest {rn} != {want}")
    if len(d["unconnected"]) != want:
        errs.append(f"DRC unconnected {len(d['unconnected'])} != ratsnest {want}")
    ol = VP.outline(text)
    xs = [float(v) for e in ol for v in (e[0], e[2])]
    ys = [float(v) for e in ol for v in (e[1], e[3])]
    size = (max(xs) - min(xs), max(ys) - min(ys))
    if proj == "rotor_socket":
        if size != (G.SOCK_W, G.SOCK_H):
            errs.append(f"outline {size}")
        pins = {r: v for r, v in brd.items() if v["fp"].endswith(G.FP_PIN)}
        if {v["layer"] for v in pins.values()} != {"F.Cu"} or len(pins) != 3 * (4 * 26 + 2):
            errs.append("spring pins: not all 318 on the front")
        back = {r: v for r, v in brd.items() if re.fullmatch(r"J\d+", r)}
        if {v["layer"] for v in back.values()} != {"B.Cu"} or len(back) != 16:
            errs.append("D-subs / headers: not all 16 on the back")
        # every D-sub pad position (from the footprint library, through the placement transform) vs every pin
        lib = GP.lib()
        dmin = 1e9
        for ref, v in back.items():
            if not v["fp"].startswith("Connector_Dsub"):
                continue
            ft = lib.text(v["fp"])
            for m in re.finditer(r'\(pad "(\d+|SH)" thru_hole \w+\s*\(at ([-\d.]+) ([-\d.]+)', ft):
                x, y = K.pad_xy(v["x"], v["y"], v["rot"], v["layer"] == "B.Cu", float(m[2]), float(m[3]))
                for pv in pins.values():
                    dmin = min(dmin, math.hypot(x - pv["x"], y - pv["y"]))
        if dmin < 2.5:
            errs.append(f"a D-sub pad is {dmin:.2f} mm from a spring pin (< 2.5)")
        extra = f"nearest D-sub pad to a spring pin {dmin:.2f} mm"
        regen_sch = G.socket_sheet().render()
    else:
        if size != (G.CART_W, G.CART_H):
            errs.append(f"outline {size}")
        pads_ = {r: v for r, v in brd.items() if v["fp"].endswith(G.FP_PAD)}
        if {v["layer"] for v in pads_.values()} != {"B.Cu"} or len(pads_) != 4 * 26 + 2:
            errs.append("cartridge pads: not all 106 on the back")
        extra = "106 pads on the back"
        regen_sch = G.cart_sheet(name).render()
    if regen_sch != (HERE / f"{proj}.kicad_sch").read_text(encoding="utf8"):
        errs.append("schematic is not reproducible")
    if GP.build(proj, nl, name) != text:
        errs.append("board is not reproducible")
    print(f"{proj}: ERC {len(ev)}; {len(nl.parts)} parts, {len(nl.nets)} nets; DRC {len(d['violations'])} violations, "
          f"{len(d['unconnected'])} unconnected (ratsnest {rn}, expected {want}); {size[0]:g} x {size[1]:g} mm; {extra}")
    for e in errs[:6]:
        print("   ", e)
    return errs, text


def move_fp(text: str, ref: str, dx: float, dy: float) -> str:
    i = text.index(f'(property "Reference" "{ref}"')
    s = text.rfind('\n\t(footprint "', 0, i)
    m = re.compile(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)').search(text, s)
    new = f"\n\t\t(at {K.fmt(float(m[1]) + dx)} {K.fmt(float(m[2]) + dy)}"
    return text[:m.start()] + new + text[m.end():]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("wiring-cards-cartridge")
    work.mkdir(parents=True, exist_ok=True)
    allerr, texts = [], {}
    for proj, name in GP.projects():
        e, texts[proj] = check_board(proj, name, work)
        allerr += [f"{proj}: {x}" for x in e]

    sock = VC.board_geometry(HERE / "rotor_socket.kicad_pcb")
    carts = {p: VC.cart_geometry(VC.board_geometry(HERE / f"{p}.kicad_pcb")) for p, _ in G.cart_projects()}
    e = geometry_errors(sock, carts)
    print(f"keys and mating (8 cartridges x 3 bays): {'only the right way round seats, 106/106 pins on their own pad' if not e else e[:3]}")
    allerr += e

    # negative tests
    neg = work / "negative.kicad_pcb"

    def geo_of(t: str, kind: str) -> dict:
        neg.write_text(t, encoding="utf8", newline="\n")
        g = VC.board_geometry(neg)
        return g if kind == "sock" else VC.cart_geometry(g)

    def report(title, bad):
        print(f"negative test {'detected' if bad else 'MISSED  '}: {title}")
        if not bad:
            allerr.append(f"negative test missed: {title}")

    nl = G.export_netlist(HERE / "rotor_socket.kicad_sch", work)
    t2 = K.swap_two_pad_nets(texts["rotor_socket"], "J7", "1", "2")
    neg.write_text(t2, encoding="utf8", newline="\n")
    report("socket: two pad nets of J7 swapped (pins 1, 2)", K.compare_pcb(nl, pad_nets(neg, work), neg,
                                                                          allowed_extra=lambda r: bool(re.fullmatch(r"[LMR]DW\d", r))))
    s2 = geo_of(move_fp(texts["rotor_socket"], "MOI5", 0.5, 0.0), "sock")
    report("socket: spring pin MOI5 moved 0.5 mm", geometry_errors(s2, {"cart_I": carts["cart_I"]}))
    c2 = geo_of(move_fp(texts["cart_II"], "K4", 0.0, 0.2), "cart")
    report("cartridge: keying hole 4.05 moved 0.2 mm", geometry_errors(sock, {"cart_II": c2}))
    c3 = geo_of(texts["cart_III"].replace("(drill 3.05)", "(drill 4.05)", 1).replace("(size 3.05 3.05)", "(size 4.05 4.05)", 1), "cart")
    report("cartridge: the 3.05 hole enlarged to 4.05 (turned 180 deg would fit)", geometry_errors(sock, {"cart_III": c3}))
    t4 = texts["cart_IV"].replace(f"(end {K.fmt(G.CART_W)} 0)", f"(end {K.fmt(G.CART_W + 1)} 0)", 1)
    c4 = geo_of(t4, "cart")
    report("cartridge: outline 1 mm wider (datum off by 0.5)", geometry_errors(sock, {"cart_IV": c4}))
    neg.unlink()

    print("RESULT:", "PASS" if not allerr else f"FAIL {allerr[:5]}")
    return 1 if allerr else 0


if __name__ == "__main__":
    sys.exit(main())
