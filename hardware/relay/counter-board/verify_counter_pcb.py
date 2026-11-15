#!/usr/bin/env python3
"""Check the placed counter PCBs (counter_common, counter_unit) against their schematics.

    python verify_counter_pcb.py [--build-dir DIR]

For each board:
  1. exports the schematic netlist (kicad-cli) and reads the board's pads and nets with the KiCad
     Python (pcbnew): the IPC-D-356 net-name field is only 14 characters, too short for the
     hierarchical net names, so pcbnew is used;
  2. every schematic part is on the board with the schematic's footprint, nothing else except the
     mounting holes H1..H4, and all footprints are on the front side;
  3. the nets on the pads are exactly the schematic nets (pad by pad), no pad has a net of another
     board and no track/via/zone exists (placement only);
  4. kicad-cli DRC (courtyard, overlap, edge, clearance, hole, ...) reports 0 violations; the
     unconnected items are the ratsnest.  Schematic parity: no missing footprint and no net
     conflict (field-text differences of the symbol are ignored, mounting holes are not in the schematic);
  5. ratsnest edge count (pcbnew connectivity) = sum(pads - 1) over the schematic nets;
  6. negative tests: swapping the nets of two pads / moving a footprint onto another is detected;
  7. determinism: generating twice gives identical files.
Exit status 0 = all good.  Default build directory: %LOCALAPPDATA%/christopher/counter-board
"""
from __future__ import annotations

import argparse
import collections
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_counter as vc  # noqa: E402
import gen_counter_pcb as gp  # noqa: E402

PROJECTS = ("counter_common", "counter_unit")
CLI = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
PY = os.environ.get("KICAD_PYTHON", str(Path(CLI).parent / "python.exe"))

DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
fps, pads = {}, {}
for f in b.GetFootprints():
    ref = f.GetReference()
    fps[ref] = {"fpid": f.GetFPIDAsString(), "front": f.GetLayer() == pcbnew.F_Cu,
                "x": f.GetPosition().x / 1e6, "y": f.GetPosition().y / 1e6}
    for p in f.Pads():
        pads["%s|%s" % (ref, p.GetNumber())] = p.GetNetname()
print(json.dumps({"fps": fps, "pads": pads, "tracks": len(list(b.GetTracks())), "zones": len(list(b.Zones())),
                  "ratsnest": b.GetConnectivity().GetUnconnectedCount(False)}))
