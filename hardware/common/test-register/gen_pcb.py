#!/usr/bin/env python3
"""Generate the UNROUTED KiCad 10 PCB (placement + outline + imported netlist) of the test register board.

    python gen_pcb.py              # export the netlist with kicad-cli, write test_register.kicad_pcb
    python gen_pcb.py --check      # verify the placed board: pad nets == schematic nets, DRC, ratsnest, determinism

Pipeline: gen_register.py (schematic) -> kicad-cli netlist export -> this script.  Every footprint is copied from the
installed KiCad footprint libraries (env KICAD_SHARE overrides the install location), moved to the placement given in
`placement()`, and its pads are assigned to the netlist nets, so the file opens with a ratsnest and no tracks.
Stdlib only.  Layout rules of thumb: connectors on the bottom edge, K lanes in a 3 x 9 grid (relay + its 7 small parts
beside it), control relays and the power / panel / test terminals along the top, board 250 x 150 mm, 2 layers.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from verify_register import Netlist, find1, find_all, parse, default_build_dir, export_netlist  # noqa: F401

HERE = Path(__file__).resolve().parent
KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", "C:/Program Files/KiCad/10.0/share/kicad"))
FPDIR = KICAD_SHARE / "footprints"
PROJECT = "test_register"
NS = uuid.UUID("6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0d01")

BW, BH = 250.0, 150.0          # board size, mm (140 -> 150: room for the 8-pole panel terminal and the 0003 parts)
OX, OY = 20.0, 20.0            # board origin on the (A3) sheet
N = 26


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def f(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ----------------------------------------------------------------------------- placement (board-local mm)
def placement() -> dict[str, tuple[float, float]]:
    pos: dict[str, tuple[float, float]] = {}
    # bottom edge: three D-sub 37 (pin 1 origin; pin row 12.74 mm above the edge)
    y_ds = BH - 12.74
    pos["J1"], pos["J2"], pos["J3"] = (12.0, y_ds), (94.0, y_ds), (176.0, y_ds)
    # K lanes: 3 rows x 9 columns, cell 25 x 21 mm
    for i in range(N):
        col, row = i % 9, i // 9
        xc, yc = 12.5 + col * 25.0, 62.0 + row * 21.0
        n = i + 1
        pos[f"K{n}"] = (xc + 3.0, yc + 1.35)
        cax, cbx = xc + 12.5, xc + 19.0
        pos[f"D{n}"] = (cax, yc + 1.5)
        pos[f"DF{n}"] = (cbx, yc + 1.5)
        pos[f"RA{n}"] = (cax, yc + 4.5)
        pos[f"RB{n}"] = (cbx, yc + 4.5)
        pos[f"RL{n}"] = (cax, yc + 7.0)
        pos[f"DL{n}"] = (cbx, yc + 7.0)
        pos[f"Q{n}"] = (cax, yc + 10.5)
    # control relays K27..K34 along the top, their diodes below
    for k in range(8):
        pos[f"K{27 + k}"] = (20.0 + 13.0 * k, 9.35)
    for k, ref in enumerate(["D27", "D28", "D29", "D30", "D31", None, None, "D32"]):
        if ref:
            pos[ref] = (22.5 + 13.0 * k, 28.0)
    for k, ref in enumerate(["DZ27", "DZ28", "DZ29", "D33"]):
        pos[ref] = (22.5 + 13.0 * k, 31.5)
    for k, ref in enumerate(["D34", "D35", "D36"]):          # 0003 R1: series diodes pin -> coil
        pos[ref] = (22.5 + 13.0 * k, 35.0)
    # indicator / pull-down / stop-lamp block
    grid = ["RI1", "RI2", "RI3", "RI4", "RR1", "RR2",
            "DI1", "DI2", "DI3", "DI4", "RR3", "RS1",
            "RS2", "RS3", "DL27"]
    for k, ref in enumerate(grid):
        pos[ref] = (132.0 + 6.0 * (k % 6), 12.0 + 5.0 * (k // 6))
    pos["Q27"] = (172.0, 12.0)
    pos["Q28"] = (172.0, 20.0)
    # power / test / panel terminals, fuses, capacitors, TVS, mounting holes
    pos["J4"], pos["J5"], pos["J6"] = (212.0, 11.0), (230.0, 11.0), (200.0, 54.0)
    pos["F1"], pos["F2"] = (232.0, 28.0), (232.0, 36.0)
    pos["C1"], pos["C2"] = (205.0, 41.0), (217.0, 41.0)
    pos["DV1"] = (240.0, 20.0)
    # decision 0003: series P-MOSFET + gate parts (R6), HOME pull-down (R4), solder jumpers (R3), CHASSIS pair (R5)
    pos["QP1"], pos["DZP1"], pos["RGP1"] = (188.0, 14.0), (200.0, 8.0), (200.0, 13.0)
    pos["RR4"], pos["RCH1"], pos["CCH1"] = (200.0, 18.0), (186.0, 28.0), (194.0, 28.0)
    pos["SJ1"], pos["SJ2"] = (186.0, 36.0), (186.0, 42.0)
    for ref, p in zip(["H1", "H2", "H3", "H4"], [(5.0, 5.0), (125.0, 5.0), (245.0, 5.0), (244.0, BH - 5.0)]):
        pos[ref] = p
    return pos


# ----------------------------------------------------------------------------- footprint transform
def load_fp(libfp: str) -> str:
    lib, name = libfp.split(":")
    return (FPDIR / f"{lib}.pretty" / f"{name}.kicad_mod").read_text(encoding="utf8")


def _hidden_property(key: str, val: str) -> str:
    """A hidden footprint field (for library footprints that lack Description / Datasheet)."""
    nl, tab, q = chr(10), chr(9), chr(34)
    lines = ['', tab + '(property ' + q + key + q + ' ' + q + val + q,
             2 * tab + '(at 0 0 0)', 2 * tab + '(unlocked yes)', 2 * tab + '(layer ' + q + 'F.Fab' + q + ')',
             2 * tab + '(hide yes)', 2 * tab + '(effects', 3 * tab + '(font', 4 * tab + '(size 1.27 1.27)',
             4 * tab + '(thickness 0.15)', 3 * tab + ')', 2 * tab + ')', tab + ')']
    return nl.join(lines)


def make_footprint(ref: str, libfp: str, value: str, x: float, y: float, path: str, sheetname: str, sheetfile: str,
                   pad_nets: dict, descr: str = "", datasheet: str = "") -> str:
    t = load_fp(libfp)
    t = re.sub(r'^\(footprint "[^"]*"', f'(footprint "{libfp}"', t, count=1)
    t = re.sub(r'\n\t\(version \d+\)\n\t\(generator "[^"]*"\)(\n\t\(generator_version "[^"]*"\))?', "", t, count=1)
    t = t.replace('\n\t(layer "F.Cu")\n',
                  f'\n\t(layer "F.Cu")\n\t(uuid "{uid("fp", ref)}")\n\t(at {f(x)} {f(y)})\n', 1)
    t = re.sub(r'\(property "Reference" "[^"]*"', f'(property "Reference" "{ref}"', t, count=1)
    if re.fullmatch(r"(D|DF|DZ|DL|DI|RA|RB|RL|RI|RR|RS|Q)\d+", ref):      # packed small parts: reference on the fab layer only
        t = re.sub(r'(\(property "Reference" "[^"]*"\s+\(at [-\d.]+ [-\d.]+ [-\d.]+\)\s+\(layer )"F\.SilkS"',
                   lambda m: m.group(1) + '"F.Fab"', t, count=1)
    t = re.sub(r'\(property "Value" "[^"]*"', f'(property "Value" "{value}"', t, count=1)
    for key, val in (("Description", descr), ("Datasheet", datasheet)):
        if f'(property "{key}"' in t:
            t = re.sub(r'\(property "%s" "[^"]*"' % key, lambda m: f'(property "{key}" "{val}"', t, count=1)
        else:      # library footprint without this field: add a hidden one
            hidden = _hidden_property(key, val)
            t = t.replace("\n\t(attr ", hidden + "\n\t(attr ", 1)
    t = t.replace("\n\t(attr ", f'\n\t(path "{path}")\n\t(sheetname "{sheetname}")\n\t(sheetfile "{sheetfile}")\n\t(attr ', 1)
    counter = [0]

    def newuuid(m):
        counter[0] += 1
        return f'(uuid "{uid(ref, counter[0])}")'
    t = re.sub(r'\(uuid "[^"]*"\)', newuuid, t)

    padn = [0]

    def padnet(m):
        blk = m.group(0)
        pin = m.group(1)
        padn[0] += 1
        add = ""
        if pin in pad_nets:
            nid, nname = pad_nets[pin]
            add += f'\n\t\t(net {nid} "{nname}")'
        if "(uuid" not in blk:
            add += f'\n\t\t(uuid "{uid(ref, "pad", padn[0])}")'
        return blk[:-3] + add + "\n\t)"       # blk ends with "\n\t)"
    t = re.sub(r'\t\(pad "([^"]*)".*?\n\t\)', padnet, t, flags=re.S)
    t = t.rstrip("\n")
    return "\n".join("\t" + ln if ln else ln for ln in t.split("\n"))


# ----------------------------------------------------------------------------- board
LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
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

SETUP = """\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t\t(pcbplotparams
\t\t\t(layerselection 0x00000000_00000000_55555555_5755f5ff)
\t\t\t(plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
\t\t\t(disableapertmacros no)
\t\t\t(usegerberextensions no)
\t\t\t(usegerberattributes yes)
\t\t\t(usegerberadvancedattributes yes)
\t\t\t(creategerberjobfile yes)
\t\t\t(svgprecision 4)
\t\t\t(plotframeref no)
\t\t\t(mode 1)
\t\t\t(useauxorigin no)
\t\t\t(pdf_front_fp_property_popups yes)
\t\t\t(pdf_back_fp_property_popups yes)
\t\t\t(plotreference yes)
\t\t\t(plotvalue yes)
\t\t\t(plotfptext yes)
\t\t\t(plotinvisibletext no)
\t\t\t(sketchpadsonfab no)
\t\t\t(subtractmaskfromsilk no)
\t\t\t(outputformat 1)
\t\t\t(mirror no)
\t\t\t(drillshape 1)
\t\t\t(scaleselection 1)
\t\t\t(outputdirectory "")
\t\t)
\t)
"""


def build(nl_text: str) -> str:
    tree = parse(nl_text)
    comps = {}
    for c in find_all(find1(tree, "components"), "comp"):
        ref = find1(c, "ref")[1]
        sp = find1(c, "sheetpath")
        sheet_ts = find1(sp, "tstamps")[1]            # "/" or "/<sheet uuid>/"
        sheet_names = find1(sp, "names")[1]
        sym_ts = find1(c, "tstamps")[1]
        dsc, ds = find1(c, "description"), find1(c, "datasheet")
        comps[ref] = dict(value=find1(c, "value")[1], fp=find1(c, "footprint")[1],
                          descr=dsc[1] if dsc else "", datasheet=ds[1] if ds else "",
                          path=sheet_ts + sym_ts, sheetname=sheet_names,
                          sheetfile="test_register.kicad_sch" if sheet_names == "/" else "sense_lanes.kicad_sch")
    nets: dict[str, list] = {}
    for n in find_all(find1(tree, "nets"), "net"):
        name = find1(n, "name")[1]
        nets[name] = [(find1(x, "ref")[1], find1(x, "pin")[1]) for x in find_all(n, "node")]
    net_id = {name: i + 1 for i, name in enumerate(sorted(nets))}
    pad_nets: dict[str, dict] = {r: {} for r in comps}
    for name, nodes in nets.items():
        for ref, pin in nodes:
            pad_nets[ref][pin] = (net_id[name], name)
    # D-sub shells (footprint pads "SH", no pin on the schematic symbol) and the mounting holes are CHASSIS (0003 R5)
    chassis = next(name for name, nodes in nets.items() if ("H1", "1") in nodes)
    for ref, c in comps.items():
        if "DSUB" in c["fp"]:
            pad_nets[ref]["SH"] = (net_id[chassis], chassis)
    pos = placement()
    missing = set(comps) - set(pos)
    extra = set(pos) - set(comps)
    if missing or extra:
        raise SystemExit(f"placement mismatch: missing {sorted(missing)} extra {sorted(extra)}")

    out = ['(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n\t(generator_version "10.0")\n'
           '\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper "A3")\n'
           '\t(title_block\n\t\t(title "Test register (placement only, unrouted)")\n\t\t(date "2026-11-15")\n\t\t(rev "A")\n'
           '\t\t(comment 1 "Project Christopher - common part. Generated by gen_pcb.py - do not edit by hand")\n'
           '\t\t(comment 2 "Routing is still to do; see README.md")\n\t)\n' + LAYERS + SETUP,
           '\t(net 0 "")']
    for name, i in sorted(net_id.items(), key=lambda kv: kv[1]):
        out.append(f'\t(net {i} "{name}")')
    for ref in sorted(comps, key=lambda r: (re.sub(r"\d+", "", r), int(re.sub(r"\D", "", r) or 0))):
        c = comps[ref]
        x, y = pos[ref]
        out.append(make_footprint(ref, c["fp"], c["value"], x + OX, y + OY, c["path"], c["sheetname"], c["sheetfile"],
                                  pad_nets[ref], c["descr"], c["datasheet"]))
    # outline (rounded corners not needed: plain rectangle)
    x0, y0, x1, y1 = OX, OY, OX + BW, OY + BH
    for k, (a, b, c_, d) in enumerate([(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]):
        out.append(f'\t(gr_line\n\t\t(start {f(a)} {f(b)})\n\t\t(end {f(c_)} {f(d)})\n\t\t(stroke\n\t\t\t(width 0.1)\n'
                   f'\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n\t\t(uuid "{uid("edge", k)}")\n\t)')
    out.append('\t(embedded_fonts no)\n)')
    return "\n".join(out) + "\n"


DUMP = """
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
pads, dups = {}, 0
for f in b.GetFootprints():
    seen = set()
    for p in f.Pads():
        n = p.GetNetname()
        if n and p.GetNumber() in seen:
            dups += 1
        seen.add(p.GetNumber())
        k = "%s|%s" % (f.GetReference(), p.GetNumber())
        pads[k] = n if pads.get(k, n) == n else "CONFLICT"
