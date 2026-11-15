"""Build the gear-bombe model and export STEP + SVG views.

    python assemble.py                 # STEP (machine + subassemblies) and SVG views
    python assemble.py --no-step       # SVG only
    python assemble.py --no-svg        # STEP only

Build products (STEP, views) go to  <local cache>/christopher/cad  (Windows: %LOCALAPPDATA%,
elsewhere $XDG_CACHE_HOME or ~/.cache), never into the repository.  The 2D drawings for the report
are written to  report/figures/generated/cad_*.svg  (found relative to this file).
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cadquery as cq

import params as P
import build
from parts import drum, index, shaft, board
from parts._util import box, V, cyl

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIG_DIR = REPO / "report" / "figures" / "generated"


def build_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "christopher" / "cad"
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_assembly(parts, name):
    a = cq.Assembly(name=name)
    for p in parts:
        r, g, b = build.COLORS.get(p.color, (0.5, 0.5, 0.5))
        alpha = 0.35 if p.color == "pc" else 1.0
        a.add(p.shape, name=p.name, color=cq.Color(r, g, b, alpha))
    return a


def export_step(parts, path, name):
    a = to_assembly(parts, name)
    try:
        a.export(str(path), "STEP")
    except TypeError:
        a.save(str(path), "STEP")
    return path


def export_all_step(model, out):
    done = {}
    done["machine"] = export_step(model.parts, out / "gear_bombe_machine.step", "gear_bombe_machine")
    for g in model.groups():
        if g.startswith("blank_"):
            continue
        parts = [p for p in model.parts if p.group == g]
        done[g] = export_step(parts, out / f"sub_{g}.step", g)
    # one drum on its own, with its shaft stack (design check part)
    m = build.Model()
    build.build_column(m, 12, grp="drum_station")
    parts = [p for p in m.parts if p.name.startswith("s12_mid")]
    done["drum_station"] = export_step(parts, out / "sub_drum_station_mid_row.step", "drum_station")
    blanks = [p for p in model.parts if p.group.startswith("blank_")]
    done["blanks"] = export_step(blanks, out / "sub_blanking_plates.step", "blanking_plates")
    return done


# ------------------------------------------------------------------------------------------
VIEWS = {   # (axis, angle) rotations applied before a projection along +Z (X right, Y up on screen)
    "front": [],
    "side": [((0, 1, 0), -90.0)],                     # camera on the +X side, front of the machine at the left
    "top": [((1, 0, 0), 90.0)],                       # plan view, front of the machine at the bottom
    "iso": [((0, 1, 0), -35.0), ((1, 0, 0), 25.0)],   # camera front-right-above
}


def svg_of(shapes, view, width=1600, height=900, hidden=False, stroke=0.6):
    comp = cq.Compound.makeCompound(list(shapes))
    for axis, ang in VIEWS[view]:
        comp = comp.rotate(V(0, 0, 0), V(*axis), ang)
    return cq.exporters.getSVG(comp, {"width": width, "height": height, "marginLeft": 20, "marginTop": 20,
                                      "projectionDir": (0, 0, 1), "showAxes": False, "strokeWidth": stroke,
                                      "strokeColor": (20, 20, 20), "hiddenColor": (160, 160, 160),
                                      "showHidden": hidden})


def slab(parts, x=None, y=None):
    """Section by intersecting with a half-space slab (keeps the cut face visible from +X or +Y)."""
    out = []
    if x is not None:
        cutter = box(x - 40.0, x, 0.0, 2000.0, -400.0, 400.0)
    else:
        cutter = box(0.0, 2000.0, y - 40.0, y, -400.0, 400.0)
    for p in parts:
        b = p.shape.BoundingBox()
        if x is not None and (b.xmax < x - 40.0 or b.xmin > x):
            continue
        if y is not None and (b.ymax < y - 40.0 or b.ymin > y):
            continue
        s = p.shape.intersect(cutter)
        if s.Volume() > 1e-3:
            out.append(s)
    return out


SKIP_VIEW = ("_contacts", "_drum_pads", "_board_posts", "_spacer_posts", "indicator_posts")


FIG_SKIP = ("_bearing_front", "_bearing_rear", "_shim", "_flange", "_index_disc", "_sleeve", "_drum_pcb", "_board",
            "_mitre_drum", "_mitre_line", "_shaft")        # small hidden detail: keeps each report figure < 1 MB


def fig_parts(parts):
    out = []
    for p in parts:
        if p.name.endswith(FIG_SKIP):
            continue
        if p.name.endswith("_drum_knob"):                     # plain cylinder instead of the cam tracks
            b = p.shape.BoundingBox()
            p = build.Part(p.name, cyl(P.KNOB_D / 2, (0.5 * (b.xmin + b.xmax), 0.5 * (b.ymin + b.ymax), b.zmin),
                                       (0.5 * (b.xmin + b.xmax), 0.5 * (b.ymin + b.ymax), b.zmax)), p.group, p.role, p.color)
        out.append(p)
    return out


def iso_shapes(parts):
    """Stand-ins for the isometric report figure: bored plates become blocks, drum bodies plain cylinders
    (bore circles are the main cost of a vector drawing); gear detail is left out."""
    out = []
    for p in parts:
        n = p.name
        if n.startswith(("carry", "posdisc", "slot_disc", "drive_train", "belt", "pulley", "indicator_plate", "torque"))                 or n.endswith("_bearings"):
            continue
        b = p.shape.BoundingBox()
        if n.endswith(("_plate_front", "_plate_rear")) or n.startswith(("drive_plate", "bracket")):
            out.append(box(b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax))
        elif n.endswith("_drum_body"):
            x, y = 0.5 * (b.xmin + b.xmax), 0.5 * (b.ymin + b.ymax)
            out.append(cyl(P.BODY_REAR_D / 2, (x, y, b.zmin), (x, y, b.zmin + P.BODY_REAR_L)))
            out.append(cyl(P.GRIP_D / 2, (x, y, b.zmin + P.BODY_REAR_L), (x, y, b.zmax)))
        elif n.startswith("indicator") and n.endswith("_drum"):
            x, y = 0.5 * (b.xmin + b.xmax), 0.5 * (b.ymin + b.ymax)
            out.append(cyl(P.INDICATOR_D / 2, (x, y, b.zmin), (x, y, b.zmax)))
        else:
            out.append(p.shape)
    return out


def view_parts(model, with_guards=False, with_contacts=False):
    out = []
    for p in model.parts:
        if p.group == "guards" and not with_guards:
            continue
        if not with_contacts and p.name.endswith(SKIP_VIEW):
            continue
        out.append(p)
    return out


def slim_svg(text):
    """Round path coordinates to 0.01 drawing unit (HLR output is very verbose)."""
    import re
    text = re.sub(r"(-?\d+\.\d{2})\d+", r"\g<1>", text)
    return re.sub(r"(\.\d)0(?!\d)", r"\g<1>", text)


def write_svg(path, text, label):
    Path(path).write_text(slim_svg(text), encoding="utf-8")
    kb = Path(path).stat().st_size / 1024
    print(f"  {label}: {path.name} {kb:.0f} kB")
    return kb


def export_views(model, out, with_figs=True):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    sizes = {}
    base = view_parts(model)
    shapes = [p.shape for p in base]
    t = time.time()
    views = {
        "front": ("front", shapes, 1900, 700),
        "side": ("side", shapes, 1100, 900),
        "iso": ("iso", shapes, 1900, 1100),
    }
    for name, (d, shp, w, h) in views.items():
        txt = svg_of(shp, d, w, h)
        sizes[name] = write_svg(out / f"view_{name}.svg", txt, f"view {name} ({time.time() - t:.0f} s)")
    if not with_figs:
        return sizes
    # report figures (copies of the useful views + two sections)
    sec_col = slab([p for p in base if p.group in ("column_12", "lines", "frame", "base", "drive") or p.group.startswith("blank")],
                   x=P.slot_x(12))
    sec_col_g = [p for p in model.parts if p.group == "guards"]
    sec_col += slab(sec_col_g, x=P.slot_x(12))
    write_svg(FIG_DIR / "cad_section_column.svg", svg_of(sec_col, "side", 1100, 800), "section column 12")
    plan = slab([p for p in model.parts if p.group != "base" and not p.name.endswith(SKIP_VIEW)], y=P.ROW_Y["mid"])
    write_svg(FIG_DIR / "cad_plan_midrow.svg", svg_of(plan, "top", 1900, 700), "plan section, middle row")
    fshapes = [p.shape for p in fig_parts(base)]
    write_svg(FIG_DIR / "cad_front.svg", svg_of(shapes, "front", 1900, 700), "front")
    ishapes = iso_shapes(fig_parts(base))
    write_svg(FIG_DIR / "cad_iso.svg", svg_of(ishapes, "iso", 1900, 1100), "isometric (gear detail left out)")
    g = [p.shape for p in model.parts if p.group == "drive" and p.name.startswith(
        ("drive_shaft", "drive_train", "carry", "posdisc", "slot_disc", "pulley", "belt", "drive_coupling", "torque", "motor"))]
    write_svg(FIG_DIR / "cad_drive_gears.svg", svg_of(g, "side", 1100, 900), "drive module gears (view from +X)")
    return sizes


def try_png(model, out):
    """Optional shaded PNG through VTK (installed together with CadQuery); skipped when unavailable."""
    try:
        from cadquery import vis
        parts = [p for p in view_parts(model) if not p.name.endswith("_contacts")]
        asm = to_assembly(parts, "bombe")
        for name, pos in (("iso", (2900, 2300, 3200)), ("iso_back", (-1000, 2200, -3000))):
            vis.show(asm, screenshot=str(out / f"{name}.png"), interact=False, width=1600, height=900,
                     position=pos, focus=(940, 850, -30), viewup=(0, 1, 0), roll=0, elevation=0, azimuth=0, zoom=1.0, tolerance=0.5,
                     trihedron=False, gradient=False, edges=False)
            print("  PNG:", name)
    except Exception as e:                                           # noqa: BLE001
        print("  PNG skipped:", repr(e)[:160])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-step", action="store_true")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--png", action="store_true")
    a = ap.parse_args(argv)
    out = build_dir()
    t0 = time.time()
    model = build.build_machine()
    print(f"model: {len(model.parts)} parts in {time.time() - t0:.1f} s; build dir {out.name} (under the local cache)")
    if not a.no_step:
        done = export_all_step(model, out)
        for k, v in done.items():
            print(f"  STEP {k}: {v.name} {v.stat().st_size / 1e6:.1f} MB")
    if not a.no_svg:
        export_views(model, out)
    if a.png:
        try_png(model, out)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
