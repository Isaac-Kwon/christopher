#!/usr/bin/env python3
"""Verify generated drum boards.  Standard library only (+ kicad-cli for --drc/--fab/--render).

Checks, independently of gen_drum.py (it only shares the wiring file):
  1. geometric connectivity: copper items (pads, vias, tracks, arcs) are merged by physical contact
     (track end on pad / via / other track end, same layer) into electrical islands;
  2. every island contains exactly the two pads the wiring table demands
       copper A: ring4[i] <-> ring3[W[i]]     copper B: ring1[i] <-> ring2[W[i]]
     and there are exactly 52 islands (no pad shared, none floating, no extra copper islands);
  3. net labels agree with the islands; pad positions/angles match the documented convention;
  4. optionally: kicad-cli DRC (must be 0 violations, 0 unconnected), IPC-D-356 net cross-check,
     Gerber + drill export, rendering.

Usage:  python verify_drum.py [--drc] [--ipc] [--fab] [--render ROTOR] [--build-dir DIR] [boards...]
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WIRING_FILE = REPO / "data" / "wirings" / "enigma.txt"
N = 26
RING_R = {1: 22.0, 2: 29.0, 3: 36.0, 4: 43.0}
COPY_RINGS = [(4, 3), (1, 2)]          # (entry ring, reflector ring)


# ------------------------------------------------------------------ s-expression parser
def tokenize(text):
    tok = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
    for m in tok.finditer(text):
        yield m.group(0)


def parse(text):
    stack = [[]]
    for t in tokenize(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            x = stack.pop()
            stack[-1].append(x)
        elif t.startswith('"'):
            stack[-1].append(("s", t[1:-1]))
        else:
            stack[-1].append(t)
    return stack[0][0]


def sval(x):
    return x[1] if isinstance(x, tuple) else x


def find(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def first(node, name):
    r = find(node, name)
    return r[0] if r else None


def fnum(x):
    return float(sval(x))


# ------------------------------------------------------------------ wiring
def load_wiring():
    d = {}
    for line in WIRING_FILE.read_text(encoding="utf-8").splitlines():
        f = line.split()
        if f and f[0] == "rotor":
            d[f[1]] = f[2]
    return d


# ------------------------------------------------------------------ union-find
class UF:
    def __init__(self):
        self.p = {}

    def add(self, x):
        self.p.setdefault(x, x)

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.add(a)
        self.add(b)
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def in_pad(pt, pad, tol=1e-6):
    dx, dy = pt[0] - pad["x"], pt[1] - pad["y"]
    a = math.radians(pad["rot"])        # KiCad: positive = counter-clockwise on screen (y down)
    c, s = math.cos(a), math.sin(a)
    lx = dx * c - dy * s                # rotate point by -(-a): undo the pad rotation
    ly = dx * s + dy * c
    return abs(lx) <= pad["w"] / 2 + tol and abs(ly) <= pad["h"] / 2 + tol


# ------------------------------------------------------------------ board check
def check_board(path, wiring, rotor):
    errors = []
    root = parse(Path(path).read_text(encoding="utf-8"))
    netname = {}
    for n in find(root, "net"):
        netname[int(n[1])] = sval(n[2])

    pads, vias, tracks = [], [], []
    for fp in find(root, "footprint"):
        at = [float(v) for v in first(fp, "at")[1:3]]
        for p in find(fp, "pad"):
            if sval(p[2]) != "smd":
                continue
            pat = first(p, "at")
            size = first(p, "size")
            net = first(p, "net")
            pads.append(dict(name=sval(p[1]), x=at[0] + fnum(pat[1]), y=at[1] + fnum(pat[2]),
                             rot=fnum(pat[3]) if len(pat) > 3 else 0.0,
                             w=fnum(size[1]), h=fnum(size[2]), net=int(net[1]),
                             netname=sval(net[2]), layers=[sval(x) for x in first(p, "layers")[1:]]))
    for v in find(root, "via"):
        at = first(v, "at")
        vias.append(dict(x=fnum(at[1]), y=fnum(at[2]), d=fnum(first(v, "size")[1]),
                         net=int(first(v, "net")[1])))
    for kind in ("segment", "arc"):
        for t in find(root, kind):
            st, en = first(t, "start"), first(t, "end")
            tracks.append(dict(kind=kind, a=(fnum(st[1]), fnum(st[2])), b=(fnum(en[1]), fnum(en[2])),
                               layer=sval(first(t, "layer")[1]), net=int(first(t, "net")[1])))

    uf = UF()
    PT = 5e-3
    # via islands (all layers)
    for i, v in enumerate(vias):
        for L in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
            uf.union(("via", i), ("n", i, L))
    for pi, p in enumerate(pads):
        uf.add(("pad", pi))
    # track end attachments
    endpoints = []                      # (layer, point, node)
    for ti, t in enumerate(tracks):
        uf.union(("trk", ti, 0), ("trk", ti, 1))     # a track (segment/arc) is one conductor
        for e, pt in enumerate((t["a"], t["b"])):
            endpoints.append((t["layer"], pt, ("trk", ti, e)))
            for vi, v in enumerate(vias):
                if math.hypot(pt[0] - v["x"], pt[1] - v["y"]) <= v["d"] / 2 + PT:
                    uf.union(("trk", ti, e), ("via", vi))
            if t["layer"] == "F.Cu":
                for pi, p in enumerate(pads):
                    if in_pad(pt, p):
                        uf.union(("trk", ti, e), ("pad", pi))
    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            la, pa, na = endpoints[i]
            lb, pb, nb = endpoints[j]
            if la == lb and math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= PT:
                uf.union(na, nb)
    # via touching a pad directly (should never happen; would be a hidden short)
    for vi, v in enumerate(vias):
        for pi, p in enumerate(pads):
            if in_pad((v["x"], v["y"]), p):
                uf.union(("via", vi), ("pad", pi))

    islands = {}
    for pi, p in enumerate(pads):
        islands.setdefault(uf.find(("pad", pi)), []).append(p["name"])
    # copper items not attached to any pad island
    pad_roots = set(islands)
    for key in list(uf.p):
        if key[0] in ("trk", "via") and uf.find(key) not in pad_roots:
            errors.append(f"floating copper item {key[:2]}")
            break

    got = sorted(tuple(sorted(v)) for v in islands.values())
    exp = []
    w = wiring[rotor]
    for entry, refl in COPY_RINGS:
        for i in range(N):
            j = ord(w[i]) - 65
            exp.append(tuple(sorted((f"{entry}{chr(65 + i)}", f"{refl}{chr(65 + j)}"))))
    exp.sort()
    if len(pads) != 104:
        errors.append(f"expected 104 pads, found {len(pads)}")
    if got != exp:
        gs, es = set(got), set(exp)
        errors.append(f"connectivity mismatch: missing {sorted(es - gs)[:5]} unexpected {sorted(gs - es)[:5]}")

    # net labels must match islands
    for root_id, names in islands.items():
        labels = {p["net"] for p in pads if p["name"] in names}
        for key in uf.p:
            if key[0] == "trk" and uf.find(key) == root_id:
                labels.add(tracks[key[1]]["net"])
            if key[0] == "via" and uf.find(key) == root_id:
                labels.add(vias[key[1]]["net"])
        if len(labels) != 1:
            errors.append(f"net labels disagree on island {names}: {sorted(labels)}")
        else:
            nm = netname[labels.pop()]
            e = sorted(names)
            exp_name = None
            for entry, refl in COPY_RINGS:
                a = [x for x in e if x[0] == str(entry)]
                b = [x for x in e if x[0] == str(refl)]
                if a and b:
                    exp_name = f"{a[0]}-{b[0]}"
            if nm != exp_name:
                errors.append(f"net name {nm} != expected {exp_name}")

    # pad geometry convention: ring radius, angle clockwise from top, letter A at top
    for p in pads:
        ring, letter = int(p["name"][0]), ord(p["name"][1]) - 65
        dx, dy = p["x"] - 150.0, p["y"] - 100.0
        r = math.hypot(dx, dy)
        ang = math.degrees(math.atan2(dx, -dy)) % 360
        if abs(r - RING_R[ring]) > 1e-4 or abs((ang - letter * 360 / N + 180) % 360 - 180) > 1e-3:
            errors.append(f"pad {p['name']} at r={r:.3f} ang={ang:.3f} violates convention")
            break
    return errors, len(islands)


# ------------------------------------------------------------------ kicad-cli helpers
def find_kicad_cli():
    for c in (os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"), shutil.which("kicad-cli.exe")):
        if c and Path(c).exists():
            return c
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), "/usr", "/opt"):
        if not base:
            continue
        for g in sorted(Path(base).glob("KiCad/*/bin/kicad-cli*"), reverse=True):
            return str(g)
    sys.exit("kicad-cli not found (set KICAD_CLI)")


def default_build_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "christopher" / "drum-pcb"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def drc(cli, board, outdir):
    rep = outdir / f"drc_{board.stem}.json"
    run([cli, "pcb", "drc", "--output", str(rep), "--format", "json", "--severity-all", "--units", "mm", str(board)])
    d = json.loads(rep.read_text(encoding="utf-8"))
    return len(d["violations"]), len(d["unconnected_items"]), len(d.get("schematic_parity", [])), d


def ipc356_check(cli, board, outdir, rotor, wiring):
    f = outdir / f"{board.stem}.d356"
    run([cli, "pcb", "export", "ipcd356", "--output", str(f), str(board)])
    nets = {}
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"327(\S+)\s+DRUM1\s+-(\S+)", line)       # pad records only
        if m:
            nets.setdefault(m.group(1), set()).add(m.group(2))
    w = wiring[rotor]
    exp = set()
    for entry, refl in COPY_RINGS:
        for i in range(N):
            exp.add(tuple(sorted((f"{entry}{chr(65 + i)}", f"{refl}{w[i]}"))))
    got = {tuple(sorted(v)) for v in nets.values() if len(v) == 2}
    return got == exp, len(got)


def fab(cli, board, outdir):
    d = outdir / "fab" / board.stem
    d.mkdir(parents=True, exist_ok=True)
    layers = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,B.SilkS,Edge.Cuts"
    rc1, o1 = run([cli, "pcb", "export", "gerbers", "--layers", layers, "--output", str(d) + os.sep, str(board)])
    rc2, o2 = run([cli, "pcb", "export", "drill", "--output", str(d) + os.sep, "--format", "excellon",
                   "--excellon-separate-th", str(board)])
    return rc1 == 0 and rc2 == 0, sorted(p.name for p in d.iterdir())


def render(cli, board, outdir):
    out = []
    for side in ("top", "bottom"):
        f = outdir / f"{board.stem}_{side}.png"
        rc, o = run([cli, "pcb", "render", "--output", str(f), "--side", side, "--width", "1800", "--height", "1800",
                     "--quality", "basic", "--background", "opaque", str(board)])
        out.append((f, rc, o.strip()[:200]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boards", nargs="*")
    ap.add_argument("--drc", action="store_true")
    ap.add_argument("--ipc", action="store_true")
    ap.add_argument("--fab", action="store_true")
    ap.add_argument("--render", metavar="ROTOR")
    ap.add_argument("--build-dir", default=None)
    a = ap.parse_args()
    boards = [Path(b) for b in a.boards] or sorted((HERE / "boards").glob("drum_*.kicad_pcb"))
    wiring = load_wiring()
    outdir = Path(a.build_dir) if a.build_dir else default_build_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    cli = find_kicad_cli() if (a.drc or a.ipc or a.fab or a.render) else None
    ok = True
    for b in boards:
        rotor = b.stem.replace("drum_", "")
        errs, n_isl = check_board(b, wiring, rotor)
        line = f"{b.name}: connectivity {'OK' if not errs else 'FAIL'} ({n_isl} islands)"
        if errs:
            ok = False
            line += " :: " + "; ".join(errs)
        if a.drc:
            v, u, par, _ = drc(cli, b, outdir)
            line += f" | DRC violations={v} unconnected={u}"
            ok &= (v == 0 and u == 0)
        if a.ipc:
            same, cnt = ipc356_check(cli, b, outdir, rotor, wiring)
            line += f" | IPC-D-356 nets {'OK' if same else 'MISMATCH'} ({cnt})"
            ok &= same
        if a.fab:
            good, files = fab(cli, b, outdir)
            line += f" | fab export {'OK' if good else 'FAIL'} ({len(files)} files)"
            ok &= good
        print(line)
    if a.render:
        for f, rc, o in render(cli, HERE / "boards" / f"drum_{a.render}.kicad_pcb", outdir):
            print(f"render {f.name}: rc={rc} {o}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
