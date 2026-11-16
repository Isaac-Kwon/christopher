"""Shared generator helpers for the position display (module I) and the offset dial (module N) boards.

Stdlib only.  The same scheme as the counter / sequencer generators:
  * a board is a list of parts (reference, symbol, value, footprint, pin -> net) defined once (`Design`);
  * the schematic is written as KiCad 10 text: a root sheet with one block per sub-sheet, every part placed on a
    grid with a short wire + GLOBAL label on each pin (so the exported netlist has the plain net names);
  * the PCB is written as KiCad 10 text: footprints copied from the installed libraries (or a generated footprint),
    placed by a function of the reference, nets assigned pad by pad, outline + design rules.  PLACEMENT ONLY;
  * UUIDs are uuid5 of fixed names, so the same input always gives byte-identical files.

Library symbols come from the installed KiCad libraries (env KICAD_SHARE), read with the sequencer's `kilib`.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RELAY = HERE.parent
sys.path.insert(0, str(RELAY / "sequencer"))
sys.path.insert(0, str(RELAY / "counter-board"))
from kilib import LABEL_ANGLE, OUT, Sym, rot  # noqa: E402  (sequencer: symbol library reader, generic)

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
DATE = "2026-11-16"


def find_cli() -> str:
    c = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if c:
        return c
    for p in ("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "/usr/bin/kicad-cli"):
        if Path(p).exists():
            return p
    raise SystemExit("kicad-cli not found (set KICAD_CLI)")


def kicad_python() -> str:
    return os.environ.get("KICAD_PYTHON", str(Path(find_cli()).parent / "python.exe"))


def build_dir(name: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "christopher" / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def g(v: float) -> float:
    """Snap to the 2.54 mm schematic grid."""
    return round(round(v / 2.54) * 2.54, 2)


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class CustomSym(Sym):
    """A symbol whose library text is given (project library), parsed like a KiCad library symbol."""

    def __init__(self, lib: str, name: str, block: str):   # noqa: super().__init__ reads the installed libraries
        import kilib
        saved = kilib.flat_block
        kilib.flat_block = lambda _lib, _name: block
        try:
            super().__init__(lib, name)
        finally:
            kilib.flat_block = saved


# ----------------------------------------------------------------------------- design (parts and nets)
class Part:
    def __init__(self, ref, kind, value, fp, nets, sheet, group="", nc=(), desc="", show_value=True, in_bom=True):
        self.ref, self.kind, self.value, self.fp = ref, kind, value, fp
        self.nets = {str(k): v for k, v in nets.items()}
        self.nc = tuple(str(p) for p in nc)
        self.sheet, self.group, self.desc = sheet, group, desc
        self.show_value, self.in_bom = show_value, in_bom


class Design:
    def __init__(self):
        self.parts: list[Part] = []
        self.refs: set[str] = set()
        self.notes: dict[str, list[tuple[str, float, float]]] = collections.defaultdict(list)

    def add(self, *a, **k) -> Part:
        p = Part(*a, **k)
        if p.ref in self.refs:
            raise SystemExit(f"duplicate reference {p.ref}")
        self.refs.add(p.ref)
        self.parts.append(p)
        return p


# ----------------------------------------------------------------------------- schematic text
class Sheet:
    def __init__(self, project: str, ns: uuid.UUID, key: str, title: str, comments=(), paper="A2"):
        self.project, self.ns, self.key = project, ns, key
        self.uuid = self.uid("file", key)
        self.title, self.paper, self.comments = title, paper, list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.path = ""
        self.flags = 0

    def uid(self, *parts) -> str:
        return str(uuid.uuid5(self.ns, "/".join(str(p) for p in parts)))

    def _u(self, kind):
        self.n += 1
        return self.uid(self.uuid, kind, self.n)

    def wire(self, x1, y1, x2, y2):
        self.items.append(
            f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self._u("w")}")\n\t)')

    def glabel(self, name, x, y, angle):
        just = "right" if angle in (180, 270) else "left"
        self.items.append(
            f'\t(global_label "{name}"\n\t\t(shape passive)\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n'
            f'\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
            f'\t\t(uuid "{self._u("gl")}")\n\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
            f'\t\t\t(at {f(x)} {f(y)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def place(self, sym: Sym, x, y, part: Part, stub=5.08):
        x, y = g(x), g(y)
        self.libs[sym.lib_id] = sym
        pins = sym.pins(1)
        ox, oy = sym.fields.get("Reference", (0.0, 0.0))
        vx, vy = sym.fields.get("Value", (0.0, 0.0))

        def prop(name, val, pos, hide=False):
            h = "\n\t\t\t(hide yes)" if hide else ""
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {f(pos[0])} {f(pos[1])} 0){h}\n\t\t\t(effects\n'
                    f'\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')

        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom {"yes" if part.in_bom else "no"})\n'
             f'\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", part.ref, (x + ox, y - oy))
        s += prop("Value", part.value, (x + vx, y - vy), hide=not part.show_value)
        s += prop("Footprint", part.fp, (x, y), hide=True)
        s += prop("Datasheet", "", (x, y), hide=True)
        s += prop("Description", part.desc, (x, y), hide=True)
        for num in pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{self.project}"\n\t\t\t\t(path "{self.path}"\n'
              f'\t\t\t\t\t(reference "{part.ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)
        seen: dict[tuple, str] = {}
        for num, (px, py, a, pname, _t) in pins.items():
            sx, sy = round(x + px, 3), round(y - py, 3)
            key = (sx, sy)
            if num in part.nc:
                if key not in seen:
                    self.noconnect(sx, sy)
                    seen[key] = "<nc>"
            elif num in part.nets:
                if key in seen:
                    if seen[key] != part.nets[num]:
                        raise SystemExit(f"{part.ref}.{num}: stacked pins with different nets")
                    continue
                seen[key] = part.nets[num]
                dx, dy = OUT[a]
                ex, ey = sx + dx * stub, sy - dy * stub
                self.wire(sx, sy, ex, ey)
                self.glabel(part.nets[num], ex, ey, LABEL_ANGLE[(dx, dy)])
            else:
                raise SystemExit(f"{part.ref}: pin {num} ({pname}) has no net / nc")
        extra = set(part.nets) - set(pins)
        if extra:
            raise SystemExit(f"{part.ref}: nets given for unknown pins {extra}")

    def flag(self, sym_flag: Sym, net, x, y):
        """PWR_FLAG on a global net (ERC: a net driven only by passive pins, e.g. CHASSIS)."""
        x, y = g(x), g(y)
        self.glabel(net, x, y, 0)
        self.libs[sym_flag.lib_id] = sym_flag
        self.flags += 1
        ref = f"#FLG{self.flags:02d}"
        self.items.append(
            f'\t(symbol\n\t\t(lib_id "{sym_flag.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
            f'\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board no)\n\t\t(dnp no)\n'
            f'\t\t(uuid "{self._u("s")}")\n'
            f'\t\t(property "Reference" "{ref}"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
            f'\t\t(property "Value" "PWR_FLAG"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
            f'\t\t(pin "1"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
            f'\t\t(instances\n\t\t\t(project "{self.project}"\n\t\t\t\t(path "{self.path}"\n'
            f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')

    def render(self) -> str:
        head = (f'(kicad_sch\n\t(version 20250610)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                f'\t(uuid "{self.uuid}")\n\t(paper "{self.paper}")\n\t(title_block\n\t\t(title "{self.title}")\n'
                f'\t\t(date "{DATE}")\n\t\t(rev "A")\n')
        for i, c in enumerate(self.comments, 1):
            head += f'\t\t(comment {i} "{c}")\n'
        head += "\t)\n\t(lib_symbols\n"
        for s in self.libs.values():
            head += "\n".join("\t" + ln for ln in s.embedded().split("\n")) + "\n"
        head += "\t)\n"
        return head + "\n".join(self.items) + "\n\t(embedded_fonts no)\n)\n"


def sheet_block(project, ns, sheet_uuid, root_uuid, name, fname, x, y, w, h, page) -> str:
    return (f'\t(sheet\n\t\t(at {f(x)} {f(y)})\n\t\t(size {f(w)} {f(h)})\n\t\t(exclude_from_sim no)\n'
            f'\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(stroke\n\t\t\t(width 0.1524)\n'
            f'\t\t\t(type solid)\n\t\t)\n\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n\t\t(uuid "{sheet_uuid}")\n'
            f'\t\t(property "Sheetname" "{name}"\n\t\t\t(at {f(x)} {f(y - 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t(thickness 0.254)\n\t\t\t\t\t(bold yes)\n\t\t\t\t)\n'
            f'\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
            f'\t\t(property "Sheetfile" "{fname}"\n\t\t\t(at {f(x)} {f(y + h + 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n'
            f'\t\t(instances\n\t\t\t(project "{project}"\n\t\t\t\t(path "/{root_uuid}"\n\t\t\t\t\t(page "{page}")\n'
            f'\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')


# symbol kind -> grid cell (x pitch, y pitch) on a sheet; default (34, 18)
CELL = {"K": (62, 46), "DB25": (50, 100), "DA15": (50, 70), "DE9": (50, 60), "T2": (40, 20), "DIAL": (70, 50),
        "QN": (40, 22), "QP": (40, 22), "R": (34, 40), "C": (34, 40), "F": (34, 40), "TVS": (34, 40), "DZ": (34, 40), "CP": (34, 40)}
PRO = """{
  "meta": {
    "filename": "%s.kicad_pro",
    "version": 3
  },
  "schematic": {
    "drawing": {
      "default_line_thickness": 6.0,
      "default_text_size": 50.0
    }
  },
  "sheets": [],
  "text_variables": {}
}
"""


def write_schematic(outdir: Path, project: str, ns: uuid.UUID, design: Design, syms: dict, sheets: list,
                    root_title: str, root_comments: list, root_text: str, sheet_notes: dict | None = None,
                    flags: dict | None = None, xmax: float = 780.0):
    """sheets: [(key, NAME, title, paper)].  Parts go to the sheet named by part.sheet, in order; a new group starts a row."""
    root_uuid = str(uuid.uuid5(ns, "/".join(("file", "root"))))
    out = []
    for key, name, title, paper in sheets:
        sh = Sheet(project, ns, key, title, [f"Project Christopher - {project}; sheet {name}",
                                             f"Generated by gen_*.py ({project}) - do not edit by hand"], paper)
        sh.path = f"/{root_uuid}/{sh.uid('sheet', key)}"
        x0, y0 = 30.0, 50.0
        x, y, rowh, grp = x0, y0, 0.0, None
        for p in [p for p in design.parts if p.sheet == key]:
            cw, ch = CELL.get(p.kind, (34, 18))
            if (grp is not None and p.group != grp) or x + cw > xmax:
                x, y, rowh = x0, y + rowh + 6, 0.0
            grp = p.group
            sh.place(syms[p.kind], x + cw / 2, y + ch / 2, p, stub=7.62 if p.kind in ("DB25", "DA15", "DE9", "K", "DIAL") else 5.08)
            x += cw
            rowh = max(rowh, ch)
        for t, tx, ty in (sheet_notes or {}).get(key, []):
            sh.text(t, tx, ty, 1.8)
        for net, (fx, fy) in (flags or {}).get(key, {}).items():
            sh.flag(syms["FLAG"], net, fx, fy)
        out.append((name, key, sh))
    root = Sheet(project, ns, "root", root_title, root_comments, "A3")
    root.path = f"/{root_uuid}"
    for i, (name, key, sh) in enumerate(out):
        root.items.append(sheet_block(project, ns, root.uid("sheet", key), root_uuid, name, f"{key}.kicad_sch",
                                      30 + (i % 4) * 90, 120 + (i // 4) * 40, 60, 20, i + 2))
    root.text(root_text, 30, 30, 1.8)
    pro = outdir / f"{project}.kicad_pro"
    text = PRO % project
    if pro.exists():     # keep the board design rules / netclasses written by the PCB generator
        old = json.loads(pro.read_text(encoding="utf8"))
        keep = {k: v for k, v in old.items() if k in ("board", "net_settings")}
        if keep:
            text = json.dumps({**json.loads(text), **keep}, indent=2) + "\n"
    pro.write_text(text, encoding="utf8", newline="\n")
    (outdir / f"{project}.kicad_sch").write_text(root.render(), encoding="utf8", newline="\n")
    for name, key, sh in out:
        (outdir / f"{key}.kicad_sch").write_text(sh.render(), encoding="utf8", newline="\n")
    return [f"{project}.kicad_sch"] + [f"{key}.kicad_sch" for _, key, _ in out]


# ----------------------------------------------------------------------------- netlist / ERC
def export_netlist(sch: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([find_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)],
                   check=True, capture_output=True)
    return out


def run_erc(sch: Path, out: Path) -> list[dict]:
    subprocess.run([find_cli(), "sch", "erc", "--severity-all", "--format", "json", "-o", str(out), str(sch)],
                   check=True, capture_output=True)
    d = json.loads(out.read_text(encoding="utf8"))
    return [v for s in d.get("sheets", []) for v in s.get("violations", [])]


def parse_value(v: str) -> float:
    """'4.7k' -> 4700, '100n' -> 1e-7, '27k' -> 27000, '1M' -> 1e6."""
    m = re.match(r"\s*([\d.]+)\s*([pnumkKM]?)", v)
    if not m:
        raise ValueError(v)
    mult = {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6}[m.group(2)]
    return float(m.group(1)) * mult


# ----------------------------------------------------------------------------- PCB text
_fp_cache: dict[str, str] = {}


def fp_text(libid: str, custom: dict | None = None) -> str:
    if custom and libid in custom:
        return custom[libid]
    if libid not in _fp_cache:
        lib, name = libid.split(":")
        _fp_cache[libid] = (FPDIR / f"{lib}.pretty" / f"{name}.kicad_mod").read_text(encoding="utf8")
    return _fp_cache[libid]


def block_end(t: str, i: int) -> int:
    depth, j, instr = 0, i, False
    while True:
        c = t[j]
        if instr:
            if c == "\\":
                j += 1
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1


def courtyard(libid: str, custom=None) -> tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax) of the front courtyard in footprint coordinates."""
    t = fp_text(libid, custom)
    xs, ys = [], []
    for m in re.finditer(r'\((fp_line|fp_rect|fp_circle|fp_poly)((?:(?!\n\t\))[\s\S])*?)\(layer "F.CrtYd"\)', t):
        body = m.group(2)
        if m.group(1) == "fp_circle":
            c = re.search(r'\(center ([-\d.]+) ([-\d.]+)\)', body)
            e = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', body)
            cx, cy = float(c[1]), float(c[2])
            r = ((float(e[1]) - cx) ** 2 + (float(e[2]) - cy) ** 2) ** 0.5
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        else:
            for a, b in re.findall(r'\((?:start|end|xy) ([-\d.]+) ([-\d.]+)\)', body):
                xs.append(float(a))
                ys.append(float(b))
    return min(xs), max(xs), min(ys), max(ys)


