#!/usr/bin/env python3
"""Verify the relay shifter board by simulating its exported netlist.

    python verify_shifter.py                 # export the netlist with kicad-cli, then verify
    python verify_shifter.py --netlist X.net # verify an existing KiCad s-expression netlist

The simulation only uses the netlist (net membership of component pins):
  * which relays a control value energises = the relays whose coil pin 1 is on the J5 control net;
  * a relay closes COM-NC (de-energised) or COM-NO (energised) per pole (G6S-2: pole 1 = pins
    4/3/5, pole 2 = pins 9/10/8; coil = pins 1 (+) and 12 (-));
  * connectivity = union-find over nets + closed contacts.
For every control value a = 0..25 it checks
  outbound  J1 pin i -> exactly J3 pin (i + a) mod 26     (relay.rs shift_binary(i, a, false))
  return    J2 pin i -> exactly J4 pin (i - a) mod 26     (relay.rs shift_binary(i, a, true))
  reversed mounting (signal entering at the OUT connectors): outbound J3 -> J1 (j - a),
  return J4 -> J2 (j + a)                                  (shift_binary with reverse flipped)
and that nothing else is connected (no sneak paths, no shorts to other connectors or control pins).
Negative tests mutate a copy of the netlist and require the verification to fail.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = 26
BITS = 5
STAGE_REFS = {b: f"K{b + 1}" for b in range(BITS)}  # K1xx .. K5xx

# G6S-2 pin functions (see gen_shifter.py)
COIL_P, COIL_N = "1", "12"
POLES = [("4", "3", "5"), ("9", "10", "8")]  # (COM, NC, NO) for outbound / return


def shift_binary(c: int, control: int, reverse: bool) -> int:
    """Mirror of crates/christopher-core/src/relay.rs shift_binary."""
    x = c
    for i in range(5):
        if control >> i & 1:
            step = 1 << i
            x = (x - step) % N if reverse else (x + step) % N
    return x


# ----------------------------------------------------------------------------- netlist parsing
def tokenize(text: str):
    for m in re.finditer(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+', text):
        yield m.group(0) if m.group(1) is None else ("s", m.group(1))


def parse(text: str):
    stack: list[list] = [[]]
    for tok in tokenize(text):
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
    def __init__(self, text: str):
        tree = parse(text)
        self.parts = {}
        comps = find1(tree, "components")
        for c in find_all(comps, "comp"):
            ref = find1(c, "ref")[1]
            self.parts[ref] = dict(
                value=(find1(c, "value") or [0, ""])[1],
                footprint=(find1(c, "footprint") or [0, ""])[1],
            )
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for n in find_all(find1(tree, "nets"), "net"):
            name = find1(n, "name")[1]
            self.nets[name] = [(find1(x, "ref")[1], find1(x, "pin")[1]) for x in find_all(n, "node")]


# ----------------------------------------------------------------------------- simulation
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


def check(nl: Netlist, quiet=False) -> list[str]:
    """Return a list of failure messages (empty = board verified)."""
    errs: list[str] = []
    log = (lambda *a: None) if quiet else print

    relays = {r: p for r, p in nl.parts.items() if re.fullmatch(r"K[1-5]\d\d", r)}
    diodes = [r for r in nl.parts if re.fullmatch(r"D[1-5]\d\d", r)]
    if len(relays) != BITS * N:
        errs.append(f"expected {BITS * N} relays, found {len(relays)}")
    if len(diodes) != BITS * N:
        errs.append(f"expected {BITS * N} flyback diodes, found {len(diodes)}")
    for r, p in relays.items():
        if "G6S-2" not in p["footprint"]:
            errs.append(f"{r}: unexpected footprint {p['footprint']}")

    node_net: dict[tuple[str, str], str] = {}
    for name, nodes in nl.nets.items():
        for nd in nodes:
            node_net[nd] = name

    def net_of(ref, pin):
        return node_net.get((ref, pin))

    # --- coil topology: stage b's coils are exactly the relays with pin 1 on J5 pin b+1's net
    gnd = net_of("J5", "6")
    if gnd is None or net_of("J5", "7") != gnd:
        errs.append("J5 pins 6/7 not on a common 0 V net")
    stage_coils: dict[int, set[str]] = {}
    for b in range(BITS):
        cnet = net_of("J5", str(b + 1))
        coils = {r for r in relays if net_of(r, COIL_P) == cnet} if cnet else set()
        stage_coils[b] = coils
        want = {f"K{b + 1}{i + 1:02d}" for i in range(N)}
        if coils != want:
            errs.append(f"control bit {b}: coils {sorted(coils ^ want)[:6]} differ from the stage-{b + 1} relay set")
    for r in relays:
        if net_of(r, COIL_N) != gnd:
            errs.append(f"{r}: coil pin 12 is not on 0 V")
    for d in diodes:  # K (pin 1) on the stage control net, A (pin 2) on 0 V, same stage as its relay
        b = int(d[1]) - 1
        if net_of(d, "1") != net_of("J5", str(b + 1)) or net_of(d, "2") != gnd:
            errs.append(f"{d}: flyback diode is not across its coil net")

    # --- connector pin lists
    def cpin(ref, i):
        return (ref, str(i + 1))

    sig = {ref: [cpin(ref, i) for i in range(N)] for ref in ("J1", "J2", "J3", "J4")}
    all_sig = {p: ref for ref, ps in sig.items() for p in ps}
    for ref, nm in (("J1", "outbound IN"), ("J2", "return IN"), ("J3", "outbound OUT"), ("J4", "return OUT")):
        for p in sig[ref]:
            if net_of(*p) is None:
                errs.append(f"{ref} pin {p[1]} ({nm}) is unconnected")

    # --- simulate every control value
    for a in range(N):
        uf = UF()
        for nodes in nl.nets.values():
            for nd in nodes[1:]:
                uf.union(nodes[0], nd)
        energised = set()
        for b in range(BITS):
            if a >> b & 1:
                energised |= stage_coils[b]
        for r in relays:
            for com, nc, no in POLES:
                uf.union((r, com), (r, no if r in energised else nc))
        comp: dict = {}
        for p in all_sig:
            comp.setdefault(uf.find(p), []).append(p)

        def reach(p):
            return {q for q in comp.get(uf.find(p), []) if q != p}

        bad = 0
        for i in range(N):
            tests = [
                ("outbound", "J1", "J3", shift_binary(i, a, False)),
                ("return", "J2", "J4", shift_binary(i, a, True)),
                ("outbound reversed", "J3", "J1", shift_binary(i, a, True)),
                ("return reversed", "J4", "J2", shift_binary(i, a, False)),
            ]
            for what, src, dst, want in tests:
                got = reach(cpin(src, i))
                if got != {cpin(dst, want)}:
                    bad += 1
                    gs = sorted(f"{r}.{p}" for r, p in got) or ["(open)"]
                    errs.append(f"a={a:2d} {what} {src}.{i + 1} -> {gs}, expected {dst}.{want + 1}")
        # control pins must stay isolated from the signal pins (checked once, a-independent)
        for b in range(BITS):
            if uf.find(("J5", str(b + 1))) in {uf.find(p) for p in all_sig}:
                errs.append(f"a={a}: control pin J5.{b + 1} is connected to a signal pin")
        log(f"  control {a:2d} ({a:05b}b): {'OK' if not bad else f'{bad} FAILURES'}  "
            f"(energised stages: {[1 << b for b in range(BITS) if a >> b & 1]})")
    return errs


# ----------------------------------------------------------------------------- negative tests
def swap_pins(text: str, ref: str, pa: str, pb: str) -> str:
    """Swap the pin numbers of two node entries of `ref` in the netlist text (moves them between nets)."""
    pat = lambda p: re.compile(r'(\(ref "%s"\)\s*\(pin )"%s"' % (re.escape(ref), re.escape(p)))
    t, na = pat(pa).subn(r'\1"@@A@@"', text)
    t, nb = pat(pb).subn(r'\1"@@B@@"', t)
    assert na == 1 and nb == 1, (ref, pa, pb, na, nb)
    return t.replace('"@@A@@"', f'"{pb}"').replace('"@@B@@"', f'"{pa}"')


NEGATIVE = [
    ("swap two outbound OUT lines (J3 pins 3<->4)", "J3", "3", "4"),
    ("swap two outbound IN lines (J1 pins 1<->2)", "J1", "1", "2"),
    ("swap two return OUT lines (J4 pins 20<->25)", "J4", "20", "25"),
    ("swap NC/NO of stage-3 relay K305 pole 1 (pins 3<->5)", "K305", "3", "5"),
    ("swap control bits CTRL1<->CTRL2 (J5 pins 2<->3)", "J5", "2", "3"),
]


# ----------------------------------------------------------------------------- main
def find_cli() -> str:
    c = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if c:
        return c
    for p in ("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "/usr/bin/kicad-cli"):
        if Path(p).exists():
            return p
    raise SystemExit("kicad-cli not found (set KICAD_CLI)")


def export_netlist(outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "shifter_board.net"
    subprocess.run([find_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out),
                    str(HERE / "shifter_board.kicad_sch")], check=True, stdout=subprocess.DEVNULL)
    return out


def default_build_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "christopher" / "shifter-board"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist", help="existing kicadsexpr netlist (default: export it with kicad-cli)")
    ap.add_argument("--build-dir", help="where the exported netlist goes (default: per-user build dir)")
    args = ap.parse_args()
    net = Path(args.netlist) if args.netlist else export_netlist(Path(args.build_dir) if args.build_dir else default_build_dir())
    text = net.read_text(encoding="utf8")
    nl = Netlist(text)
    print(f"netlist: {len(nl.parts)} components, {len(nl.nets)} nets")
    print("simulating control values 0..25 (outbound, return, and reversed mounting):")
    errs = check(nl)
    if errs:
        print(f"FAIL: {len(errs)} discrepancies, first: {errs[:5]}")
        return 1
    print("PASS: all 26 control values, 26 lines, both halves, both mounting directions match shift_binary")

    ok = True
    print("negative tests (mutated netlist copies must fail):")
    for name, ref, pa, pb in NEGATIVE:
        bad = check(Netlist(swap_pins(text, ref, pa, pb)), quiet=True)
        print(f"  {'detected' if bad else 'MISSED  '}: {name} ({len(bad)} discrepancies)")
        ok &= bool(bad)
    if not ok:
        print("FAIL: a negative test was not detected")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
