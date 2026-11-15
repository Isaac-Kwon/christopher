#!/usr/bin/env python3
"""Check the placed shifter PCB against the schematic.

    python verify_shifter_pcb.py [--build-dir DIR]

  1. exports the schematic netlist (kicad-cli) and the board's IPC-D-356 netlist;
  2. every schematic part is on the board with the schematic's footprint, nothing else (the mounting holes H1..H4 are schematic parts, net CHASSIS; D-sub shell pads SH
     are checked against CHASSIS);
  3. the nets on the pads are exactly the schematic nets (same pad partition; single-pad nets
     carry no net or their own 'unconnected' net);
  4. kicad-cli DRC: 0 violations; the unconnected items are exactly the ratsnest edges of the
     schematic nets (sum of pads-1);
  5. negative test: swapping the nets of two pads in a copy of the board is detected.
  6. both boards are checked: the standard one (shifter_board.kicad_pcb, S1-S3) and the S4 variant
     (shifter_board_s4.kicad_pcb: same netlist, J1-J4 mirrored on B.Cu).  The S4 variant must have exactly
     the same pad nets as the standard board, the same outline and holes, J5 and all other parts at the same
     poses, J1-J4 on the bottom side with pin 1 at the opposite end of each connector, the "S4 / a4 - FRONT"
     marking; both boards must be reproduced byte for byte by the generator (determinism).
Exit status 0 = all good.  Default build directory: %LOCALAPPDATA%/christopher/shifter-board
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_shifter as vs  # noqa: E402
import gen_shifter_pcb as gen  # noqa: E402

PCB = HERE / "shifter_board.kicad_pcb"
PCB_S4 = HERE / "shifter_board_s4.kicad_pcb"
CLI = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"


def run(*a):
    return subprocess.run([CLI, *a], check=True, capture_output=True, text=True)


def pcb_nets(pcb: Path, work: Path) -> dict[tuple[str, str], str]:
    out = work / "board.d356"
    run("pcb", "export", "ipcd356", "-o", str(out), str(pcb))
    res = {}
    for ln in out.read_text(encoding="utf8", errors="replace").splitlines():
        m = re.match(r"^3[12]7(\S+)\s+(\S+)\s+-(\S+)\s+[DA]\d", ln)
        if m:
            k = (m[2], m[3])
            res[k] = m[1] if res.get(k, m[1]) == m[1] else "CONFLICT"   # same pad name twice (SH) on different nets
    return res


def compare(nl: "vs.Netlist", pads: dict, pcb: Path) -> list[str]:
    errs = []
    txt = pcb.read_text(encoding="utf8")
    fps = dict(re.findall(r'\(footprint "([^"]+)"[^\n]*\n(?:\t\t[^\n]*\n)*?\t\t\t?\(property "Reference" "([^"]+)"', txt))
    by_ref = {r: f for f, r in fps.items()} if False else {r: f for f, r in re.findall(
        r'\(footprint "([^"]+)".*?\(property "Reference" "([^"]+)"', txt, re.S)}
    for ref, part in nl.parts.items():
        if ref not in by_ref:
            errs.append(f"{ref}: missing on the board")
        elif by_ref[ref] != part["footprint"]:
            errs.append(f"{ref}: footprint {by_ref[ref]} != schematic {part['footprint']}")
    extra = set(by_ref) - set(nl.parts)
    if extra:
        errs.append(f"unexpected footprints: {sorted(extra)}")
    # pad partition
    groups = defaultdict(set)
    for (ref, pin), net in pads.items():
        if net == "N/C" or ("(" in net):
            continue
        groups[net].add((ref, pin))
    # D-sub shell pads ("SH", not schematic pins) must be on CHASSIS (0003 7.1)
    shells = {(r, "SH") for r in nl.parts if re.fullmatch(r"J\d", r)}
    for name, nodes in nl.nets.items():
        nodes = set(nodes) | (shells if name.endswith("CHASSIS") else set())
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
            if not (n == "N/C" or ("(" in n)):
                errs.append(f"single pad {nd} carries net {n}")
    return errs


def board_parts(text: str) -> dict:
    """ref -> dict(layer, at=(x, y, rot), pad1, pad19 = stored local pad positions) for every footprint."""
    out = {}
    for m in re.finditer(r'\n\t\(footprint "([^"]+)"', text):
        s = m.start() + 1
        blk = text[s:gen.block_end(text, s)]
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)[1]
        at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', blk)
        d = dict(layer=re.search(r'\(layer "([^"]+)"\)', blk)[1], at=(float(at[1]), float(at[2]), float(at[3] or 0)))
        for pin in ("1", "19"):
            pm = re.search(r'\(pad "%s"[^\n]*\n\t+\(at ([-\d.]+) ([-\d.]+)' % pin, blk)
            if pm:
                d["pad" + pin] = (float(pm[1]), float(pm[2]))
        out[ref] = d
    return out


def pin1_end(d: dict) -> str:
    """'L' if pin 1 of a D-sub is at the smaller board y than pin 19 (pad positions are stored footprint-local;
    the footprint rotation is applied here, KiCad convention: y down, positive angle counter-clockwise on screen)."""
    x0, y0, th = d["at"]
    c, s = math.cos(math.radians(th)), math.sin(math.radians(th))

    def y_of(p):
        return y0 + (-p[0] * s + p[1] * c)
    return "L" if y_of(d["pad19"]) > y_of(d["pad1"]) else "R"


S4_TEXT_MARK = "S4 / a4 - FRONT"


def outline_of(text: str):
    return re.findall(r'\(gr_line\n\t\t\(start ([-\d.]+) ([-\d.]+)\)\n\t\t\(end ([-\d.]+) ([-\d.]+)\)[^\n]*\n(?:[^\n]*\n)*?\t\t\(layer "Edge.Cuts"\)', text)


def variant_checks(std_text: str, s4_text: str) -> list[str]:
    """The S4 variant against the standard board: only J1-J4 differ (bottom side, pin 1 at the other end)."""
    errs = []
    a, b = board_parts(std_text), board_parts(s4_text)
    if set(a) != set(b):
        errs.append(f"parts differ: {sorted(set(a) ^ set(b))}")
    for ref in sorted(set(a) & set(b)):
        if ref in ("J1", "J2", "J3", "J4"):
            if b[ref]["layer"] != "B.Cu" or a[ref]["layer"] != "F.Cu":
                errs.append(f"{ref}: layers std {a[ref]['layer']} / s4 {b[ref]['layer']} (want F.Cu / B.Cu)")
            if pin1_end(a[ref]) == pin1_end(b[ref]):
                errs.append(f"{ref}: pin 1 end is the same on both boards")
            if abs(a[ref]["at"][0] - b[ref]["at"][0]) > 1e-6:
                errs.append(f"{ref}: edge differs")
        elif a[ref]["at"] != b[ref]["at"] or a[ref]["layer"] != b[ref]["layer"]:
            errs.append(f"{ref}: moved or re-sided on the variant ({a[ref]['at']} -> {b[ref]['at']})")
    for ref in ("J1", "J2", "J3", "J4"):        # the pin-row centre (49.86 mm row) of each connector is kept
        if ref in a and ref in b:
            ca = a[ref]["at"][1] + (24.93 if pin1_end(a[ref]) == "L" else -24.93)
            cb = b[ref]["at"][1] + (24.93 if pin1_end(b[ref]) == "L" else -24.93)
            if abs(ca - cb) > 0.02:    # the generator offsets 10.28 / 60.13 differ by 49.85, the row is 49.86
                errs.append(f"{ref}: pin-row centre moved {ca:.2f} -> {cb:.2f}")
    if S4_TEXT_MARK not in s4_text or S4_TEXT_MARK in std_text:
        errs.append("the 'S4 / a4 - FRONT' marking is missing on the variant or present on the standard board")
    if outline_of(std_text) != outline_of(s4_text):
        errs.append("outline differs")
    return errs


def check_board(label: str, pcb: Path, nl: "vs.Netlist", work: Path) -> list[str]:
    pads = pcb_nets(pcb, work)
    errs = compare(nl, pads, pcb)
    print(f"[{label}] parts: {len(nl.parts)} schematic, nets: {len(nl.nets)}; pads read from the board: {len(pads)}")
    print(f"[{label}] placement/net comparison:", "OK" if not errs else f"{len(errs)} problems")
    for e in errs[:20]:
        print("  ", e)

    run("pcb", "drc", "--severity-all", "--format", "json", "-o", str(work / "drc.json"), str(pcb))
    d = json.loads((work / "drc.json").read_text(encoding="utf8"))
    viol, unc = d.get("violations", []), d.get("unconnected_items", [])
    n_shell = 2 * sum(1 for r in nl.parts if re.fullmatch(r"J\d", r))   # SH pads: 2 per D-sub, on CHASSIS
    expect = sum(len(n) - 1 for n in nl.nets.values()) + n_shell
    print(f"[{label}] DRC: {len(viol)} violations, {len(d.get('schematic_parity', []))} parity items; "
          f"{len(unc)} unconnected items listed (the report is capped at about 500)")
    for v in viol[:10]:
        print("  ", v["type"], v["description"])
    if viol:
        errs.append("DRC violations")
    py = os.environ.get("KICAD_PYTHON", str(Path(CLI).parent / "python.exe"))
    if Path(py).exists():
        r = subprocess.run([py, "-c", "import pcbnew,sys;b=pcbnew.LoadBoard(sys.argv[1]);"
                            "print(b.GetConnectivity().GetUnconnectedCount(False))", str(pcb)],
                           capture_output=True, text=True)
        n = int(r.stdout.split()[-1])
        print(f"[{label}] ratsnest (pcbnew connectivity): {n} unconnected edges, expected {expect} = sum(pads-1) over schematic nets + {n_shell} D-sub shell pads on CHASSIS")
        if n != expect:
            errs.append("ratsnest edge count mismatch")
    else:
        print("KiCad python not found: ratsnest count not checked")

    # negative test: swap two pad nets
    t = pcb.read_text(encoding="utf8")
    pat = r'(\(pad "4"[^)]*\)\s*\(net )(\d+ "%s")'
    m1 = re.search(pat % "/OIN_A", t)
    m2 = re.search(pat % "/OIN_B", t)
    bad = t
    if m1 and m2:
        (a1, b1), (a2, b2) = (m1.span(2), m2.span(2))
        bad = t[:a1] + t[a2:b2] + t[b1:a2] + t[a1:b1] + t[b2:]
    neg = work / f"negative_{label}.kicad_pcb"
    neg.write_text(bad, encoding="utf8", newline="\n")
    nerr = compare(nl, pcb_nets(neg, work), neg)
    print(f"[{label}] negative test (two pad nets swapped):", "detected" if nerr and bad != t else "NOT DETECTED")
    if not nerr or bad == t:
        errs.append("negative test failed")
    neg.unlink()

    # negative test 2 (0003 S2): a D-sub shell pad moved from CHASSIS to 0 V must be detected
    gid = re.search(r'\(net (\d+) "/GND"\)', t).group(1)
    bad2 = re.sub(r'(\(pad "SH".*?\(net )\d+ "/CHASSIS"', lambda m: m.group(1) + f'{gid} "/GND"', t, count=1, flags=re.S)
    neg.write_text(bad2, encoding="utf8", newline="\n")
    nerr = compare(nl, pcb_nets(neg, work), neg)
    print(f"[{label}] negative test (D-sub shell pad on 0 V instead of CHASSIS):", "detected" if nerr and bad2 != t else "NOT DETECTED")
    if not nerr or bad2 == t:
        errs.append("negative test 2 failed")
    neg.unlink()
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default=str(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "christopher" / "shifter-board"))
    a = ap.parse_args()
    work = Path(a.build_dir)
    work.mkdir(parents=True, exist_ok=True)
    run("sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(work / "sch.net"), str(HERE / "shifter_board.kicad_sch"))
    nl = vs.Netlist((work / "sch.net").read_text(encoding="utf8"))
    errs = []
    for label, pcb in (("std", PCB), ("s4", PCB_S4)):
        errs += check_board(label, pcb, nl, work)
        # determinism: the generator reproduces the file byte for byte
        text, _ = gen.build(nl, label)
        same = text == pcb.read_text(encoding="utf8")
        print(f"[{label}] regenerated board identical to the file: {'OK' if same else 'DIFFERS'}")
        if not same:
            errs.append(f"{label}: board is not reproducible")
    ve = variant_checks(PCB.read_text(encoding="utf8"), PCB_S4.read_text(encoding="utf8"))
    s4p, stp = pcb_nets(PCB_S4, work), pcb_nets(PCB, work)
    if s4p != stp:
        ve.append("pad nets of the S4 variant differ from the standard board")
    print("S4 variant vs standard board (same pad nets, only J1-J4 mirrored on B.Cu, outline, marking):",
          "OK" if not ve else f"{len(ve)} problems")
    for e in ve[:10]:
        print("  ", e)
    errs += ve
    print("RESULT:", "PASS" if not errs else "FAIL")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