def place(ns: uuid.UUID, project: str, libid: str, ref: str, value: str, x: float, y: float, rot_deg: float,
          pin_net: dict[str, tuple[int, str]], hide_ref=False, custom=None, ref_layer=None) -> str:
    """Instantiate a footprint (library or generated text) on the front side of the board."""
    def uid(*p):
        return str(uuid.uuid5(ns, "/".join(str(q) for q in p)))
    t = fp_text(libid, custom).replace("\r\n", "\n")
    name = libid.split(":")[1]
    t = re.sub(r'^\t\(version [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator_version [^)]*\)\n', "", t, flags=re.M)
    t = t.replace(f'(footprint "{name}"', f'(footprint "{libid}"', 1)
    head = (f'\t(layer "F.Cu")\n\t(uuid "{uid(project, "fp", ref)}")\n'
            f'\t(at {f(x)} {f(y)}{" " + f(rot_deg) if rot_deg else ""})\n')
    t, n = re.subn(r'^\t\(layer "F\.Cu"\)\n', head, t, count=1, flags=re.M)
    assert n == 1, libid
    t = re.sub(r'\(property "Reference" "[^"]*"', f'(property "Reference" "{ref}"', t, count=1)
    t = re.sub(r'\(property "Value" "[^"]*"', f'(property "Value" "{value}"', t, count=1)
    t = re.sub(r'\(property "Footprint" "[^"]*"', f'(property "Footprint" "{libid}"', t, count=1)

    def hide_prop(t, prop):
        i = t.find(f'(property "{prop}"')
        j = block_end(t, i)
        blk = t[i:j]
        if "(hide yes)" not in blk:
            blk = re.sub(r'(\(layer "[^"]*"\))', r'\1\n\t\t(hide yes)', blk, count=1)
        return t[:i] + blk + t[j:]
    t = hide_prop(t, "Value")
    if hide_ref:
        t = hide_prop(t, "Reference")
    if ref_layer:
        i = t.find('(property "Reference"')
        j = block_end(t, i)
        t = t[:i] + re.sub(r'\(layer "[^"]*"\)', f'(layer "{ref_layer}")', t[i:j], count=1) + t[j:]

    if rot_deg:   # pad / text angles in a board file include the footprint rotation
        def addrot(m):
            a = float(m.group(3)) if m.group(3) is not None else 0.0
            return f'{m.group(1)}(at {m.group(2)} {f((a + rot_deg) % 360)})'
        t = re.sub(r'(\((?:pad|property|fp_text)[^\n]*\n\t\t)\(at ([-\d.]+ [-\d.]+)(?: ([-\d.]+))?\)', addrot, t)

    out, pos = [], 0
    for m in re.finditer(r'\(pad "([^"]*)"', t):
        if m.start() < pos:
            continue
        end = block_end(t, m.start())
        blk = t[m.start():end]
        if m.group(1) in pin_net:
            nid, nname = pin_net[m.group(1)]
            blk = re.sub(r'(\n\t\t\(at [^\n]*\))', lambda mm: mm.group(1) + f'\n\t\t(net {nid} "{nname}")', blk, count=1)
        out.append((m.start(), end, blk))
        pos = end
    for s, e, blk in reversed(out):
        t = t[:s] + blk + t[e:]
    k = [0]

    def newuuid(m):
        k[0] += 1
        return m.group(0) if k[0] == 1 else f'(uuid "{uid(project, "fp", ref, k[0])}")'
    t = re.sub(r'\(uuid "[^"]*"\)', newuuid, t)
    return "\n".join(("\t" + ln if ln else ln) for ln in t.rstrip("\n").split("\n")) + "\n"


