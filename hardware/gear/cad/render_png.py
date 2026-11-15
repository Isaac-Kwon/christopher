"""Shaded PNG views of the gear-bombe model for the report (VTK through cadquery.vis).

    python render_png.py               # writes report/figures/generated/cad_{iso,front,section,station}.png

The SVG drawings of assemble.py stay the line drawings; these PNGs are the shaded views that LaTeX can
include directly (no SVG-to-PDF converter is needed).  Each file is kept below 1.5 MB.
"""
import sys
import time
from pathlib import Path

import cadquery as cq
from cadquery import vis

import params as P
import build
from assemble import FIG_DIR, slab, to_assembly, view_parts

W, H = 1800, 1000


def shot(obj, name, position, focus, ortho=False, width=W, height=H, zoom=1.0):
    path = FIG_DIR / f"cad_{name}.png"
    vis.show(obj, screenshot=str(path), interact=False, width=width, height=height,
             position=position, focus=focus, viewup=(0, 1, 0), roll=0, elevation=0, azimuth=0,
             zoom=zoom, tolerance=0.3, trihedron=False, gradient=False, edges=False,
             orthographic=ortho, fxaa=True)
    kb = path.stat().st_size / 1024
    print(f"  {path.name}: {kb:.0f} kB")
    if kb > 1500:
        print("  WARNING: larger than 1.5 MB", file=sys.stderr)
    return path


def main():
    t0 = time.time()
    model = build.build_machine()
    print(f"model: {len(model.parts)} parts in {time.time() - t0:.1f} s")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    base = [p for p in view_parts(model) if not p.name.endswith("_contacts")]
    asm = to_assembly(base, "bombe")

    # 1. isometric from front-right-above, covers removed
    shot(asm, "iso", position=(3300, 2500, 3400), focus=(940, 820, -60))
    # 2. front view, orthographic
    shot(asm, "front", position=(940, 690, 6000), focus=(940, 690, 0), ortho=True, zoom=1 / 730)   # parallel scale = half height (mm)
    # 3. section through column 12 (slot 12), seen from +X, covers included
    x12 = P.slot_x(12)
    sec_parts = [p for p in model.parts
                 if p.group in ("column_12", "lines", "frame", "base", "guards") or p.group.startswith("blank")]
    sec = []
    for p in sec_parts:                      # cut part by part so that each keeps its colour
        cut = slab([p], x=x12)
        if cut:
            sec.append(build.Part(p.name, cut[0], p.group, p.role, p.color))
    shot(to_assembly(sec, "section"), "section", position=(x12 + 5000, 700, -80), focus=(x12, 700, -80),
         ortho=True, zoom=1 / 740, width=900, height=1400)
    # 4. one drum station (middle row of slot 12) with its shaft stack, close up
    m = build.Model()
    build.build_column(m, 12, grp="drum_station")
    st = [p for p in m.parts if p.name.startswith("s12_mid")]
    sa = to_assembly(st, "station")
    bb = cq.Compound.makeCompound([p.shape for p in st]).BoundingBox()
    c = (0.5 * (bb.xmin + bb.xmax), 0.5 * (bb.ymin + bb.ymax), 0.5 * (bb.zmin + bb.zmax))
    shot(sa, "station", position=(c[0] + 330, c[1] + 220, c[2] + 330), focus=c, width=1400, height=1000)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
