#!/usr/bin/env python3
"""Generate position_display.kicad_pcb: deterministic PLACEMENT only (no tracks, vias or zones; routing by hand).

    python gen_display_pcb.py [OUTDIR]     # board + design rules merged into the .kicad_pro (stdlib; kicad-cli for the netlist)

Floor plan (mm, origin top-left, y down; 88 x 294, 4 layers; it stands in the 90 mm strip right of the counter / sequencer
cards in the control column, layout.md 3.5):
    top band     digit labels l / m / r and the five bit lamps of each digit (16 8 4 2 1)
    buffer band  15 input channels (2 x 3 cells per digit: BSS138, AO3401A, divider, pull-down, gate filter)
    scale band   per digit one column: 26 letter lamps (5 mm, pitch 8 mm) with the letter printed beside each, the lamp
                 resistor, and the digit's 13 tree relays (G6S-2 turned 90 degrees, one per two lamp rows) with their
                 flyback diodes
    bottom band  power input chain (J2, PTC, P-MOSFET, TVS, capacitor), CHASSIS pair, D-sub 25 POS_IN on the bottom edge
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402

PROJECT = "position_display"
NS = uuid.UUID("5e0d1a7c-2b9f-4c3e-8a61-0f4d2c7b9e12")
W, H = 88.0, 294.0
HOLES = [(5.0, 5.0), (W - 5.0, 5.0), (5.0, H - 5.0), (W - 5.0, H - 5.0)]
COL_W = 29.0
Y_LAMP0, LAMP_P = 56.0, 8.0          # first letter row, row pitch
RELAYS = [1, 1, 2, 4, 5]
DB25_DEPTH = 12.74                   # pin-1 row to the mating face (board edge)


def x0(d: int) -> float:
    return 1.0 + COL_W * (d - 1)


def placements(nl) -> tuple[dict, str]:
    P: dict = {}
    extra = []
    gt = lambda key, text, x, y, size=1.5, **k: extra.append(kigen.gr_text(NS, PROJECT, key, text, x, y, size, **k))
    gr = lambda key, x, y, w, h: extra.append(kigen.gr_rect(NS, PROJECT, key, x, y, w, h))
    for d, (name, lab) in enumerate((("l", "L (FAST)"), ("m", "M"), ("r", "R (SLOW)")), 1):
        X = x0(d)
        gt(f"dig{d}", name, X + 2.2, 13.0, 3.0, thick=0.45, just="center")
        gt(f"diglab{d}", lab, X + 14.5, 51.0, 1.2, thick=0.18, just="center")
        for b in range(5):                                      # top band: bit lamps, MSB left
            bx = X + 7.0 + 4.6 * (4 - b)
            P[f"DB{d}{b}"] = (bx, 12.5, 0, True)
            P[f"RL{d}{b}"] = (bx, 15.3, 0, True)
            gt(f"w{d}{b}", str(1 << b), bx, 9.6, 1.0, thick=0.15, just="center")
        for b in range(5):                                      # buffer band: 2 x 3 cells
            cx, cy = X + 0.3 + 14.25 * (b % 2), 19.0 + 10.0 * (b // 2)
            P[f"QN{d}{b}"] = (cx + 3.0, cy + 2.4, 0, True)
            P[f"QP{d}{b}"] = (cx + 10.0, cy + 2.4, 0, True)
            for k, ref in enumerate(("RP", "RA", "RB", "CG")):
                P[f"{ref}{d}{b}"] = (cx + 1.8 + 3.2 * k, cy + 6.1, 0, True)
            P[f"RC{d}{b}"] = (cx + 1.8, cy + 8.4, 0, True)
            P[f"RD{d}{b}"] = (cx + 5.0, cy + 8.4, 0, True)
            gt(f"cl{d}{b}", f"b{b}", cx + 9.6, cy + 8.4, 0.9, thick=0.13)
        gr(f"buf{d}", X + 0.1, 18.4, COL_W - 0.2, 30.4)
        for v in range(26):                                     # scale band: letter, lamp, resistor
            y = Y_LAMP0 + LAMP_P * v
            gt(f"let{d}{v}", chr(65 + v), X + 1.7, y, 2.6, thick=0.4, just="center")
            P[f"DS{d}{v:02d}"] = (X + 5.0, y, 0, True)
            P[f"RS{d}{v:02d}"] = (X + 11.6, y, 90, True)
        slot = 0
        for b in range(5):                                      # tree relays, one per two letter rows, grouped by bit
            for i in range(RELAYS[b]):
                yt = Y_LAMP0 - 4.0 + 2 * LAMP_P * slot
                xr = X + 15.0
                P[f"K{d}{b}{i}"] = (xr, yt + 7.5, 90, False)
                P[f"D{d}{b}{i}"] = (xr + 5.0, yt + 12.6, 0, True)
                slot += 1
        gr(f"col{d}", X + 0.1, Y_LAMP0 - 4.6, COL_W - 0.2, 26 * LAMP_P + 1.0)
    # bottom band: power input chain (0003 7.3) and CHASSIS pair (7.1)
    yb = 269.0
    for ref, (x, y, r, hide) in {"J2": (6.0, yb + 1.0, 0, False), "F1": (20.5, yb, 0, False), "Q1": (31.0, yb, 0, False),
                                 "DZ1": (40.5, yb - 2.0, 0, True), "RG1": (40.5, yb + 1.6, 0, True), "TVS1": (48.5, yb, 0, False),
                                 "CB1": (56.5, yb, 0, False), "RCH1": (68.5, yb - 1.5, 0, True), "CCH1": (68.5, yb + 1.0, 0, True)}.items():
        P[ref] = (x, y, r, hide)
    gt("pwr", "+24V_L IN", 6.0, yb - 6.6, 1.1, thick=0.16, just="center")
    for k, (x, y) in enumerate(HOLES, 1):
        P[f"H{k}"] = (x, y, 0, True)
    xmin, xmax, _y0, _y1 = kigen.courtyard(nl.parts["J1"]["footprint"])
    P["J1"] = (W / 2 - (xmin + xmax) / 2, H - DB25_DEPTH, 0, False)
    gt("pos", "POS IN (counter J4)", W / 2, H - DB25_DEPTH - 4.6, 1.1, thick=0.16, just="center")
    return P, "".join(extra)


def build(nl) -> tuple[str, dict]:
    P, extra = placements(nl)
    text = kigen.board_text(NS, PROJECT, "Relay position display (module I): POS (l, m, r) -> three letters",
                            ["Generated by gen_display_pcb.py - placement only, routing by hand",
                             "Netlist from position_display.kicad_sch; 4 layers; G6S-2 relays, 5 mm letter lamps"],
                            nl, P, W, H, layers4=True, extra=extra)
    return text, P


def generate(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    work = kigen.build_dir("position-display")
    net = kigen.export_netlist(HERE / f"{PROJECT}.kicad_sch", work / "pcb_netlist.net")
    nl = vc.Netlist(net.read_text(encoding="utf8"))
    text, P = build(nl)
    (outdir / f"{PROJECT}.kicad_pcb").write_text(text, encoding="utf8", newline="\n")
    kigen.merge_project(outdir / f"{PROJECT}.kicad_pro", [("Default", 0.2, 0.5, 0.25), ("Rail", 0.6, 0.8, 0.4)],
                        [("Rail", p) for p in ("+24V", "GND", "VIN", "VF")])
    print(f"wrote {PROJECT}.kicad_pcb ({len(P)} parts, {W:g} x {H:g} mm) and board rules in {PROJECT}.kicad_pro")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