LAYERS4 = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" signal)
\t\t(6 "In2.Cu" signal)
\t\t(2 "B.Cu" signal)
"""
LAYERS2 = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
"""
LAYERS_USER = """\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
"""
SETUP = """\t(setup
\t\t(pad_to_mask_clearance 0.05)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
"""


def gr_text(ns, project, key, text, x, y, size=1.5, layer="F.SilkS", angle=0, mirror=False, thick=0.2, just="left"):
    u = str(uuid.uuid5(ns, f"{project}/gtext/{key}"))
    words = ([] if just == "center" else [just]) + (["mirror"] if mirror else [])
    j = f"(justify {' '.join(words)})" if words else ""
    jj = f"\n\t\t\t{j}" if j else ""
    return (f'\t(gr_text "{text}"\n\t\t(at {f(x)} {f(y)}{" " + f(angle) if angle else ""})\n\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{u}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {f(size)} {f(size)})\n'
            f'\t\t\t\t(thickness {f(thick)})\n\t\t\t){jj}\n\t\t)\n\t)\n')


def gr_rect(ns, project, key, x, y, w, h, layer="Dwgs.User", width=0.15, dash=True):
    u = str(uuid.uuid5(ns, f"{project}/grect/{key}"))
    return (f'\t(gr_rect\n\t\t(start {f(x)} {f(y)})\n\t\t(end {f(x + w)} {f(y + h)})\n'
            f'\t\t(stroke\n\t\t\t(width {f(width)})\n\t\t\t(type {"dash" if dash else "solid"})\n\t\t)\n\t\t(fill no)\n'
            f'\t\t(layer "{layer}")\n\t\t(uuid "{u}")\n\t)\n')


