"""Export the project's KiCad schematics to PDF for the report appendix.

    python report/figures/make_schematic_figures.py

Writes report/figures/generated/sch_<name>.pdf (all sheets, vector) with kicad-cli. Projects that
do not exist yet (boards still being designed) are skipped. Absolute paths are stripped from the
PDF metadata check below: the script fails if the exported PDF mentions a user directory.
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

# (output name, root schematic relative to the repository)
SCHEMATICS = [
    ("shifter-board", "hardware/relay/shifter-board/shifter_board.kicad_sch"),
    ("counter-common", "hardware/relay/counter-board/counter_common.kicad_sch"),
    ("counter-unit", "hardware/relay/counter-board/counter_unit.kicad_sch"),
    ("test-register", "hardware/common/test-register/test_register.kicad_sch"),
    ("unit-tester", "hardware/common/unit-tester/pcb/unit_tester.kicad_sch"),
    ("contact-board", "hardware/gear/contact-board/contact_board.kicad_sch"),
    ("column-terminal", "hardware/gear/contact-board/column_terminal.kicad_sch"),
    ("reflector-card-b", "hardware/gear/contact-board/reflector_B.kicad_sch"),
    ("reflector-card-c", "hardware/gear/contact-board/reflector_C.kicad_sch"),
    ("diagonal-d1", "hardware/common/diagonal-board/d1.kicad_sch"),
    ("diagonal-d2", "hardware/common/diagonal-board/d2.kicad_sch"),
    ("diagonal-d3", "hardware/common/diagonal-board/d3.kicad_sch"),
    ("diagonal-d4", "hardware/common/diagonal-board/d4.kicad_sch"),
    ("diagonal-d5", "hardware/common/diagonal-board/d5.kicad_sch"),
    ("patch-panel", "hardware/common/patch-panel/patch_panel.kicad_sch"),
]

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
    for name, rel in SCHEMATICS:
        sch = ROOT / rel
        if not sch.exists():
            print(f"skip {name}: {rel} not found")
            continue
        pdf = OUT / f"sch_{name}.pdf"
        subprocess.run([cli, "sch", "export", "pdf", "--output", str(pdf), str(sch)],
                       check=True, capture_output=True, cwd=sch.parent)
        if PERSONAL.search(pdf.read_bytes()):
            pdf.unlink()
            sys.exit(f"{pdf.name}: exported PDF contains a personal path — not kept")
        print(f"{pdf.relative_to(ROOT)}  {pdf.stat().st_size} bytes")


if __name__ == "__main__":
    main()