"""


def run(*a):
    return subprocess.run([CLI, *a], check=True, capture_output=True, text=True)


def dump(pcb: Path) -> dict:
    r = subprocess.run([PY, "-c", DUMP, str(pcb)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    d["pads"] = {tuple(k.split("|", 1)): v for k, v in d["pads"].items()}
    return d


def sch_nets(nl: "vc.Netlist") -> dict[str, set]:
    nets: dict[str, set] = collections.defaultdict(set)
    for node, name in nl.node_net.items():
        nets[name].add(node)
    return nets


def compare(nl: "vc.Netlist", d: dict) -> list[str]:
    errs = []
    for ref, part in nl.parts.items():
        f = d["fps"].get(ref)
        if f is None:
            errs.append(f"{ref}: missing on the board")
        elif f["fpid"] != part["footprint"]:
            errs.append(f"{ref}: footprint {f['fpid']} != schematic {part['footprint']}")
        elif not f["front"]:
            errs.append(f"{ref}: not on the front side")
    extra = set(d["fps"]) - set(nl.parts) - {f"H{i}" for i in range(1, 5)}
    if extra:
        errs.append(f"unexpected footprints: {sorted(extra)}")
    if sum(1 for r in d["fps"] if re.fullmatch(r"H\d", r)) != 4:
        errs.append("expected exactly 4 mounting holes")
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
        if (ref, pin) not in nl.node_net and not ref.startswith("H") and name:
            errs.append(f"pad {(ref, pin)} carries net {name!r} but is not in the schematic netlist")
    if d["tracks"] or d["zones"]:
        errs.append(f"board contains {d['tracks']} tracks/vias and {d['zones']} zones (placement only expected)")
    return errs


def swap_pad_nets(text: str) -> str | None:
    """Swap the nets of the first relay pad 4 on +24 V and the first relay pad 4 on another net."""
    pat = r'(\(pad "4"[^)]*\)\s*\(net )(\d+ "%s")'
    m1 = re.search(pat % re.escape("/+24V"), text)
    m2 = re.search(r'(\(pad "4"[^)]*\)\s*\(net )(\d+ "(?!/\+24V")[^"]*")', text)
    if not (m1 and m2):
        return None
    (a1, b1), (a2, b2) = m1.span(2), m2.span(2)
    if a1 > a2:
        (a1, b1), (a2, b2) = (a2, b2), (a1, b1)
    return text[:a1] + text[a2:b2] + text[b1:a2] + text[a1:b1] + text[b2:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default=str(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "christopher" / "counter-board"))
    a = ap.parse_args()
    work = Path(a.build_dir)
    work.mkdir(parents=True, exist_ok=True)
    errs_all: list[str] = []

    for project in PROJECTS:
        errs: list[str] = []
        pcb = HERE / f"{project}.kicad_pcb"
        print(f"=== {project} ===")
        run("sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(work / f"{project}.net"),
            str(HERE / f"{project}.kicad_sch"))
        nl = vc.Netlist((work / f"{project}.net").read_text(encoding="utf8"))
        nets = sch_nets(nl)
        d = dump(pcb)
        e = compare(nl, d)
        errs += e
        print(f"parts: {len(nl.parts)} schematic / {len(d['fps'])} on the board (incl. 4 holes), "
              f"pads {len(nl.node_net)}, nets {len(nets)}")
        print("placement / pad-net comparison:", "OK" if not e else f"{len(e)} problems")
        for x in e[:10]:
            print("  ", x)

        # DRC (rules from the .kicad_pro next to the board) + schematic parity
        run("pcb", "drc", "--severity-all", "--format", "json", "--schematic-parity", "-o", str(work / f"{project}.drc.json"), str(pcb))
        dr = json.loads((work / f"{project}.drc.json").read_text(encoding="utf8"))
        viol, unc, par = dr.get("violations", []), dr.get("unconnected_items", []), dr.get("schematic_parity", [])
        bad_par = [p for p in par if p["type"] != "footprint_symbol_field_mismatch"
                   and not (p["type"] == "extra_footprint" and re.search(r"\bH\d\b", json.dumps(p)))]
        print(f"DRC: {len(viol)} violations; {len(unc)} unconnected items listed (report capped at about 500); "
              f"schematic parity: {len(par)} items, of which {len(bad_par)} are real (missing / extra / net conflicts)")
        for v in viol[:10]:
            print("  ", v["type"], v["description"])
        for p in bad_par[:10]:
            print("  parity:", p["type"], p["description"])
        if viol:
            errs.append("DRC violations")
        if bad_par:
            errs.append("schematic parity problems")
        if {v["type"] for v in unc} - {"unconnected_items"}:
            errs.append("unexpected unconnected item type")

        expect = sum(len(n) - 1 for n in nets.values())
        print(f"ratsnest (pcbnew connectivity): {d['ratsnest']} unconnected edges, expected {expect} = sum(pads-1) over schematic nets")
        if d["ratsnest"] != expect:
            errs.append("ratsnest edge count mismatch")

        # negative tests
        t = pcb.read_text(encoding="utf8")
        bad = swap_pad_nets(t)
        neg = work / f"{project}_negative.kicad_pcb"
        ok1 = False
        if bad and bad != t:
            neg.write_text(bad, encoding="utf8", newline="\n")
            ok1 = bool(compare(nl, dump(neg)))
        print("negative test 1 (nets of two relay pads swapped):", "detected" if ok1 else "NOT DETECTED")
        m = re.search(r'\(footprint "Diode_SMD:D_SOD-123"\s*\(layer "F.Cu"\)\s*\(uuid "[^"]*"\)\s*\(at ', t)
        ok2 = False
        if m:    # a diode moved to the back side of the board file is flagged as "not on the front side"
            bad = t[:m.start()] + t[m.start():].replace('(layer "F.Cu")', '(layer "B.Cu")', 1)
            neg.write_text(bad, encoding="utf8", newline="\n")
            ok2 = any("front" in x or "unexpected" in x or "missing" in x for x in compare(nl, dump(neg)))
        print("negative test 2 (a footprint flipped to the back side):", "detected" if ok2 else "NOT DETECTED")
        if neg.exists():
            neg.unlink()
        if not (ok1 and ok2):
            errs.append("negative test failed")
        errs_all += [f"{project}: {x}" for x in errs]

    # determinism: generate twice into fresh directories (the project files start from the committed ones)
    dirs = []
    for k in (1, 2):
        td = work / f"det{k}"
        shutil.rmtree(td, ignore_errors=True)
        td.mkdir(parents=True)
        for p in PROJECTS:
            shutil.copy(HERE / f"{p}.kicad_pro", td / f"{p}.kicad_pro")
        gp.generate(td)
        dirs.append(td)
    same = all(filecmp.cmp(dirs[0] / f, dirs[1] / f, shallow=False) for f in os.listdir(dirs[0])) and \
        all(filecmp.cmp(dirs[0] / f"{p}.kicad_pcb", HERE / f"{p}.kicad_pcb", shallow=False) and
            filecmp.cmp(dirs[0] / f"{p}.kicad_pro", HERE / f"{p}.kicad_pro", shallow=False) for p in PROJECTS)
    print("determinism (two fresh runs identical, and identical to the files in the repository):",
          "OK" if same else "DIFFERENT")
    if not same:
        errs_all.append("generation is not deterministic / files are stale")
    for td in dirs:
        shutil.rmtree(td, ignore_errors=True)

    print("RESULT:", "PASS" if not errs_all else "FAIL")
    for x in errs_all:
        print("  ", x)
    return 1 if errs_all else 0


if __name__ == "__main__":
    sys.exit(main())