def nets_of(nl) -> dict[str, list[tuple[str, str]]]:
    nets: dict[str, list] = collections.defaultdict(list)
    for node, name in nl.node_net.items():
        nets[name].append(node)
    return {k: sorted(v) for k, v in nets.items()}


def board_text(ns, project, title, comments, nl, P, W, H, layers4=True, extra="", custom=None, chassis_pads=()):
    """P: ref -> (x, y, rot, hide_ref[, ref_layer]).  chassis_pads: extra (ref, pad) pairs on the CHASSIS net (D-sub shells)."""
    nets = nets_of(nl)
    netid, node_net = {}, {}
    for name in sorted(nets):
        netid[name] = len(netid) + 1
        for node in nets[name]:
            node_net[node] = (netid[name], name)
    missing = set(nl.parts) - set(P)
    assert not missing, f"unplaced parts: {sorted(missing)}"
    assert not set(P) - set(nl.parts), f"placed but not in schematic: {sorted(set(P) - set(nl.parts))}"
    o = [f'(kicad_pcb\n\t(version 20241229)\n\t(generator "gen_pcb")\n\t(generator_version "1.0")\n'
         f'\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper "A3")\n'
         f'\t(title_block\n\t\t(title "{title}")\n\t\t(rev "A")\n']
    for i, c in enumerate(comments, 1):
        o.append(f'\t\t(comment {i} "{c}")\n')
    o.append("\t)\n")
    o.append((LAYERS4 if layers4 else LAYERS2) + LAYERS_USER)
    o.append(SETUP)
    o.append('\t(net 0 "")\n')
    for name, i in netid.items():
        o.append(f'\t(net {i} "{name}")\n')
    for ref in sorted(P):
        x, y, r, hide = P[ref][:4]
        ref_layer = P[ref][4] if len(P[ref]) > 4 else None
        part = nl.parts[ref]
        pn = {p: node_net[(rr, p)] for (rr, p) in node_net if rr == ref}
        o.append(place(ns, project, part["footprint"], ref, part["value"], x, y, r, pn, hide_ref=hide, custom=custom,
                       ref_layer=ref_layer))
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        u = str(uuid.uuid5(ns, f"{project}/edge/{k}"))
        o.append(f'\t(gr_line\n\t\t(start {f(a[0])} {f(a[1])})\n\t\t(end {f(b[0])} {f(b[1])})\n'
                 f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n'
                 f'\t\t(uuid "{u}")\n\t)\n')
    o.append(extra)
    o.append(")\n")
    return "".join(o)


