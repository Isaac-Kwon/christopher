#!/usr/bin/env python3
"""Generate the five KiCad 10 PCBs of the diagonal board: deterministic PLACEMENT only (no routing).

    python gen_diagonal_pcb.py        # exports each schematic netlist with kicad-cli, writes d1..d5.kicad_pcb

Scheme (README.md): 130 mm high, 4 layers.  Front = panel side: male D-sub 37 jacks (vertical footprint,
shells through the panel) in rows at 30 mm pitch, columns at 110 mm pitch (upper letter's two jacks in rows 1-2,
lower letter's in rows 3-4).  Back = ribbon side: three 2 x 32 IDC headers (vertical, back side) per boundary,
pin 1 at the top, identical orientation on every board (ribbon 1:1).

One jack grid across all five boards: the 110 mm column pitch holds across the board boundaries too, so the
52 jacks form one 13 x 4 grid of 1,390.5 mm (courtyards) and the five boards 1,404.5 mm, centred in the
1,440 mm bank span of the panel (column centres 60 + 110 j from the left end of the span).  Only ONE header block
fits into the 39.5 mm gap between two jack columns, so the blocks of a boundary are staggered:

  * XR1..XR3 of board k sit in the boundary gap, at the right edge of board k (XR1 nearest the edge);
  * XL1..XL3 of board k+1 sit in the gap between its jack columns 1 and 2 (XL1 nearest the boundary).

The three ribbons of a boundary then nest (XR1-XL1 innermost, spans about 91 / 113 / 135 mm) and pass over the
back of the first jack column of board k+1.  A geometric check of all this is in verify_diagonal_pcb.py.

kicad-cli runs on copies in the build directory with a timeout (it can hang on the synced work folder and
leaves .kicad_prl / lock files next to the file it opens): stage(), cli() and the wrappers below.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
import gen_diagonal as G  # noqa: E402
import ki_common as K  # noqa: E402

HERE = Path(__file__).resolve().parent
UID_NS = "6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0d02"

BOARD_H = 130.0
ROW0, ROW_PITCH = 18.0, 30.0
COL_PITCH = 110.0
JACK_CX = 24.925            # pin-field centre relative to pin 1 (x)
JACK_CY = 1.42              # relative to pin 1 (y)
JACK_L, JACK_R = -10.32, 60.17     # courtyard extents relative to pin 1 (footprint: -10.28 / 60.13)
JACK_W = JACK_R - JACK_L           # 70.49
JACK_GAP = COL_PITCH - JACK_W      # 39.51 between the courtyards of two neighbouring columns
HDR_PITCH = 10.8
HDR_COURT_L, HDR_COURT_R = 6.26, 3.73   # back-side header, rot 180: courtyard extends 6.26 left / 3.73 right of pin 1
HDR_W = HDR_COURT_L + 2 * HDR_PITCH + HDR_COURT_R   # 31.59: courtyard width of a block of three
HDR_Y1 = 65.0 - 45.015 + 5.64            # pin 1 y so that the 90 mm courtyard is centred
END_M = 7.0                 # outer ends (D1 left, D5 right): board edge - jack courtyard
HDR_CLR = 1.0               # boundary gap: jack courtyard - header courtyard
HDR_EDGE = 1.5              # boundary gap: header courtyard - board edge
JACK_EDGE = 3.0             # boundary gap: board edge - jack courtyard of the next board
BOARD_GAP = JACK_GAP - HDR_CLR - HDR_W - HDR_EDGE - JACK_EDGE   # 2.42: air between two boards
BANK_SPAN = 1440.0          # panel width under the drum columns (hardware/gear/layout.md 3.9: 12 x 120)
LABEL_DY = 10.0             # letter label below each jack (front silk)
assert BOARD_GAP >= 2.0, BOARD_GAP


def geometry(k: int) -> dict:
    """Board-local x positions: jack pin 1 and centres, header pin-1 x (index n-1 -> XLn / XRn), board length."""
    name, cols = G.BOARDS[k]
    first, last = k == 0, k == G.NB - 1
    assert first or len(cols) >= 2, "the left header block needs the gap between jack columns 1 and 2"
    x0 = END_M if first else JACK_EDGE
    pin1 = [x0 - JACK_L + ci * COL_PITCH for ci in range(len(cols))]
    centres = [p + JACK_CX for p in pin1]
    left_hdr = []
    if not first:
        s = pin1[0] + JACK_R + (JACK_GAP - HDR_W) / 2 + HDR_COURT_L       # centred in the gap after column 1
        left_hdr = [s + i * HDR_PITCH for i in range(3)]                  # XL1 nearest the boundary (left)
    court_r = pin1[-1] + JACK_R
    right_hdr = []
    if not last:
        s = court_r + HDR_CLR + HDR_COURT_L
        right_hdr = [s + (2 - i) * HDR_PITCH for i in range(3)]           # XR1 nearest the boundary (right)
        length = court_r + HDR_CLR + HDR_W + HDR_EDGE
    else:
        length = court_r + END_M
    return dict(pin1=pin1, centres=centres, left_hdr=left_hdr, right_hdr=right_hdr, length=round(length, 2))


def panel_offsets() -> list[float]:
    """x of each board's left edge, measured from the left end of the 1,440 mm bank span of the panel."""
    total = sum(geometry(k)["length"] for k in range(G.NB)) + (G.NB - 1) * BOARD_GAP
    x, out = (BANK_SPAN - total) / 2, []
    for k in range(G.NB):
        out.append(round(x, 3))
        x += geometry(k)["length"] + BOARD_GAP
    return out


