#!/usr/bin/env python3
"""Check the placed position display PCB against its schematic (as verify_counter_pcb.py does for the counter boards).

    python verify_display_pcb.py

1. schematic netlist (kicad-cli) vs. the board read with the KiCad Python (pcbnew): every part on the board with the
   schematic's footprint, on the front side, pad nets == schematic nets (D-sub shell pads SH on CHASSIS), no copper;
2. kicad-cli DRC (all severities): 0 violations; schematic parity without missing parts or net conflicts;
3. ratsnest edge count == sum(pads - 1) over the schematic nets (+ duplicate-numbered pads);
4. negative tests: two relay pads' nets swapped, a footprint moved to the back, a shell pad on 0 V;
5. determinism: two fresh runs are byte-identical to each other and to the repository files.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402
import gen_display_pcb as gp  # noqa: E402


def main() -> int:
    work = kigen.build_dir("position-display")
    net = kigen.export_netlist(HERE / "position_display.kicad_sch", work / "position_display.net")
    nl = vc.Netlist(net.read_text(encoding="utf8"))
    print("=== position_display ===")
    errs = kigen.verify_pcb("position_display", HERE, nl, gp.generate, work, swap_pad="4", flip_fp="Diode_SMD:D_SOD-123")
    print("RESULT:", "PASS" if not errs else "FAIL")
    for x in errs:
        print("  ", x)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