def merge_project(path: Path, netclasses: list[tuple[str, float, float, float]], patterns: list[tuple[str, str]],
                  copper_layers: int = 4):
    """Board design rules + netclasses into the .kicad_pro (other keys kept); the same rules as the counter boards."""
    pro = json.loads(path.read_text(encoding="utf8")) if path.exists() else {"meta": {"filename": path.name, "version": 3}}
    TRACK, CLEAR, VIA_D, VIA_DRILL = 0.15, 0.15, 0.5, 0.25
    rules = {"min_clearance": CLEAR, "min_connection": 0.0, "min_copper_edge_clearance": 0.3,
             "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2,
             "min_microvia_drill": 0.1, "min_resolved_spokes": 2, "min_silk_clearance": 0.0,
             "min_text_height": 0.8, "min_text_thickness": 0.12, "min_through_hole_diameter": VIA_DRILL,
             "min_track_width": TRACK, "min_via_annular_width": 0.125, "min_via_diameter": VIA_D,
             "solder_mask_clearance": 0.05, "solder_mask_min_width": 0.0, "use_height_for_length_calcs": True}
    sev = {k: "error" for k in ("clearance", "copper_edge_clearance", "hole_clearance", "hole_to_hole",
                                "track_width", "via_diameter", "annular_width", "through_hole_diameter",
                                "shorting_items", "unconnected_items", "invalid_outline", "malformed_courtyard",
                                "courtyards_overlap", "text_height", "text_thickness")}
    sev.update({"silk_over_copper": "ignore", "silk_overlap": "ignore", "silk_edge_clearance": "ignore",
                "missing_courtyard": "ignore", "footprint": "ignore", "footprint_type_mismatch": "ignore",
                "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
                "solder_mask_bridge": "ignore", "padstack": "warning"})
    pro["board"] = {"design_settings": {
        "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "silk_line_width": 0.15,
                     "silk_text_size_h": 1.0, "silk_text_size_v": 1.0, "silk_text_thickness": 0.15},
        "diff_pair_dimensions": [], "drc_exclusions": [], "rules": rules, "rule_severities": sev,
        "track_widths": [0.0, TRACK, 0.2, 0.3, 0.6],
        "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": VIA_D, "drill": VIA_DRILL}],
        "zones_allow_external_fillets": False}, "layer_pairs": []}

    def cls(name, track, via_d, via_drill):
        return {"name": name, "clearance": CLEAR, "track_width": track, "via_diameter": via_d,
                "via_drill": via_drill, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "wire_width": 6, "bus_width": 12, "line_style": 0,
                "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"}
    pro["net_settings"] = {"classes": [cls(*c) for c in netclasses], "meta": {"version": 4}, "net_colors": None,
                           "netclass_assignments": None,
                           "netclass_patterns": [{"netclass": c, "pattern": p} for c, p in patterns]}
    pro.setdefault("sheets", [])
    pro.setdefault("text_variables", {})
    path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")