print(json.dumps({"pads": pads, "dups": dups, "tracks": len(list(b.GetTracks())), "zones": len(list(b.Zones())),
                  "ratsnest": b.GetConnectivity().GetUnconnectedCount(False)}))
"""


def check() -> int:
    """Placement checks: pad nets == schematic nets (pcbnew), DRC 0 violations and schematic parity (only the SH shell
    pads, which have no symbol pin, may differ), ratsnest == sum(pads - 1), no copper, determinism, negative tests."""
    import json
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    py = os.environ.get("KICAD_PYTHON", str(Path(cli).parent / "python.exe"))
    work = default_build_dir()
    net = export_netlist(work)
    nl = Netlist(net.read_text(encoding="utf8"))
    pcb = HERE / f"{PROJECT}.kicad_pcb"
    errs: list[str] = []

    def dump(path):
        r = subprocess.run([py, "-c", DUMP, str(path)], capture_output=True, text=True, check=True)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        d["pads"] = {tuple(k.split("|", 1)): v for k, v in d["pads"].items()}
        return d

    def compare(d):
        e = []
        want = {nd: name for name, nodes in nl.nets.items() for nd in nodes}
        ch = next(name for name, nodes in nl.nets.items() if ("H1", "1") in nodes)
        for r, c in nl.parts.items():
            if "DSUB" in c["footprint"]:
                want[(r, "SH")] = ch
        bad = 0
        for nd, name in want.items():
            got = d["pads"].get(nd)
            if got is None:
                e.append(f"pad {nd} not on the board")
            elif got != name:
                bad += 1
                if bad <= 5:
                    e.append(f"pad {nd}: board net {got!r} != schematic net {name!r}")
        for nd, name in d["pads"].items():
            if nd not in want and name:
                e.append(f"pad {nd} has net {name!r} but is not in the schematic")
        if d["tracks"] or d["zones"]:
            e.append("board has copper (placement only expected)")
        return e

    d = dump(pcb)
    e = compare(d)
    errs += e
    print(f"parts {len(nl.parts)}, pads compared {len(d['pads'])}: net comparison", "OK" if not e else f"{len(e)} problems {e[:5]}")
    subprocess.run([cli, "pcb", "drc", "--severity-all", "--schematic-parity", "--format", "json", "-o", str(work / "drc.json"), str(pcb)],
                   check=True, capture_output=True)
    dr = json.loads((work / "drc.json").read_text(encoding="utf8"))
    par = [p for p in dr.get("schematic_parity", []) if not (p["type"] == "net_conflict" and all(
        "pad SH " in i.get("description", "") for i in p.get("items", [])))]
    print(f"DRC: {len(dr['violations'])} violations; {len(dr['unconnected_items'])} unconnected items listed (capped at about 500); "
          f"schematic parity: {len(dr.get('schematic_parity', []))} items, {len(par)} real (the SH shell pads have no symbol pin)")
    if dr["violations"]:
        errs.append("DRC violations")
        for v in dr["violations"][:8]:
            print("  ", v["type"], v["description"])
    if par:
        errs.append("schematic parity problems")
    n_dup = d["dups"]
    expect = sum(len(n) - 1 for n in nl.nets.values()) + n_dup
    print(f"ratsnest (pcbnew): {d['ratsnest']} edges, expected {expect} = sum(pads-1) over schematic nets + {n_dup} "
          "duplicate-numbered pads (second D-sub shell pads, MOSFET tab)")
    if d["ratsnest"] != expect:
        errs.append("ratsnest edge count mismatch")
    txt = pcb.read_text(encoding="utf8")
    again = build(net.read_text(encoding="utf8"))
    same = again == txt and again == build(net.read_text(encoding="utf8"))
    print("determinism (two generations identical, and identical to the file in the repository):", "OK" if same else "DIFFERENT")
    if not same:
        errs.append("generation not deterministic / file stale")
    def pad_nets_of(text, num):
        """(start, end) of the net id + name inside every pad `num` block that has a net."""
        out = []
        for m in re.finditer(r'\(pad "%s"' % num, text):
            i, depth, instr = m.start(), 0, False
            j = i
            while True:
                c = text[j]
                if instr:
                    instr = c != '"'
                elif c == '"':
                    instr = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            mm = re.search(r'\(net (\d+ "[^"]*")\)', text[i:j])
            if mm:
                out.append((i + mm.start(1), i + mm.end(1), mm.group(1)))
        return out
    pads4 = pad_nets_of(txt, "4")
    first = pads4[0]
    other = next(x for x in pads4 if x[2] != first[2])
    (a1, b1, _), (a2, b2, _) = sorted([first, other])
    bad_txt = txt[:a1] + txt[a2:b2] + txt[b1:a2] + txt[a1:b1] + txt[b2:]
    neg = work / "negative.kicad_pcb"
    neg.write_text(bad_txt, encoding="utf8", newline="\n")
    ok = bad_txt != txt and bool(compare(dump(neg)))
    neg.unlink()
    print("negative test (nets of two relay pads swapped):", "detected" if ok else "NOT DETECTED")
    if not ok:
        errs.append("negative test failed")
    gid = re.search(r'\(net (\d+) "/?GND"\)', txt).group(1)
    shell_bad = re.sub(r'(\(pad "SH".*?\(net )\d+ "[^"]*"', lambda mm: mm.group(1) + gid + ' "GND"', txt, count=1, flags=re.S)
    neg = work / "negative2.kicad_pcb"
    neg.write_text(shell_bad, encoding="utf8", newline="\n")
    ok2 = shell_bad != txt and bool(compare(dump(neg)))
    neg.unlink()
    print("negative test (a D-sub shell pad on 0 V instead of CHASSIS):", "detected" if ok2 else "NOT DETECTED")
    if not ok2:
        errs.append("negative test 2 failed")
    print("RESULT:", "PASS" if not errs else "FAIL")
    return 1 if errs else 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    net = export_netlist(default_build_dir())
    pcb = build(net.read_text(encoding="utf8"))
    (HERE / f"{PROJECT}.kicad_pcb").write_text(pcb, encoding="utf8", newline="\n")
    print(f"wrote {PROJECT}.kicad_pcb ({len(pcb) // 1024} kB)")


if __name__ == "__main__":
    main()
