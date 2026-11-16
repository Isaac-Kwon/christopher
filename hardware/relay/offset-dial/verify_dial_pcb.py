#!/usr/bin/env python3
"""Check the placed offset dial PCB against its schematic (as verify_counter_pcb.py does for the counter boards).

    python verify_dial_pcb.py

Pad nets == schematic nets (pcbnew; the code-track pads of SW1 carry the net of their ring, the dummy islands none, the
D-sub shell pads CHASSIS), all footprints on the front (= rotor) side, no copper besides the footprints (placement only),
kicad-cli DRC 0 violations, schematic parity, ratsnest count, negative tests, determinism.  The geometry of the code
tracks against the rotor and the counter is checked by verify_dial.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "position-display"))
import kigen  # noqa: E402
import verify_counter as vc  # noqa: E402
import gen_dial_pcb as gp  # noqa: E402


def main() -> int:
    work = kigen.build_dir("offset-dial")
    net = kigen.export_netlist(HERE / "offset_dial.kicad_sch", work / "offset_dial.net")
    nl = vc.Netlist(net.read_text(encoding="utf8"))
    print("=== offset_dial ===")
    errs = kigen.verify_pcb("offset_dial", HERE, nl, gp.generate, work, swap_pad="1", swap_net="DIAL_COM",
                            flip_fp="Connector_Dsub:DSUB-15_Pins_Vertical_P2.77x2.84mm_MountingHoles")
    print("RESULT:", "PASS" if not errs else "FAIL")
    for x in errs:
        print("  ", x)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