# ----------------------------------------------------------------------------- board checks (KiCad python)
DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
fps, pads, dups, polys = {}, {}, 0, []
want_poly = len(sys.argv) > 2
for f in b.GetFootprints():
    ref = f.GetReference()
    fps[ref] = {"fpid": f.GetFPIDAsString(), "front": f.GetLayer() == pcbnew.F_Cu,
                "x": f.GetPosition().x / 1e6, "y": f.GetPosition().y / 1e6, "rot": f.GetOrientationDegrees()}
    seen_nums = set()
    for p in f.Pads():
        if p.GetNetname() and p.GetNumber() in seen_nums:
            dups += 1
        seen_nums.add(p.GetNumber())
        k = "%s|%s" % (ref, p.GetNumber())
        n = p.GetNetname()
        if p.GetNumber():
            pads[k] = n if pads.get(k, n) == n else "CONFLICT"
        if want_poly and ref == sys.argv[2]:
            layer = pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
            sp = p.GetEffectivePolygon(layer, pcbnew.ERROR_INSIDE)
            outl = []
            for i in range(sp.OutlineCount()):
                o = sp.Outline(i)
                outl.append([(o.CPoint(j).x / 1e6, o.CPoint(j).y / 1e6) for j in range(o.PointCount())])
            holes = []
            for i in range(sp.OutlineCount()):
                for h in range(sp.HoleCount(i)):
                    hh = sp.Hole(i, h)
                    holes.append([(hh.CPoint(j).x / 1e6, hh.CPoint(j).y / 1e6) for j in range(hh.PointCount())])
            polys.append({"num": p.GetNumber(), "net": n, "outlines": outl, "holes": holes,
                          "layer": "F.Cu" if layer == pcbnew.F_Cu else "B.Cu", "area": sp.Area() / 1e12})