def placements(k: int, nl: K.Netlist) -> tuple[dict, dict]:
    name, cols = G.BOARDS[k]
    geo = geometry(k)
    L = geo["length"]
    P = {}
    for ci, (up, lo) in enumerate(cols):
        for r, c in enumerate((up, lo)):
            for n in (1, 2):
                row = 2 * r + (n - 1)
                P[G.jack_ref(c, n)] = dict(x=geo["pin1"][ci], y=ROW0 + ROW_PITCH * row - JACK_CY, rot=0, hide=True)
    for tag, xs in (("XL", geo["left_hdr"]), ("XR", geo["right_hdr"])):
        for i, x in enumerate(xs):
            P[f"{tag}{i + 1}"] = dict(x=x, y=HDR_Y1, rot=180, flip=True, hide=True)
    nh = 6 if len(G.board_letters(k)) == 6 else 4
    pts = [(6.0, 6.0), (L - 6.0, 6.0), (6.0, BOARD_H - 6.0), (L - 6.0, BOARD_H - 6.0)]
    if nh == 6:
        pts += [(L / 2, 6.0), (L / 2, BOARD_H - 6.0)]
    for h, (x, y) in enumerate(pts, 1):
        P[f"H{h}"] = dict(x=x, y=y, rot=0, hide=True)
    P["R1"] = dict(x=L / 2 - 18.0, y=BOARD_H - 8.0, rot=0)
    P["C1"] = dict(x=L / 2 + 18.0, y=BOARD_H - 8.0, rot=0)
    if k == 0:
        P["J0V"] = dict(x=geo["pin1"][0] + 5.0, y=BOARD_H - 8.0, rot=0)
    return P, geo


def build(k: int, nl: K.Netlist) -> str:
    name, cols = G.BOARDS[k]
    uid = K.Ids(UID_NS)
    P, geo = placements(k, nl)
    L = geo["length"]
    letters = G.board_letters(k)
    extra = K.edge_rect(uid, L, BOARD_H)
    extra += K.gr_text(uid, "title", f"D{k + 1}  {' '.join(letters)}", L / 2 + 45.0, 3.2, 0, "F.SilkS", 2.0, 0.3)
    extra += K.gr_text(uid, "title_b", f"D{k + 1}  {' '.join(letters)}  (BACK: RIBBONS)", L / 2 + 45.0, 3.2, 0, "B.SilkS", 2.0, 0.3, mirror=True)
    for ci, (up, lo) in enumerate(cols):
        for r, c in enumerate((up, lo)):
            for n in (1, 2):
                row = 2 * r + (n - 1)
                extra += K.gr_text(uid, f"lbl{c}{n}", f"{c}{n}", geo["centres"][ci],
                                   ROW0 + ROW_PITCH * row + LABEL_DY, 0, "F.SilkS", 3.0, 0.4)
    for tag, xs, nb in (("XL", geo["left_hdr"], k), ("XR", geo["right_hdr"], k + 2)):
        for i, x in enumerate(xs):
            extra += K.gr_text(uid, f"{tag}{i}", f"{tag}{i + 1} D{nb}", x - 1.27, HDR_Y1 - 8.0, 0, "B.SilkS", 1.2, 0.2, mirror=True)
    lib = K.FpLib()
    return K.build_pcb("gen_diagonal_pcb.py", f"Diagonal board D{k + 1} (letters {' '.join(letters)})", "A2",
                       ["Generated by gen_diagonal_pcb.py - placement only, routing by hand",
                        f"Netlist from d{k + 1}.kicad_sch; 4 layers; jacks front (one 110 mm grid over D1-D5), junction ribbons back"],
                       nl, P, lib, uid, extra=extra, four_layers=True)


