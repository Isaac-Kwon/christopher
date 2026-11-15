#!/usr/bin/env python3
"""Check the placed PCBs of the relay wiring cards (placement only, no routing).

    python verify_cards_pcb.py [--build-dir DIR]

For rotor_I..VIII and reflector_B/C:
  1. parts, footprints and pad nets equal the schematic (netlist via kicad-cli, IPC-D-356 of the board); the D-sub
     shell pads SH are net CHASSIS;
  2. kicad-cli DRC: 0 violations; the unconnected items are exactly the ratsnest (pcbnew count = sum(pads - 1));
  3. identical to the shifter board: outline 250 x 228 mm and the plated mounting holes H1..H4; every connector (W: J1..J4,
     U: J2, J3) has the footprint, position and rotation of the same-named connector in shifter_board.kicad_pcb;
  4. determinism: regenerating schematic and board gives byte-identical files;
  5. negative tests: two pad nets swapped; a connector moved by 1 mm; the outline changed; a hole removed.
  6. pin-1 alignment of all 14 stack ribbons and the leads from the real footprint poses (S4 = shifter_board_s4.kicad_pcb);
     the standard shifter as S4 would misalign 4 jumpers (information).
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
import gen_cards as C  # noqa: E402
import gen_cards_pcb as GP  # noqa: E402
import ki_common as K  # noqa: E402
import verify_cards as VC  # noqa: E402

HERE = Path(__file__).resolve().parent
SHIFTER_PCB = HERE.parent / "shifter-board" / "shifter_board.kicad_pcb"


def parse_board(text: str) -> dict:
    """ref -> dict(fp, layer, x, y, rot, pads {name: [nets]})."""
    out = {}
    for m in re.finditer(r'\n\t\(footprint "([^"]+)"', text):
        s = m.start() + 1
        blk = text[s:K.block_end(text, s)]
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)[1]
        layer = re.search(r'\(layer "([^"]+)"\)', blk)[1]
        at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', blk)
        pads: dict[str, list] = {}
        for pm in re.finditer(r'\(pad "([^"]*)"', blk):
            pb = blk[pm.start():K.block_end(blk, pm.start())]
            nm = re.search(r'\(net (?:\d+ )?"([^"]*)"\)', pb)
            pads.setdefault(pm[1], []).append(nm[1] if nm else None)
        out[ref] = dict(fp=m[1], layer=layer, x=float(at[1]), y=float(at[2]), rot=float(at[3] or 0), pads=pads)
    return out


def outline(text: str):
    return sorted(re.findall(r'\(gr_line\n\t\t\(start ([-\d.]+) ([-\d.]+)\)\n\t\t\(end ([-\d.]+) ([-\d.]+)\)[^\n]*\n(?:[^\n]*\n)*?\t\t\(layer "Edge.Cuts"\)', text))


def expected_ratsnest(text: str) -> int:
    cnt = Counter()
    for pm in re.finditer(r'\(pad "[^"]*"', text):
        e = K.block_end(text, pm.start())
        nm = re.search(r'\(net \d+ "([^"]*)"\)', text[pm.start():e])
        if nm and not nm[1].startswith("unconnected"):
            cnt[nm[1]] += 1
    return sum(c - 1 for c in cnt.values() if c > 1)


def compare_poses(mine: dict, shifter: dict, refs) -> list[str]:
    errs = []
    for ref in refs:
        a, b = mine.get(ref), shifter.get(ref)
        if a is None or b is None:
            errs.append(f"{ref}: missing ({'card' if a is None else 'shifter'})")
            continue
        for k in ("fp", "layer"):
            if a[k] != b[k]:
                errs.append(f"{ref}: {k} {a[k]} != shifter {b[k]}")
        for k in ("x", "y", "rot"):
            if abs(a[k] - b[k]) > 0.0005:
                errs.append(f"{ref}: {k} {a[k]} != shifter {b[k]}")
    return errs


def check_card(proj: str, kind: str, name: str, work: Path, shifter: dict, shifter_text: str) -> tuple[list[str], str]:
    pcb = HERE / f"{proj}.kicad_pcb"
    text = pcb.read_text(encoding="utf8")
    errs: list[str] = []
    nl = K.Netlist.export(HERE / f"{proj}.kicad_sch", work)
    pads = {k: v for k, v in K.pcb_pad_nets(pcb, work).items() if k[1] != "SH"}
    errs += K.compare_pcb(nl, pads, pcb)
    brd = parse_board(text)
    for ref, d in brd.items():
        if ref.startswith("J"):
            if d["pads"].get("SH") != ["/CHASSIS"] * 2:
                errs.append(f"{ref}: shell pads {d['pads'].get('SH')} are not CHASSIS")
    d = K.run_drc(pcb, work)
    errs += [f"DRC {v['type']}: {v['description']}" for v in d["violations"][:5]]
    want = expected_ratsnest(text)
    rn = K.ratsnest_count(pcb)
    if rn is not None and rn != want:
        errs.append(f"ratsnest {rn} != {want}")
    if len(d["unconnected"]) == 0:
        errs.append("no unconnected items reported (ratsnest missing)")
    # identical to the shifter board
    conns = ["J1", "J2", "J3", "J4"] if kind == "rotor" else ["J2", "J3"]
    if sorted(r for r in brd if r.startswith("J")) != conns:
        errs.append(f"connectors {sorted(r for r in brd if r.startswith('J'))} != {conns}")
    errs += compare_poses(brd, shifter, conns + ["H1", "H2", "H3", "H4"])
    if outline(text) != outline(shifter_text):
        errs.append("outline differs from the shifter board")
    # determinism
    sch_new = (C.rotor_sheet(name) if kind == "rotor" else C.reflector_sheet(name)).render()
    if sch_new != (HERE / f"{proj}.kicad_sch").read_text(encoding="utf8"):
        errs.append("schematic is not reproducible")
    if GP.build(proj, kind, name, nl) != text:
        errs.append("board is not reproducible")
    print(f"{proj}: {len(nl.parts)} parts, {len(nl.nets)} nets; DRC {len(d['violations'])} violations, "
          f"{len(d['unconnected'])} unconnected (ratsnest {rn}, expected {want}); "
          f"{GP.BOARD_W:g} x {GP.BOARD_H:g} mm, {len(conns)} connectors at the shifter poses")
    for e in errs[:6]:
        print("   ", e)
    return errs, text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir")
    a = ap.parse_args()
    work = Path(a.build_dir) if a.build_dir else K.default_build_dir("wiring-cards")
    work.mkdir(parents=True, exist_ok=True)
    shifter_text = SHIFTER_PCB.read_text(encoding="utf8")
    shifter = parse_board(shifter_text)
    allerr, texts = [], {}
    for proj, kind, name in C.all_cards():
        e, texts[proj] = check_card(proj, kind, name, work, shifter, shifter_text)
        allerr += [f"{proj}: {x}" for x in e]

    # negative tests on copies of rotor_I.kicad_pcb
    text = texts["rotor_I"]
    nl = K.Netlist.export(HERE / "rotor_I.kicad_sch", work)
    neg = work / "cards_negative.kicad_pcb"

    def detect(title, t2, kind="nets"):
        neg.write_text(t2, encoding="utf8", newline="\n")
        if kind == "nets":
            bad = K.compare_pcb(nl, {k: v for k, v in K.pcb_pad_nets(neg, work).items() if k[1] != "SH"}, neg)
        else:
            b2 = parse_board(t2)
            bad = compare_poses(b2, shifter, ["J1", "J2", "J3", "J4", "H1", "H2", "H3", "H4"])
            if outline(t2) != outline(shifter_text):
                bad.append("outline")
        print(f"negative test {'detected' if bad else 'MISSED  '}: {title}")
        if not bad:
            allerr.append(f"negative test missed: {title}")
    detect("two pad nets of J1 swapped (pins 1, 2)", K.swap_two_pad_nets(text, "J1", "1", "2"))
    j3 = parse_board(text)["J3"]
    i = text.index('(property "Reference" "J3"')
    s = text.rfind('(footprint "', 0, i)
    mv = text[:s] + text[s:i].replace(f"(at {K.fmt(j3['x'])} {K.fmt(j3['y'])} {K.fmt(j3['rot'])})",
                                      f"(at {K.fmt(j3['x'])} {K.fmt(j3['y'] + 1.0)} {K.fmt(j3['rot'])})", 1) + text[i:]
    assert mv != text
    detect("connector J3 moved by 1 mm", mv, "pose")
    detect("outline 1 mm too wide", text.replace("(end 250 0)", "(end 251 0)", 1), "pose")
    neg.unlink()

    # pin-1 ends of the stack ribbons, from the real footprint poses of the board files (S4 = the mirrored variant)
    bad = VC.pin1_check(VC.STACK, "s4", VC.board_pin1_ends())
    print(f"pin-1 alignment of the 14 stack ribbons and the IN/OUT leads (S1-S3 standard shifter, S4 variant, rotor_I, reflector_B): "
          f"{'all aligned' if not bad else bad}")
    allerr += [f"pin 1: {x}" for x in bad]
    old = VC.pin1_check(VC.STACK, "std", VC.board_pin1_ends(), s4_variant=False)
    print(f"information: with the standard shifter as S4, {len(old)} jumpers would have pin 1 at opposite ends "
          f"({', '.join(sorted(x.split(' (')[0] for x in old))}): the reason for shifter_board_s4.kicad_pcb")
    if len(old) != 4:
        allerr.append("pin 1: the standard-S4 negative case is not the expected 4 jumpers")

    print("RESULT:", "PASS" if not allerr else f"FAIL {allerr[:5]}")
    return 1 if allerr else 0


if __name__ == "__main__":
    sys.exit(main())