print(json.dumps({"fps": fps, "pads": pads, "tracks": len(list(b.GetTracks())), "zones": len(list(b.Zones())),
                  "ratsnest": b.GetConnectivity().GetUnconnectedCount(False), "dups": dups, "polys": polys}))
"""


def dump_board(pcb: Path, poly_ref: str | None = None) -> dict:
    args = [kicad_python(), "-c", DUMP, str(pcb)] + ([poly_ref] if poly_ref else [])
    r = subprocess.run(args, capture_output=True, text=True, check=True)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    d["pads"] = {tuple(k.split("|", 1)): v for k, v in d["pads"].items()}
    return d


def compare_board(nl, d: dict, chassis_refs=()) -> list[str]:
    """Schematic parts == footprints (same footprint id), pad nets == schematic nets, no copper (placement only)."""
    errs = []
    for ref, part in nl.parts.items():
        fpd = d["fps"].get(ref)
        if fpd is None:
            errs.append(f"{ref}: missing on the board")
        elif fpd["fpid"] != part["footprint"]:
            errs.append(f"{ref}: footprint {fpd['fpid']} != schematic {part['footprint']}")
        elif not fpd["front"]:
            errs.append(f"{ref}: not on the front side")
    extra = set(d["fps"]) - set(nl.parts)
    if extra:
        errs.append(f"unexpected footprints: {sorted(extra)}")
    bad = 0
    for node, name in nl.node_net.items():
        got = d["pads"].get(node)
        if got is None:
            errs.append(f"pad {node}: not on the board")
        elif got != name:
            bad += 1
            if bad <= 5:
                errs.append(f"pad {node}: board net {got!r} != schematic net {name!r}")
    if bad > 5:
        errs.append(f"... {bad} pads with a wrong net in total")
    for (ref, pin), name in d["pads"].items():
        if (ref, pin) not in nl.node_net and name:
            errs.append(f"pad {(ref, pin)} carries net {name!r} but is not in the schematic netlist")
    if d["tracks"] or d["zones"]:
        errs.append(f"board contains {d['tracks']} tracks/vias and {d['zones']} zones (placement only expected)")
    return errs


def run_drc(pcb: Path, out: Path) -> dict:
    subprocess.run([find_cli(), "pcb", "drc", "--severity-all", "--format", "json", "--schematic-parity", "-o", str(out),
                    str(pcb)], check=True, capture_output=True)
    return json.loads(out.read_text(encoding="utf8"))


def swap_pad_nets(text: str, pad: str, net: str = "+24V") -> str | None:
    """Swap the nets of the first pad `pad` on `net` and the first pad `pad` on another net (a mis-wired board)."""
    pat = r'(\(pad "%s"[^)]*\)\s*\(net )(\d+ "%s")'
    m1 = re.search(pat % (re.escape(pad), re.escape(net)), text)
    m2 = re.search(r'(\(pad "%s"[^)]*\)\s*\(net )(\d+ "(?!%s")[^"]*")' % (re.escape(pad), re.escape(net)), text)
    if not (m1 and m2):
        return None
    (a1, b1), (a2, b2) = m1.span(2), m2.span(2)
    if a1 > a2:
        (a1, b1), (a2, b2) = (a2, b2), (a1, b1)
    return text[:a1] + text[a2:b2] + text[b1:a2] + text[a1:b1] + text[b2:]


def verify_pcb(project: str, here: Path, nl, generate, work: Path, swap_pad: str, flip_fp: str, swap_net: str = "+24V") -> list[str]:
    """The checks of verify_counter_pcb.py for one board: placement / pad nets, DRC + schematic parity, ratsnest count,
    three negative tests, determinism (two fresh runs identical to each other and to the repository files)."""
    import filecmp
    errs: list[str] = []
    pcb = here / f"{project}.kicad_pcb"
    nets = nets_of(nl)
    d = dump_board(pcb)
    e = compare_board(nl, d)
    errs += e
    print(f"parts: {len(nl.parts)} schematic / {len(d['fps'])} on the board, pads {len(nl.node_net)}, nets {len(nets)}")
    print("placement / pad-net comparison:", "OK" if not e else f"{len(e)} problems")
    for x in e[:10]:
        print("  ", x)
    dr = run_drc(pcb, work / f"{project}.drc.json")
    viol, unc, par = dr.get("violations", []), dr.get("unconnected_items", []), dr.get("schematic_parity", [])
    bad_par = [p for p in par if p["type"] != "footprint_symbol_field_mismatch"
               and not (p["type"] == "net_conflict" and all("pad SH " in i.get("description", "") for i in p.get("items", [])))]
    print(f"DRC: {len(viol)} violations; {len(unc)} unconnected items listed (report capped); schematic parity: {len(par)} items, "
          f"of which {len(bad_par)} are real (missing / extra / net conflicts; field texts and D-sub shell pads excluded)")
    for v in viol[:10]:
        print("  ", v["type"], v["description"], [i["description"] for i in v.get("items", [])][:2])
    for p in bad_par[:10]:
        print("  parity:", p["type"], p["description"])
    if viol:
        errs.append("DRC violations")
    if bad_par:
        errs.append("schematic parity problems")
    if {v["type"] for v in unc} - {"unconnected_items"}:
        errs.append("unexpected unconnected item type")
    expect = sum(len(n) - 1 for n in nets.values()) + d["dups"]
    print(f"ratsnest (pcbnew connectivity): {d['ratsnest']} unconnected edges, expected {expect} = sum(pads - 1) over the schematic "
          f"nets + {d['dups']} duplicate-numbered pads; tracks/vias {d['tracks']}, zones {d['zones']}")
    if d["ratsnest"] != expect:
        errs.append("ratsnest edge count mismatch")
    t = pcb.read_text(encoding="utf8")
    neg = work / f"{project}_negative.kicad_pcb"
    ok = []
    bad = swap_pad_nets(t, swap_pad, swap_net)
    if bad and bad != t:
        neg.write_text(bad, encoding="utf8", newline="\n")
        ok.append(bool(compare_board(nl, dump_board(neg))))
    else:
        ok.append(False)
    print(f"negative test 1 (nets of two '{swap_pad}' pads swapped, one on {swap_net}):", "detected" if ok[-1] else "NOT DETECTED")
    m = re.search(r'\(footprint "%s"\s*\(layer "F.Cu"\)' % re.escape(flip_fp), t)
    ok.append(False)
    if m:
        bad = t[:m.start()] + t[m.start():].replace('(layer "F.Cu")', '(layer "B.Cu")', 1)
        neg.write_text(bad, encoding="utf8", newline="\n")
        ok[-1] = any("front" in x for x in compare_board(nl, dump_board(neg)))
    print(f"negative test 2 (a {flip_fp.split(':')[1]} footprint moved to the back side):", "detected" if ok[-1] else "NOT DETECTED")
    target = "GND" if re.search(r'\(net \d+ "GND"\)', t) else swap_net     # a passive board has no 0 V
    gid = re.search(r'\(net (\d+) "%s"\)' % re.escape(target), t)
    ok.append(False)
    if gid:
        bad = re.sub(r'(\(pad "SH".*?\(net )\d+ "CHASSIS"', lambda mm: mm.group(1) + f'{gid.group(1)} "{target}"', t, count=1, flags=re.S)
        neg.write_text(bad, encoding="utf8", newline="\n")
        ok[-1] = bad != t and bool(compare_board(nl, dump_board(neg)))
    print(f"negative test 3 (a D-sub shell pad on {target} instead of CHASSIS):", "detected" if ok[-1] else "NOT DETECTED")
    if neg.exists():
        neg.unlink()
    if not all(ok):
        errs.append("negative test failed")
    dirs = []
    for k in (1, 2):
        td = work / f"det{k}"
        shutil.rmtree(td, ignore_errors=True)
        td.mkdir(parents=True)
        shutil.copy(here / f"{project}.kicad_pro", td / f"{project}.kicad_pro")
        generate(td)
        dirs.append(td)
    names = sorted(os.listdir(dirs[0]))
    same = all(filecmp.cmp(dirs[0] / x, dirs[1] / x, shallow=False) for x in names) and \
        all(filecmp.cmp(dirs[0] / x, here / x, shallow=False) for x in names)
    print(f"determinism ({', '.join(names)}: two fresh runs identical, and identical to the repository files):", "OK" if same else "DIFFERENT")
    if not same:
        errs.append("generation is not deterministic / files are stale")
    for td in dirs:
        shutil.rmtree(td, ignore_errors=True)
    return errs
