"""Shared helpers for the generated KiCad 10 work of Project Christopher (stdlib only).

The same file is kept (identical copy) in hardware/gear/contact-board, hardware/common/diagonal-board and
hardware/common/patch-panel so that every folder runs on its own.  It provides

  * Sym / Sheet     - flat KiCad 10 schematic writer; library symbols are copied from the installed
                      KiCad standard libraries (env KICAD_SHARE overrides the install location),
  * Netlist         - reader of the s-expression netlist exported by kicad-cli,
  * Pcb helpers     - footprint placement from the standard or a project .pretty library (front and back
                      side), board outline/holes/text, design rules written into the .kicad_pro,
  * check helpers   - kicad-cli ERC / DRC / IPC-D-356, placement-vs-schematic comparison.

Placement only: nothing here creates copper tracks.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
SYMDIR = KICAD_SHARE / "symbols"
FPDIR = KICAD_SHARE / "footprints"
N = 26


# ----------------------------------------------------------------------------- small helpers
def kicad_cli() -> str:
    c = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if c:
        return c
    for p in ("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "/usr/bin/kicad-cli"):
        if Path(p).exists():
            return p
    raise SystemExit("kicad-cli not found (set KICAD_CLI)")


def kicad_python() -> str | None:
    p = os.environ.get("KICAD_PYTHON") or str(Path(kicad_cli()).parent / "python.exe")
    return p if Path(p).exists() else None


def run_cli(*a, check=True):
    return subprocess.run([kicad_cli(), *a], check=check, capture_output=True, text=True)


def default_build_dir(name: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "christopher" / name


def g(v: float) -> float:
    """Snap to the 2.54 mm grid (keeps pin and wire ends on the 1.27 mm connection grid)."""
    return round(round(v / 2.54) * 2.54, 2)


def letter(i: int) -> str:
    return chr(ord("A") + i)


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


fmt = f


class Ids:
    """Stable UUIDs: uuid5 of a namespace and a path of parts."""

    def __init__(self, ns: str):
        self.ns = uuid.UUID(ns)

    def __call__(self, *parts) -> str:
        return str(uuid.uuid5(self.ns, "/".join(str(p) for p in parts)))


# ----------------------------------------------------------------------------- symbols
_lib_cache: dict[str, str] = {}


def lib_text(lib: str) -> str:
    if lib not in _lib_cache:
        _lib_cache[lib] = (SYMDIR / f"{lib}.kicad_sym").read_text(encoding="utf8")
    return _lib_cache[lib]


def sym_block(lib: str, name: str) -> str:
    t = lib_text(lib)
    m = re.search(r'\n\t\(symbol "%s"\n' % re.escape(name), t)
    if not m:
        raise SystemExit(f"symbol {lib}:{name} not found")
    i = m.start() + 1
    depth, j = 0, i
    while True:
        c = t[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    b = t[i:j + 1]
    if "(extends" in b:
        raise SystemExit(f"{lib}:{name} uses extends; pick a base symbol")
    return b


class Sym:
    def __init__(self, lib: str, name: str):
        self.lib, self.name = lib, name
        self.block = sym_block(lib, name)
        self.lib_id = f"{lib}:{name}"
        self.pins: dict[str, tuple[float, float, int]] = {}
        for m in re.finditer(
                r'\(pin \w+ \w+\s+\(at ([-\d.]+) ([-\d.]+) (\d+)\).*?\(number "([^"]*)"', self.block, re.S):
            self.pins[m[4]] = (float(m[1]), float(m[2]), int(m[3]))
        self.fields = {}
        for m in re.finditer(r'\(property "(Reference|Value)"\s+"[^"]*"\s+\(at ([-\d.]+) ([-\d.]+) ', self.block):
            self.fields[m[1]] = (float(m[2]), float(m[3]))

    def embedded(self) -> str:
        return self.block.replace(f'(symbol "{self.name}"', f'(symbol "{self.lib_id}"', 1)


OUT = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}      # pin angle -> outward unit (x right, y up)
LABEL_ANGLE = {(-1, 0): 180, (1, 0): 0, (0, 1): 90, (0, -1): 270}


class Sheet:
    """One flat schematic sheet (file)."""

    def __init__(self, ids: Ids, project: str, paper: str, title: str, comments=(), inst_path=""):
        self.ids, self.project, self.paper, self.title = ids, project, paper, title
        self.comments, self.inst_path = list(comments), inst_path
        self.uuid = ids("sheetfile", project)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.no_bom: set[str] = set()

    def _u(self, kind: str) -> str:
        self.n += 1
        return self.ids(self.uuid, kind, self.n)

    def wire(self, x1, y1, x2, y2):
        self.items.append(
            f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self._u("w")}")\n\t)')

    def label(self, name, x, y, angle):
        just = "right bottom" if angle in (180, 270) else "left bottom"
        self.items.append(
            f'\t(label "{name}"\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{self._u("l")}")\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def symbol(self, sym: Sym, x, y, ref, value, footprint, datasheet="", desc="", show_value=True,
               in_bom=True, on_board=True):
        self.libs[sym.lib_id] = sym
        x, y = g(x), g(y)

        def prop(name, val, key=None, hide=False):
            ox, oy = sym.fields.get(key or name, (0.0, 0.0))
            h = "\n\t\t\t(hide yes)" if hide else ""
            pos = f"{f(x + ox)} {f(y - oy)}" if key else f"{f(x)} {f(y)}"
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {pos} 0){h}\n\t\t\t(effects\n\t\t\t\t(font\n'
                    f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')
        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom {"yes" if in_bom else "no"})\n'
             f'\t\t(on_board {"yes" if on_board else "no"})\n\t\t(dnp no)\n'
             f'\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", ref, "Reference")
        s += prop("Value", value, "Value", hide=not show_value)
        s += prop("Footprint", footprint, hide=True)
        s += prop("Datasheet", datasheet, hide=True)
        s += prop("Description", desc, hide=True)
        for num in sym.pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{self.project}"\n\t\t\t\t(path "{self.inst_path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

    def pin_xy(self, sym: Sym, x, y, num):
        px, py, a = sym.pins[num]
        return g(x) + px, g(y) - py, OUT[a]

    def pin_net(self, sym, x, y, num, name, length=2.54):
        px, py, (dx, dy) = self.pin_xy(sym, x, y, num)
        ex, ey = px + dx * length, py - dy * length
        self.wire(px, py, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[(dx, dy)])

    def part(self, sym, x, y, ref, value, footprint, nets: dict, datasheet="", desc="", length=2.54,
             show_value=True, in_bom=True, on_board=True):
        """Place a symbol; `nets` maps pin number -> net name; every other pin gets a no-connect flag."""
        self.symbol(sym, x, y, ref, value, footprint, datasheet, desc, show_value, in_bom, on_board)
        for num in sym.pins:
            if num in nets:
                self.pin_net(sym, x, y, num, nets[num], length)
            else:
                px, py, _ = self.pin_xy(sym, x, y, num)
                self.noconnect(px, py)
        missing = set(nets) - set(sym.pins)
        assert not missing, (ref, missing)

    def render(self) -> str:
        head = (f'(kicad_sch\n\t(version 20250610)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                f'\t(uuid "{self.uuid}")\n\t(paper "{self.paper}")\n\t(title_block\n\t\t(title "{self.title}")\n'
                f'\t\t(date "2026-11-15")\n\t\t(rev "A")\n')
        for i, c in enumerate(self.comments, 1):
            head += f'\t\t(comment {i} "{c}")\n'
        head += "\t)\n\t(lib_symbols\n"
        for s in self.libs.values():
            head += "\n".join("\t" + ln for ln in s.embedded().split("\n")) + "\n"
        head += "\t)\n"
        return head + "\n".join(self.items) + "\n\t(embedded_fonts no)\n)\n"


PRO_SCH = """{
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


def write_project_and_sheet(outdir: Path, project: str, sheet: Sheet, fp_libs: dict[str, str] | None = None):
    """Write <project>.kicad_pro (unless it exists: the PCB step merges rules into it) and .kicad_sch."""
    pro = outdir / f"{project}.kicad_pro"
    if not pro.exists():
        pro.write_text(PRO_SCH % project, encoding="utf8", newline="\n")
    (outdir / f"{project}.kicad_sch").write_text(sheet.render(), encoding="utf8", newline="\n")


def write_fp_lib_table(outdir: Path, libs: dict[str, str]):
    """libs: name -> uri relative to the project, e.g. {'Christopher': '${KIPRJMOD}/Christopher.pretty'}."""
    rows = "".join(f'\t(lib (name "{n}")(type "KiCad")(uri "{u}")(options "")(descr "Project Christopher"))\n'
                   for n, u in libs.items())
    (outdir / "fp-lib-table").write_text(f"(fp_lib_table\n\t(version 7)\n{rows})\n", encoding="utf8", newline="\n")


# ----------------------------------------------------------------------------- netlist reader
def _tokenize(text: str):
    for m in re.finditer(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+', text):
        yield m.group(0) if m.group(1) is None else ("s", m.group(1))


def sexp_parse(text: str):
    stack: list[list] = [[]]
    for tok in _tokenize(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            lst = stack.pop()
            stack[-1].append(lst)
        else:
            stack[-1].append(tok[1] if isinstance(tok, tuple) else tok)
    return stack[0][0]


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def find1(node, key):
    r = find_all(node, key)
    return r[0] if r else None


class Netlist:
    """parts: ref -> {value, footprint}; nets: name -> [(ref, pin)]; node_net: (ref, pin) -> name."""

    def __init__(self, text: str):
        tree = sexp_parse(text)
        self.parts = {}
        for c in find_all(find1(tree, "components"), "comp"):
            ref = find1(c, "ref")[1]
            self.parts[ref] = dict(value=(find1(c, "value") or [0, ""])[1],
                                   footprint=(find1(c, "footprint") or [0, ""])[1])
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for n in find_all(find1(tree, "nets"), "net"):
            name = find1(n, "name")[1]
            self.nets[name] = [(find1(x, "ref")[1], find1(x, "pin")[1]) for x in find_all(n, "node")]
        self.node_net = {nd: name for name, nodes in self.nets.items() for nd in nodes}

    @classmethod
    def export(cls, sch: Path, work: Path) -> "Netlist":
        work.mkdir(parents=True, exist_ok=True)
        out = work / (sch.stem + ".net")
        run_cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch))
        return cls(out.read_text(encoding="utf8"))


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

    def groups(self) -> dict:
        out = defaultdict(set)
        for a in list(self.p):
            out[self.find(a)].add(a)
        return out


def swap_pins(text: str, ref: str, pa: str, pb: str) -> str:
    """Swap the pin numbers of two node entries of `ref` in a netlist text (moves them between nets)."""
    pat = lambda p: re.compile(r'(\(ref "%s"\)\s*\(pin )"%s"' % (re.escape(ref), re.escape(p)))
    t, na = pat(pa).subn(r'\1"@@A@@"', text)
    t, nb = pat(pb).subn(r'\1"@@B@@"', t)
    assert na == 1 and nb == 1, (ref, pa, pb, na, nb)
    return t.replace('"@@A@@"', f'"{pb}"').replace('"@@B@@"', f'"{pa}"')


def run_erc(sch: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    out = work / (sch.stem + "_erc.json")
    run_cli("sch", "erc", "--severity-all", "--format", "json", "-o", str(out), str(sch))
    d = json.loads(out.read_text(encoding="utf8"))
    items = [v for sh in d.get("sheets", []) for v in sh.get("violations", [])]
    return {"violations": items, "count": len(items)}


# ----------------------------------------------------------------------------- footprints
class FpLib:
    """Footprint text from the KiCad standard libraries and optional project .pretty directories."""

    def __init__(self, extra: dict[str, Path] | None = None):
        self.extra = extra or {}
        self._cache: dict[str, str] = {}

    def text(self, libid: str) -> str:
        if libid not in self._cache:
            lib, name = libid.split(":")
            base = self.extra.get(lib, FPDIR / f"{lib}.pretty")
            self._cache[libid] = (Path(base) / f"{name}.kicad_mod").read_text(encoding="utf8")
        return self._cache[libid]


def block_end(t: str, i: int) -> int:
    """Index just after the s-expression block that starts at t[i] == '('."""
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


def _swap_side(t: str) -> str:
    return re.sub(r'"([FB])\.(\w+)"', lambda m: f'"{"B" if m[1] == "F" else "F"}.{m[2]}"', t)


def _mirror_text(t: str) -> str:
    def fix(m):
        blk = m.group(0)
        if "(justify" in blk:
            return re.sub(r'\(justify ([^)]*)\)', lambda k: f'(justify {k[1]} mirror)' if "mirror" not in k[1] else k[0], blk)
        return blk[:-1].rstrip() + "\n\t\t\t(justify mirror)\n\t\t)" if blk.endswith(")") else blk
    out, pos, res = 0, 0, []
    for m in re.finditer(r'\(effects', t):
        if m.start() < pos:
            continue
        e = block_end(t, m.start())
        res.append(t[pos:m.start()])
        blk = t[m.start():e]
        if "(justify" in blk:
            blk = re.sub(r'\(justify ([^)]*)\)',
                         lambda k: k[0] if "mirror" in k[1] else f'(justify {k[1]} mirror)', blk)
        else:
            blk = blk[:-1].rstrip() + "\n\t\t\t(justify mirror)\n\t\t)"
        res.append(blk)
        pos = e
    res.append(t[pos:])
    return "".join(res)


def _mirror_y(t: str) -> str:
    """Mirror every coordinate of a footprint body in y (what KiCad stores for a back-side footprint)."""
    def at(m):
        a = m.group(4)
        return f'(at {m.group(2)} {fmt(-float(m.group(3)))}' + (f' {fmt((-float(a)) % 360)}' if a else '') + ')'
    t = re.sub(r'\((at) ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', at, t)
    t = re.sub(r'\((start|end|center|mid|xy) ([-\d.]+) ([-\d.]+)\)',
               lambda m: f'({m.group(1)} {m.group(2)} {fmt(-float(m.group(3)))})', t)
    return t


def rot_f(theta_deg: float, a: float, b: float) -> tuple[float, float]:
    """KiCad rotation of the vector (a, b) (y down, positive angle = counter-clockwise on screen)."""
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    return a * c + b * s, -a * s + b * c


def pad_xy(x0, y0, theta, flip, lx, ly):
    """Board position of a pad at library position (lx, ly) of a footprint placed with `(at x0 y0 theta)`.
    A back-side footprint is stored mirrored in y (checked against pcbnew's Flip)."""
    if flip:
        ly = -ly
    dx, dy = rot_f(theta, lx, ly)
    return x0 + dx, y0 + dy


def place(lib: FpLib, uid: Ids, libid: str, ref: str, value: str, x: float, y: float, rot: float,
          pin_net: dict[str, tuple[int, str]], hide_ref=False, flip=False) -> str:
    """Instantiate a library footprint on the board.  `rot` is the value written after (at x y);
    flip=True puts it on B.Cu (layers swapped, texts mirrored)."""
    t = lib.text(libid).replace("\r\n", "\n")
    name = libid.split(":")[1]
    t = re.sub(r'^\t\(version [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator [^)]*\)\n', "", t, flags=re.M)
    t = re.sub(r'^\t\(generator_version [^)]*\)\n', "", t, flags=re.M)
    t = t.replace(f'(footprint "{name}"', f'(footprint "{libid}"', 1)
    side = "B.Cu" if flip else "F.Cu"
    head = f'\t(layer "{side}")\n\t(uuid "{uid("fp", ref)}")\n\t(at {fmt(x)} {fmt(y)}{" " + fmt(rot) if rot else ""})\n'
    t, n = re.subn(r'^\t\(layer "F\.Cu"\)\n', head, t, count=1, flags=re.M)
    assert n == 1, libid
    if flip:
        body_start = t.index(head) + len(head)
        t = t[:body_start] + _mirror_text(_mirror_y(_swap_side(t[body_start:])))
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

    if rot:
        def addrot(m):
            a = float(m.group(3)) if m.group(3) is not None else 0.0
            return f'{m.group(1)}(at {m.group(2)} {fmt((a + rot) % 360)})'
        t = re.sub(r'(\((?:pad|property|fp_text)[^\n]*\n\t\t)\(at ([-\d.]+ [-\d.]+)(?: ([-\d.]+))?\)', addrot, t)

    out, pos = [], 0
    for m in re.finditer(r'\(pad "([^"]*)"', t):
        if m.start() < pos:
            continue
        end = block_end(t, m.start())
        blk = t[m.start():end]
        pn = m.group(1)
        if pn in pin_net:
            nid, nname = pin_net[pn]
            blk = re.sub(r'(\n\t\t\(at [^\n]*\))', lambda mm: mm.group(1) + f'\n\t\t(net {nid} "{nname}")', blk, count=1)
        out.append((m.start(), end, blk))
        pos = end
    for s, e, blk in reversed(out):
        t = t[:s] + blk + t[e:]

    k = [0]

    def newuuid(m):
        k[0] += 1
        if k[0] == 1:
            return m.group(0)
        return f'(uuid "{uid("fp", ref, k[0])}")'
    t = re.sub(r'\(uuid "[^"]*"\)', newuuid, t)
    return "\n".join(("\t" + ln if ln else ln) for ln in t.rstrip("\n").split("\n")) + "\n"


def npth_hole(uid: Ids, ref: str, x: float, y: float, d: float) -> str:
    """Bare non-plated hole (a footprint with one np_thru_hole pad)."""
    return (f'\t(footprint "Christopher:NPTH_{fmt(d)}mm"\n\t\t(layer "F.Cu")\n\t\t(uuid "{uid("hole", ref)}")\n'
            f'\t\t(at {fmt(x)} {fmt(y)})\n'
            f'\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid("hole", ref, "r")}")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            f'\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            f'\t\t(property "Value" "NPTH {fmt(d)}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid("hole", ref, "v")}")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            f'\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            f'\t\t(attr exclude_from_pos_files exclude_from_bom board_only)\n'
            f'\t\t(pad "" np_thru_hole circle\n\t\t\t(at 0 0)\n\t\t\t(size {fmt(d)} {fmt(d)})\n\t\t\t(drill {fmt(d)})\n'
            f'\t\t\t(layers "*.Cu" "*.Mask")\n\t\t\t(uuid "{uid("hole", ref, "p")}")\n\t\t)\n'
            f'\t\t(fp_circle\n\t\t\t(center 0 0)\n\t\t\t(end {fmt(d / 2 + 0.3)} 0)\n\t\t\t(stroke\n\t\t\t\t(width 0.05)\n'
            f'\t\t\t\t(type solid)\n\t\t\t)\n\t\t\t(fill no)\n\t\t\t(layer "F.CrtYd")\n'
            f'\t\t\t(uuid "{uid("hole", ref, "c")}")\n\t\t)\n\t)\n')


def edge_line(uid: Ids, key, a, b) -> str:
    return (f'\t(gr_line\n\t\t(start {fmt(a[0])} {fmt(a[1])})\n\t\t(end {fmt(b[0])} {fmt(b[1])})\n'
            f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n'
            f'\t\t(uuid "{uid("edge", key)}")\n\t)\n')


def edge_circle(uid: Ids, key, c, r) -> str:
    return (f'\t(gr_circle\n\t\t(center {fmt(c[0])} {fmt(c[1])})\n\t\t(end {fmt(c[0] + r)} {fmt(c[1])})\n'
            f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(fill no)\n\t\t(layer "Edge.Cuts")\n'
            f'\t\t(uuid "{uid("edge", key)}")\n\t)\n')


def edge_rect(uid: Ids, w: float, h: float, x0=0.0, y0=0.0) -> str:
    c = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    return "".join(edge_line(uid, k, c[k], c[(k + 1) % 4]) for k in range(4))


def gr_text(uid: Ids, key, text, x, y, rot=0, layer="F.SilkS", size=1.5, thick=0.2, mirror=False) -> str:
    j = "\n\t\t\t(justify mirror)" if mirror else ""
    return (f'\t(gr_text "{text}"\n\t\t(at {fmt(x)} {fmt(y)} {fmt(rot)})\n\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{uid("txt", key)}")\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {fmt(size)} {fmt(size)})\n'
            f'\t\t\t\t(thickness {fmt(thick)})\n\t\t\t){j}\n\t\t)\n\t)\n')


def gr_poly(uid: Ids, key, pts, layer="F.SilkS") -> str:
    p = " ".join(f"(xy {fmt(x)} {fmt(y)})" for x, y in pts)
    return (f'\t(gr_poly\n\t\t(pts {p})\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type solid)\n\t\t)\n'
            f'\t\t(fill yes)\n\t\t(layer "{layer}")\n\t\t(uuid "{uid("poly", key)}")\n\t)\n')


PCB_LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" signal)
\t\t(6 "In2.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
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

PCB_LAYERS_2 = PCB_LAYERS.replace('\t\t(4 "In1.Cu" signal)\n\t\t(6 "In2.Cu" signal)\n', "")

PCB_SETUP = """\t(setup
\t\t(pad_to_mask_clearance 0.05)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
"""


def pcb_header(generator: str, title: str, paper: str, comments: list[str], four_layers=True) -> str:
    s = (f'(kicad_pcb\n\t(version 20241229)\n\t(generator "{generator}")\n\t(generator_version "1.0")\n'
         f'\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper "{paper}")\n'
         f'\t(title_block\n\t\t(title "{title}")\n\t\t(rev "A")\n')
    for i, c in enumerate(comments, 1):
        s += f'\t\t(comment {i} "{c}")\n'
    s += "\t)\n" + (PCB_LAYERS if four_layers else PCB_LAYERS_2) + PCB_SETUP
    return s


def merge_board_rules(path: Path, track=0.2, clear=0.15, via_d=0.6, via_drill=0.3, min_track=0.15):
    pro = json.loads(path.read_text(encoding="utf8")) if path.exists() else {"meta": {"filename": path.name, "version": 3}}
    rules = {"min_clearance": clear, "min_connection": 0.0, "min_copper_edge_clearance": 0.3,
             "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2,
             "min_microvia_drill": 0.1, "min_resolved_spokes": 2, "min_silk_clearance": 0.0,
             "min_text_height": 0.8, "min_text_thickness": 0.12, "min_through_hole_diameter": via_drill,
             "min_track_width": min_track, "min_via_annular_width": 0.125, "min_via_diameter": via_d,
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
        "track_widths": [0.0, min_track, track, 0.3, 0.5],
        "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": via_d, "drill": via_drill}],
        "zones_allow_external_fillets": False}, "layer_pairs": []}
    pro["net_settings"] = {"classes": [{
        "name": "Default", "clearance": clear, "track_width": track, "via_diameter": via_d, "via_drill": via_drill,
        "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2, "microvia_diameter": 0.3,
        "microvia_drill": 0.1, "wire_width": 6, "bus_width": 12, "line_style": 0,
        "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"}],
        "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None, "netclass_patterns": []}
    pro.setdefault("sheets", [])
    pro.setdefault("text_variables", {})
    path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf8", newline="\n")


def assign_nets(nl: Netlist):
    """Net ids for the PCB and the pad -> (id, name) map."""
    netid, node_net = {}, {}
    for name in sorted(nl.nets):
        netid[name] = len(netid) + 1
        for ref, pin in nl.nets[name]:
            node_net[(ref, pin)] = (netid[name], name)
    return netid, node_net


def build_pcb(generator, title, paper, comments, nl: Netlist, placements: dict, lib: FpLib, uid: Ids,
              holes=(), extra="", four_layers=True, hole_fp=None, skip_refs=()):
    """placements: ref -> dict(x, y, rot, hide=False, flip=False).  Returns the board text."""
    netid, node_net = assign_nets(nl)
    o = [pcb_header(generator, title, paper, comments, four_layers), '\t(net 0 "")\n']
    for name, i in netid.items():
        o.append(f'\t(net {i} "{name}")\n')
    missing = set(nl.parts) - set(placements) - set(skip_refs)
    assert not missing, f"unplaced parts: {sorted(missing)[:10]}"
    for ref in sorted(placements):
        p = placements[ref]
        part = nl.parts[ref]
        o.append(place(lib, uid, part["footprint"], ref, part["value"], p["x"], p["y"], p.get("rot", 0),
                       {pin: node_net[(r, pin)] for (r, pin) in node_net if r == ref},
                       hide_ref=p.get("hide", False), flip=p.get("flip", False)))
    for ref, x, y, d in holes:
        o.append(npth_hole(uid, ref, x, y, d))
    o.append(extra)
    o.append(")\n")
    return "".join(o)


# ----------------------------------------------------------------------------- checks on a placed board
def pcb_pad_nets(pcb: Path, work: Path) -> dict[tuple[str, str], str]:
    out = work / (pcb.stem + ".d356")
    run_cli("pcb", "export", "ipcd356", "-o", str(out), str(pcb))
    res = {}
    for ln in out.read_text(encoding="utf8", errors="replace").splitlines():
        m = re.match(r"^3[12]7(\S+)\s+([^\s-]+)\s*-(\S+)\s+[DA]\d", ln)
        if m:
            res[(m[2], m[3])] = m[1]
    return res


def board_footprints(txt: str) -> dict[str, str]:
    return {r: fp for fp, r in re.findall(r'\(footprint "([^"]+)".*?\(property "Reference" "([^"]+)"', txt, re.S)}


def _unconn(net: str) -> bool:
    """IPC-D-356 shortens names to 14 characters: 'unconnected-(...)' nets show up as a fragment."""
    return net == "N/C" or "(" in net or ")" in net or net.startswith("unconn")


def compare_pcb(nl: Netlist, pads: dict, pcb: Path, allowed_extra=lambda r: False, skip=lambda r: False) -> list[str]:
    errs = []
    by_ref = board_footprints(pcb.read_text(encoding="utf8"))
    for ref, part in nl.parts.items():
        if skip(ref):
            continue
        if ref not in by_ref:
            errs.append(f"{ref}: missing on the board")
        elif by_ref[ref] != part["footprint"]:
            errs.append(f"{ref}: footprint {by_ref[ref]} != schematic {part['footprint']}")
    extra = {r for r in by_ref if r not in nl.parts and not allowed_extra(r)}
    if extra:
        errs.append(f"unexpected footprints: {sorted(extra)[:10]}")
    groups = defaultdict(set)
    for (ref, pin), net in pads.items():
        if allowed_extra(ref) or _unconn(net):
            continue
        groups[net].add((ref, pin))
    for name, nodes in nl.nets.items():
        nodes = set(nodes)
        if len(nodes) == 1:
            continue
        got = groups.get(name)
        if got != nodes:
            errs.append(f"net {name}: board {len(got or [])} pads vs schematic {len(nodes)}")
    for name in set(groups) - set(nl.nets):
        errs.append(f"net {name} on the board is not in the schematic")
    for name, nodes in nl.nets.items():
        if len(nodes) == 1:
            nd = next(iter(nodes))
            n = pads.get(nd, "N/C")
            if not _unconn(n) and n != name:
                errs.append(f"single pad {nd} carries net {n}")
    return errs


def run_drc(pcb: Path, work: Path) -> dict:
    out = work / (pcb.stem + "_drc.json")
    run_cli("pcb", "drc", "--severity-all", "--format", "json", "-o", str(out), str(pcb))
    d = json.loads(out.read_text(encoding="utf8"))
    return {"violations": d.get("violations", []), "unconnected": d.get("unconnected_items", []),
            "parity": d.get("schematic_parity", [])}


def ratsnest_count(pcb: Path) -> int | None:
    py = kicad_python()
    if not py:
        return None
    r = subprocess.run([py, "-c", "import pcbnew,sys;b=pcbnew.LoadBoard(sys.argv[1]);"
                        "print(b.GetConnectivity().GetUnconnectedCount(False))", str(pcb)],
                       capture_output=True, text=True)
    return int(r.stdout.split()[-1])


def swap_two_pad_nets(text: str, ref: str, pad_a: str, pad_b: str) -> str:
    """Negative-test helper: exchange the nets of two pads of one footprint (same ref) in a board text."""
    i = text.index(f'(property "Reference" "{ref}"')
    start = text.rfind('(footprint "', 0, i)
    end = block_end(text, start)
    fp = text[start:end]
    pat = lambda p: re.compile(r'(\(pad "%s"[^\n]*\n(?:[^\n]*\n)*?\t\t\t\(net )(\d+ "[^"]*")' % re.escape(p))
    ma, mb = pat(pad_a).search(fp), pat(pad_b).search(fp)
    assert ma and mb, (ref, pad_a, pad_b)
    (a1, b1), (a2, b2) = ma.span(2), mb.span(2)
    fp2 = fp[:a1] + fp[a2:b2] + fp[b1:a2] + fp[a1:b1] + fp[b2:]
    return text[:start] + fp2 + text[end:]
