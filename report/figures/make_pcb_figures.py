"""Export PCB placement drawings (unrouted boards) for the report appendix.

    python report/figures/make_pcb_figures.py

For each placed board, writes report/figures/generated/pcb_<name>.pdf: board outline, footprint
courtyards, fabrication outlines and silkscreen of the front side, as one vector page. Boards that
do not exist are skipped; a PDF that mentions a personal directory is deleted and the script fails.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "report" / "figures" / "generated"

BOARDS = [
    ("shifter-board", "hardware/relay/shifter-board/shifter_board.kicad_pcb"),
    ("test-register", "hardware/common/test-register/test_register.kicad_pcb"),
    ("unit-tester", "hardware/common/unit-tester/pcb/unit_tester.kicad_pcb"),
    ("counter-common", "hardware/relay/counter-board/counter_common.kicad_pcb"),
    ("counter-unit", "hardware/relay/counter-board/counter_unit.kicad_pcb"),
]
LAYERS = "Edge.Cuts,F.Fab,F.SilkS,F.CrtYd,User.Drawings"
PERSONAL = re.compile(rb"(?i)c:[/\\]+users|/home/[a-z]|dropbox[/\\]")


def kicad_cli() -> str:
    found = shutil.which("kicad-cli")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
    if default.exists():
        return str(default)
    sys.exit("kicad-cli not found")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cli = kicad_cli()
    for name, rel in BOARDS:
        pcb = ROOT / rel
        if not pcb.exists():
            print(f"skip {name}: {rel} not found")
            continue
        pdf = OUT / f"pcb_{name}.pdf"
        raw = OUT / f"pcb_{name}.uncropped.pdf"
        subprocess.run([cli, "pcb", "export", "pdf", "--mode-single", "--layers", LAYERS,
                        "--exclude-value", "--output", str(raw), str(pcb)],
                       check=True, capture_output=True, cwd=pcb.parent)
        # The plot keeps the drawing-sheet size; crop to the board (TeX Live's pdfcrop).
        if shutil.which("pdfcrop"):
            subprocess.run(["pdfcrop", "--margins", "4", raw.name, pdf.name], check=True, capture_output=True, cwd=OUT)
            raw.unlink()
        else:
            raw.replace(pdf)
        if PERSONAL.search(pdf.read_bytes()):
            pdf.unlink()
            sys.exit(f"{pdf.name}: exported PDF contains a personal path — not kept")
        print(f"{pdf.relative_to(ROOT)}  {pdf.stat().st_size} bytes")


if __name__ == "__main__":
    main()
