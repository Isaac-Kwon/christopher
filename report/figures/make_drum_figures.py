#!/usr/bin/env python3
"""Regenerate the drum-PCB figures of the report from the KiCad board.

    python report/figures/make_drum_figures.py            # rotor I
    python report/figures/make_drum_figures.py --rotor III

Input : hardware/gear/drum-pcb/boards/drum_<R>.kicad_pcb   (never modified; a copy is plotted)
Output: report/figures/generated/
          drum_<R>_top.png      3D render, pad side
          drum_<R>_bottom.png   3D render, back side
          drum_<R>_fcu.pdf      F.Cu (pads, stubs) + outline, vector
          drum_<R>_in1.pdf      In1.Cu (radial tracks) + outline, vector
          drum_<R>_in2.pdf      In2.Cu (arcs) + outline, vector
          drum_<R>_copper.pdf   F.Cu + In1.Cu + In2.Cu in layer colours, vector

Needs kicad-cli (KiCad 9 or later; set KICAD_CLI, or it is searched on PATH and in Program Files)
and pdfcrop (TeX Live). Standard library only. All work happens in a temporary directory, so no
KiCad side files (.kicad_prl, backups) and no absolute paths end up in the repository.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BOARDS = REPO / "hardware" / "gear" / "drum-pcb" / "boards"
OUT = HERE / "generated"

RENDER_PX = 900
VECTOR = {                       # name -> (layers, black and white)
    "fcu": ("F.Cu,Edge.Cuts", True),
    "in1": ("In1.Cu,Edge.Cuts", True),
    "in2": ("In2.Cu,Edge.Cuts", True),
    "copper": ("F.Cu,In1.Cu,In2.Cu,Edge.Cuts", False),
}


def find_kicad_cli():
    cand = [os.environ.get("KICAD_CLI"), shutil.which("kicad-cli")]
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
        if root:
            cand += sorted(glob.glob(os.path.join(root, "KiCad", "*", "bin", "kicad-cli.exe")), reverse=True)
    cand += ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]
    for c in cand:
        if c and Path(c).is_file():
            return c
    sys.exit("kicad-cli not found (set KICAD_CLI)")


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, cmd))}\n{r.stdout}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotor", default="I")
    a = ap.parse_args()
    board = BOARDS / f"drum_{a.rotor}.kicad_pcb"
    if not board.is_file():
        sys.exit(f"missing {board.relative_to(REPO)} (run hardware/gear/drum-pcb/gen_drum.py)")
    cli = find_kicad_cli()
    pdfcrop = shutil.which("pdfcrop")
    if not pdfcrop:
        sys.exit("pdfcrop not found (part of TeX Live)")
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"drum_{a.rotor}"

    with tempfile.TemporaryDirectory() as tmp:
        # Work on a copy with relative names only: nothing is written next to the real board and
        # no absolute path can be embedded in the outputs.
        shutil.copy(board, Path(tmp) / "board.kicad_pcb")
        pro = board.with_suffix(".kicad_pro")
        if pro.is_file():
            shutil.copy(pro, Path(tmp) / "board.kicad_pro")

        for side in ("top", "bottom"):
            run([cli, "pcb", "render", "--output", f"{side}.png", "--side", side,
                 "--width", str(RENDER_PX), "--height", str(RENDER_PX), "--quality", "basic",
                 "board.kicad_pcb"], tmp)
            shutil.copy(Path(tmp) / f"{side}.png", OUT / f"{stem}_{side}.png")

        for name, (layers, bw) in VECTOR.items():
            cmd = [cli, "pcb", "export", "pdf", "--mode-single", "--no-property-popups",
                   "--output", f"{name}.pdf", "--layers", layers]
            if bw:
                cmd.append("--black-and-white")
            run(cmd + ["board.kicad_pcb"], tmp)
            # The plot is a full sheet; crop to the board outline (Edge.Cuts is in every plot).
            run([pdfcrop, "--margins", "2", f"{name}.pdf", f"{name}-crop.pdf"], tmp)
            shutil.copy(Path(tmp) / f"{name}-crop.pdf", OUT / f"{stem}_{name}.pdf")

    for f in sorted(OUT.glob(f"{stem}_*")):
        print(f"{f.relative_to(REPO).as_posix():45s} {f.stat().st_size:7d} bytes")


if __name__ == "__main__":
    main()
