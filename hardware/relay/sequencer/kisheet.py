"""Sheet emitter for the KiCad 10 schematics of the sequencer board (as the unit tester / control board generators).

Stdlib only.  Library symbols are copied from the installed KiCad libraries (env KICAD_SHARE overrides).
"""
from __future__ import annotations

import uuid

from kilib import LABEL_ANGLE, OUT, Sym, rot

PROJECT = "sequencer"
NS = uuid.UUID("7d2f4a52-3c41-4d6f-8f07-5b6c1d0c7a21")
S_FLAG = Sym("power", "PWR_FLAG")
_FLG = 0


def g(v: float) -> float:
    return round(round(v / 2.54) * 2.54, 2)


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def G(name: str) -> str:
    return "@" + name


# ----------------------------------------------------------------------------- sheet emitter (as the unit tester)
class Sheet:
    def __init__(self, key: str, title: str, comments=(), paper="A1"):
        self.key = key
        self.uuid = uid("file", key)
        self.title, self.paper, self.comments = title, paper, list(comments)
        self.libs: dict[str, Sym] = {}
        self.items: list[str] = []
        self.n = 0
        self.path = ""

    def _u(self, kind):
        self.n += 1
        return uid(self.uuid, kind, self.n)

    def wire(self, x1, y1, x2, y2):
        self.items.append(
            f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self._u("w")}")\n\t)')

    def label(self, name, x, y, angle, is_global=False):
        just = "right" if angle in (180, 270) else "left"
        if is_global:
            self.items.append(
                f'\t(global_label "{name}"\n\t\t(shape passive)\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n'
                f'\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                f'\t\t(uuid "{self._u("gl")}")\n\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
                f'\t\t\t(at {f(x)} {f(y)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n'
                f'\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n\t)')
        else:
            self.items.append(
                f'\t(label "{name}"\n\t\t(at {f(x)} {f(y)} {angle})\n\t\t(effects\n\t\t\t(font\n'
                f'\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just} bottom)\n\t\t)\n'
                f'\t\t(uuid "{self._u("l")}")\n\t)')

    def noconnect(self, x, y):
        self.items.append(f'\t(no_connect\n\t\t(at {f(x)} {f(y)})\n\t\t(uuid "{self._u("nc")}")\n\t)')

    def text(self, s, x, y, size=1.8):
        s = s.replace('"', "'").replace("\n", "\\n")
        self.items.append(
            f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t)\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{self._u("t")}")\n\t)')

    def net_at(self, x, y, out, name, length=5.08):
        is_global = name.startswith("@")
        name = name.lstrip("@")
        dx, dy = out
        ex, ey = x + dx * length, y - dy * length
        self.wire(x, y, ex, ey)
        self.label(name, ex, ey, LABEL_ANGLE[out], is_global)

    def place(self, sym: Sym, x, y, ref, value, fp, nets=None, unit=1, rotation=0, nc=(), dnp=False,
              desc="", ds="", in_bom=True, on_board=True, show_value=True, stub=5.08):
        nets = dict(nets or {})
        x, y = g(x), g(y)
        self.libs[sym.lib_id] = sym
        pins = sym.pins(unit)
        ox, oy = sym.fields.get("Reference", (0.0, 0.0))
        vx, vy = sym.fields.get("Value", (0.0, 0.0))

        def prop(name, val, pos, hide=False):
            h = "\n\t\t\t(hide yes)" if hide else ""
            return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {f(pos[0])} {f(pos[1])} 0){h}\n\t\t\t(effects\n'
                    f'\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')

        s = (f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(x)} {f(y)} {rotation})\n\t\t(unit {unit})\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom {"yes" if in_bom else "no"})\n'
             f'\t\t(on_board {"yes" if on_board else "no"})\n\t\t(dnp {"yes" if dnp else "no"})\n'
             f'\t\t(uuid "{self._u("s")}")\n')
        s += prop("Reference", ref, (x + ox, y - oy))
        s += prop("Value", value, (x + vx, y - vy), hide=not show_value)
        s += prop("Footprint", fp, (x, y), hide=True)
        s += prop("Datasheet", ds, (x, y), hide=True)
        s += prop("Description", desc, (x, y), hide=True)
        for num in pins:
            s += f'\t\t(pin "{num}"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
        s += (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.path}"\n'
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)
        seen: dict[tuple, str] = {}
        nets = {str(k): v for k, v in nets.items()}
        nc = {str(k) for k in nc}
        for num, (px, py, a, pname, ptype) in pins.items():
            rx, ry = rot(px, py, rotation)
            o = OUT[a]
            o = tuple(int(round(c)) for c in rot(o[0], o[1], rotation))
            sx, sy = round(x + rx, 3), round(y - ry, 3)
            key = (sx, sy)
            if num in nc:
                if key not in seen:
                    self.noconnect(sx, sy)
                    seen[key] = "<nc>"
            elif num in nets:
                if key in seen:
                    if seen[key] != nets[num]:
                        raise SystemExit(f"{ref}.{num}: stacked pins with different nets")
                    continue
                seen[key] = nets[num]
                self.net_at(sx, sy, o, nets[num], stub)
            else:
                raise SystemExit(f"{ref} unit {unit}: pin {num} ({pname}) has no net / nc")
        extra = set(nets) - set(pins)
        if extra:
            raise SystemExit(f"{ref}: nets given for unknown pins {extra}")

    def flag(self, net, x, y):
        x, y = g(x), g(y)
        self.label(net, x, y, 0, True)
        self.libs[S_FLAG.lib_id] = S_FLAG
        global _FLG
        _FLG += 1
        ref = f"#FLG{_FLG:04d}"
        s = (f'\t(symbol\n\t\t(lib_id "{S_FLAG.lib_id}")\n\t\t(at {f(x)} {f(y)} 0)\n\t\t(unit 1)\n'
             f'\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board no)\n\t\t(dnp no)\n'
             f'\t\t(uuid "{self._u("s")}")\n'
             f'\t\t(property "Reference" "{ref}"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
             f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
             f'\t\t(property "Value" "PWR_FLAG"\n\t\t\t(at {f(x)} {f(y + 2.54)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
             f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
             f'\t\t(pin "1"\n\t\t\t(uuid "{self._u("p")}")\n\t\t)\n'
             f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.path}"\n'
             f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')
        self.items.append(s)

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



def sheet_block(sheet_uuid, root_uuid, name, fname, x, y, w, h, page) -> str:
    return (f'\t(sheet\n\t\t(at {f(x)} {f(y)})\n\t\t(size {f(w)} {f(h)})\n\t\t(exclude_from_sim no)\n'
            f'\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(stroke\n\t\t\t(width 0.1524)\n'
            f'\t\t\t(type solid)\n\t\t)\n\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n\t\t(uuid "{sheet_uuid}")\n'
            f'\t\t(property "Sheetname" "{name}"\n\t\t\t(at {f(x)} {f(y - 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t(thickness 0.254)\n\t\t\t\t\t(bold yes)\n\t\t\t\t)\n'
            f'\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
            f'\t\t(property "Sheetfile" "{fname}"\n\t\t\t(at {f(x)} {f(y + h + 0.7)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n'
            f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "/{root_uuid}"\n\t\t\t\t\t(page "{page}")\n'
            f'\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')