# ----------------------------------------------------------------------------- kicad-cli on staged copies
def stage(path: Path, work: Path) -> Path:
    """Copy a .kicad_sch / .kicad_pcb with its .kicad_pro into work/stage and return the copy (kicad-cli then
    writes .kicad_prl / lock files there, not next to the tracked files)."""
    d = work / "stage"
    d.mkdir(parents=True, exist_ok=True)
    for f in (path, path.with_suffix(".kicad_pro")):
        if f.exists():
            shutil.copyfile(f, d / f.name)
    return d / path.name


def cli(*args, timeout=180, tries=3):
    """kicad-cli with a timeout and retries (an occasional run never returns on this machine)."""
    for _ in range(tries):
        try:
            return subprocess.run([K.kicad_cli(), *args], check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
    raise SystemExit(f"kicad-cli {' '.join(args[:3])} did not finish in {tries} tries")


def export_netlist_text(sch: Path, work: Path) -> str:
    work.mkdir(parents=True, exist_ok=True)
    s = stage(sch, work)
    out = work / (sch.stem + ".net")
    cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(s))
    return out.read_text(encoding="utf8")


def export_netlist(sch: Path, work: Path) -> K.Netlist:
    return K.Netlist(export_netlist_text(sch, work))


def staged_pad_nets(pcb: Path, work: Path) -> dict:
    s = stage(pcb, work)
    out = work / (pcb.stem + ".d356")
    cli("pcb", "export", "ipcd356", "-o", str(out), str(s))
    res = {}
    for ln in out.read_text(encoding="utf8", errors="replace").splitlines():
        m = re.match(r"^3[12]7(\S+)\s+([^\s-]+)\s*-(\S+)\s+[DA]\d", ln)
        if m:
            res[(m[2], m[3])] = m[1]
    return res


def staged_drc(pcb: Path, work: Path) -> dict:
    s = stage(pcb, work)
    out = work / (pcb.stem + "_drc.json")
    cli("pcb", "drc", "--severity-all", "--format", "json", "-o", str(out), str(s))
    d = json.loads(out.read_text(encoding="utf8"))
    return {"violations": d.get("violations", []), "unconnected": d.get("unconnected_items", []), "staged": s}


def staged_erc(sch: Path, work: Path) -> int:
    s = stage(sch, work)
    out = work / (sch.stem + "_erc.json")
    cli("sch", "erc", "--severity-all", "--format", "json", "-o", str(out), str(s))
    d = json.loads(out.read_text(encoding="utf8"))
    return sum(len(sh.get("violations", [])) for sh in d.get("sheets", []))


def main():
    with tempfile.TemporaryDirectory() as td:
        for k in range(G.NB):
            name = G.BOARDS[k][0]
            nl = export_netlist(HERE / f"{name}.kicad_sch", Path(td))
            text = build(k, nl)
            (HERE / f"{name}.kicad_pcb").write_text(text, encoding="utf8", newline="\n")
            K.merge_board_rules(HERE / f"{name}.kicad_pro")
            P, geo = placements(k, nl)
            print(f"{name}: {len(P)} parts, {geo['length']} x {BOARD_H} mm")
    off = panel_offsets()
    end = off[-1] + geometry(G.NB - 1)["length"]
    print(f"panel (from the left end of the {BANK_SPAN:g} mm bank span): boards at {', '.join(f'{x:.2f}' for x in off)}; "
          f"{off[0]:.2f} ... {end:.2f} (length {end - off[0]:.2f}, gaps {BOARD_GAP:.2f}); jack columns 60 + 110 j")


if __name__ == "__main__":
    main()
