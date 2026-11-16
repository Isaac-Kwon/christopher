#!/usr/bin/env python3
"""Generate the KiCad 10 schematics (and the two footprints) of the visitor-swappable rotor CARTRIDGE arrangement.

  rotor_socket        one per relay unit, in the rotor strip below the unit row (hardware/common/rotor-cartridge.md):
                      three bays L, M, R (left to right, as in an Enigma), each with 104 + 2 spring pins on the front
                      and four male D-sub 37 on the back for the leads from the unit's shifters.  The four connectors
                      of a bay carry the functions of the W card's J1..J4 (bay b: J(4b+1)..J(4b+4) = W.J1..W.J4), so a
                      lead replaces exactly one stack jumper of the card arrangement.  A series detect chain
                      (J16.1 -> bay L detect pins -> lid switch J13 -> bay M -> J14 -> bay R -> J15 -> J16.2) closes
                      only with all three cartridges seated and all three lids latched (ILOCK, rotor-cartridge.md).
  cart_I .. cart_VIII  the passive cartridge PCB of one rotor: 4 x 26 + 2 gold pads on the back, the rotor wiring of
                      data/wirings/enigma.txt between them (outbound OI i -> OO W[i], return RI j -> RO W^-1[j], the same
                      nets as the W card) and a short between the two detect pads.

Standard library only (ki_common.py).  Geometry shared with gen_cartridge_pcb.py lives here.

    python gen_cartridge.py        # footprint library Christopher.pretty + fp-lib-table + the nine projects
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import gen_cards as C
import ki_common as K

HERE = Path(__file__).resolve().parent
NS = "6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0f03"
N = 26
L = C.L

# ----------------------------------------------------------------------------- geometry (mm, KiCad board frame, y down)
BAYS = ("L", "M", "R")                       # left, middle, right rotor (Enigma order, seen from the visitor)
W_SLOT = {"L": "W_L", "M": "W_M", "R": "W_R"}
SOCK_W, SOCK_H = 116.0, 166.0                # one socket board per unit, column pitch 120 (layout.md 3.5)
BAY_X = {"L": 19.0, "M": 58.0, "R": 97.0}    # bay pitch 39; the D-sub columns sit at BAY_X +- DSUB_DX
BAY_YC = 83.0                                # bay datum (pin field centre) on the socket board
PITCH = 2.54
# pad columns of the field (x offset from the datum), row i (A..Z) at y = (i - 12.5) * PITCH (A at the top)
GROUPS = (("OI", -3.81), ("OO", -1.27), ("RO", 1.27), ("RI", 3.81))
# group -> function = the W card connector it replaces (README.md section 2)
GROUP_W = {"OI": "J3", "OO": "J1", "RO": "J2", "RI": "J4"}
GROUP_FN = {"OI": "OUTBOUND_IN", "OO": "OUTBOUND_OUT", "RO": "RETURN_OUT", "RI": "RETURN_IN"}
DETECT = (("DT1", -1.27, -36.0), ("DT2", 1.27, -36.0))      # above row A only: a cartridge turned 180 deg misses them
# keying dowels (on the bay frame, through the socket board) and the matching holes of the cartridge:
# diameter, x, y relative to the datum.  Different diameters (turned 180 deg: the 3.05 hole meets the 4 dowel) and
# 1 mm off the centre line in opposite directions (turned over: the holes land 2 mm beside the dowels).
DOWELS = (("DW4", 4.0, -1.0, -42.0), ("DW3", 3.0, 1.0, 42.0))
HOLE_PLAY = 0.05                             # cartridge hole = dowel + 0.05 (H8/h6 class, estimate)
DSUB_DX = 10.0                               # D-sub columns at BAY_X -+ 10 (19 / 20 apart; courtyards clear the dowels)
DSUB_ROW_Y = (37.7, 128.3)                   # centres of the two D-sub rows (courtyards 2.5 ... 72.9, 93.1 ... 163.5)
# which W-function connector sits where (column -1 = left of the field, +1 = right; row 0 = top, 1 = bottom)
DSUB_SPOT = {"OI": (-1, 0), "OO": (-1, 1), "RO": (1, 0), "RI": (1, 1)}
MOUNT_X = (9.0, 48.0, 68.0, 107.0)           # M3 holes (CHASSIS) in the band between the D-sub rows
MOUNT_Y = BAY_YC
JST_X = {"J13": 29.0, "J16": 38.5, "J14": 77.5, "J15": 87.0}    # 2-pin XH on the back, same band

CART_W, CART_H = 34.0, 118.0                 # cartridge PCB; its datum is the board centre
CART_DATUM = (CART_W / 2, CART_H / 2)
CART_SCREWS = ((CART_W / 2, 5.0), (CART_W / 2, CART_H - 5.0))   # NPTH 2.7 for the housing screws (M2.5)

# ----------------------------------------------------------------------------- footprints (no library part)
FP_LIB = "Christopher"
FP_PIN = "SpringPin_SMD_D1.6_P2.54"
FP_PAD = "CartridgePad_D1.8"
PIN_PAD_D, PIN_BODY_D = 1.6, 1.5             # ASSUMED (SMT spring pin, 2.54 mm grid): confirm with the datasheet
CART_PAD_D = 1.8                             # hard-gold pad on the cartridge (tip contact window +-0.6 with a 0.6 tip)

FP_DSUB = "Connector_Dsub:DSUB-37_Pins_Vertical_P2.77x2.84mm_MountingHoles"
FP_JST = "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical"
FP_HOLE = "MountingHole:MountingHole_3.2mm_M3_Pad"
S_ONE = K.Sym("Connector_Generic", "Conn_01x01")
S_TWO = K.Sym("Connector_Generic", "Conn_01x02")
S_DSUB = K.Sym("Connector", "DC37_Pins_MountingHoles")
S_HOLE = K.Sym("Mechanical", "MountingHole_Pad")


def field(group_x: float, i: int) -> tuple[float, float]:
    """Datum-relative position of row i of a pad column."""
    return group_x, (i - 12.5) * PITCH


def sock_conn(bay: str, group: str) -> str:
    """Socket D-sub of a bay and group: bay b, W connector Jn -> J(4b + n)."""
    return f"J{4 * BAYS.index(bay) + int(GROUP_W[group][1:])}"


def w_conn_of_sock(ref: str) -> tuple[str, str]:
    """Inverse of sock_conn: socket J1..J12 -> (W slot instance, W connector)."""
    n = int(ref[1:]) - 1
    return W_SLOT[BAYS[n // 4]], f"J{n % 4 + 1}"


def pin_ref(bay: str, name: str) -> str:
    return f"{bay}{name}"


def cart_projects() -> list[tuple[str, str]]:
    return [(f"cart_{r}", r) for r in C.ROTORS]


def _fp_text(name: str, descr: str, tags: str, pad: str, fab_d: float, crt_d: float) -> str:
    ids = K.Ids(NS)
    u = lambda *p: ids("fp", name, *p)
    eff = "(effects\n\t\t\t(font\n\t\t\t\t(size 0.8 0.8)\n\t\t\t\t(thickness 0.12)\n\t\t\t)\n\t\t)"
    circ = lambda key, r, layer, w: (f'\t(fp_circle\n\t\t(center 0 0)\n\t\t(end {K.fmt(r)} 0)\n\t\t(stroke\n'
                                     f'\t\t\t(width {w})\n\t\t\t(type solid)\n\t\t)\n\t\t(fill no)\n\t\t(layer "{layer}")\n'
                                     f'\t\t(uuid "{u(key)}")\n\t)\n')
    return (f'(footprint "{name}"\n\t(version 20241229)\n\t(generator "gen_cartridge.py")\n\t(layer "F.Cu")\n'
            f'\t(descr "{descr}")\n\t(tags "{tags}")\n'
            f'\t(property "Reference" "REF**"\n\t\t(at 0 -1.8 0)\n\t\t(layer "F.Fab")\n\t\t(uuid "{u("ref")}")\n\t\t{eff}\n\t)\n'
            f'\t(property "Value" "{name}"\n\t\t(at 0 1.8 0)\n\t\t(layer "F.Fab")\n\t\t(uuid "{u("val")}")\n\t\t{eff}\n\t)\n'
            f'\t(attr smd)\n' + circ("fab", fab_d / 2, "F.Fab", 0.1) + circ("crt", crt_d / 2, "F.CrtYd", 0.05)
            + pad.replace("@UUID@", u("pad")) + '\t(embedded_fonts no)\n)\n')


def write_footprints(libdir: Path):
    libdir.mkdir(exist_ok=True)
    pin_pad = (f'\t(pad "1" smd circle\n\t\t(at 0 0)\n\t\t(size {K.fmt(PIN_PAD_D)} {K.fmt(PIN_PAD_D)})\n'
               f'\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t\t(uuid "@UUID@")\n\t)\n')
    (libdir / f"{FP_PIN}.kicad_mod").write_text(_fp_text(
        FP_PIN, "Surface-mount spring-loaded pin on a 2.54 mm grid (rotor cartridge socket, hardware/common/rotor-cartridge.md). "
        "Pad and body diameter ASSUMED: confirm with the datasheet of the chosen pin.",
        "spring pin pogo cartridge", pin_pad, PIN_BODY_D, 2.0), encoding="utf8", newline="\n")
    cart_pad = (f'\t(pad "1" smd circle\n\t\t(at 0 0)\n\t\t(size {K.fmt(CART_PAD_D)} {K.fmt(CART_PAD_D)})\n'
                f'\t\t(layers "F.Cu" "F.Mask")\n\t\t(uuid "@UUID@")\n\t)\n')
    (libdir / f"{FP_PAD}.kicad_mod").write_text(_fp_text(
        FP_PAD, "Hard-gold contact pad of the rotor cartridge (placed on the back side, facing the spring pins).",
        "cartridge contact pad", cart_pad, CART_PAD_D, 2.2), encoding="utf8", newline="\n")


# ----------------------------------------------------------------------------- schematics
def socket_sheet() -> K.Sheet:
    ids = K.Ids(NS)
    sh = K.Sheet(ids, "rotor_socket", "A1", "Relay rotor socket board: three cartridge bays of one unit",
                 ["Project Christopher - relay track, hardware/common/rotor-cartridge.md; generated by gen_cartridge.py",
                  "Bay b (L, M, R): spring pin <b><group><row> -> pin row+1 of D-sub J(4b+n), n = the W card connector of the group",
                  "Groups: OI = W.J3 outbound in, OO = W.J1 outbound out, RO = W.J2 return out, RI = W.J4 return in; 27-37 not connected",
                  "Detect chain J16.1 - L detect - J13 lid L - M detect - J14 lid M - R detect - J15 lid R - J16.2 (ILOCK)"],
                 inst_path=f"/{ids('sheetfile', 'rotor_socket')}")
    for b, bay in enumerate(BAYS):
        x0 = 30.0 + b * 270.0
        sh.text(f"BAY {bay}  (rotor slot {W_SLOT[bay]}, D-sub J{4 * b + 1}..J{4 * b + 4} = W.J1..J4)", x0, 22.0, 2.5)
        for g, (grp, _) in enumerate(GROUPS):
            xp = x0 + g * 66.0
            ref = sock_conn(bay, grp)
            for i in range(N):
                sh.part(S_ONE, xp, 40.0 + i * 5.08, pin_ref(bay, f"{grp}{i + 1}"), f"PIN {bay} {grp} {L(i)}",
                        f"{FP_LIB}:{FP_PIN}", {"1": f"{bay}_{grp}_{L(i)}"}, "",
                        f"Spring pin, bay {bay}, {GROUP_FN[grp]}, line {L(i)}", show_value=False)
            nets = {str(i + 1): f"{bay}_{grp}_{L(i)}" for i in range(N)}
            nets["SH"] = "CHASSIS"
            sh.part(S_DSUB, xp + 30.0, 105.0, ref, f"{bay}_{GROUP_FN[grp]}", FP_DSUB, nets, "",
                    f"Lead from the unit stack (replaces the jumper to {W_SLOT[bay]}.{GROUP_W[grp]}): pins 1-26 = lines A-Z, "
                    "27-37 NOT connected (0003 5.2), shell = CHASSIS", length=5.08)
    # detect chain: CHAIN_2b = bay b detect pin 1 (and the previous lid switch), CHAIN_2b+1 = detect pin 2 + lid switch pin 1
    net = lambda n: f"CHAIN_{n}"
    y = 300.0
    for b, bay in enumerate(BAYS):
        x0 = 40.0 + b * 120.0
        for d, (dn, _, _) in enumerate(DETECT):
            n = 2 * b + d
            sh.part(S_ONE, x0 + d * 30.0, y, pin_ref(bay, dn), f"DETECT {bay}{d + 1}", f"{FP_LIB}:{FP_PIN}", {"1": net(n)}, "",
                    f"Detect spring pin {d + 1} of bay {bay} (the cartridge shorts the pair)", show_value=False)
        sh.part(S_TWO, x0 + 80.0, y, f"J{13 + b}", f"LID_SWITCH_{bay}", FP_JST, {"1": net(2 * b + 1), "2": net(2 * b + 2)}, "",
                f"Lid switch of bay {bay} (closed only with the clamp lid latched)", length=5.08)
    sh.part(S_TWO, 420.0, y, "J16", "CHAIN", FP_JST, {"1": net(0), "2": net(6)}, "",
            "Detect chain: 1 = from the previous unit / ILOCK supply, 2 = to the next unit / ILOCK return", length=5.08)
    for h in range(4):
        sh.part(S_HOLE, 500.0 + h * 20.0, y, f"H{h + 1}", "M3_CHASSIS", FP_HOLE, {"1": "CHASSIS"}, "",
                "M3 hole, plated, net CHASSIS (to the bay frame)", in_bom=False)
    C.flag(sh, "CHASSIS", 590.0, y - 10.0, 1)
    sh.text("ROTOR SOCKET (one per unit).  The bay + the cartridge in it = one W card of the card arrangement:\n"
            "  outbound: lead into J(4b+3) pin i+1 -> pin <b>OI(i+1) -> cartridge -> pin <b>OO(W[i]+1) -> J(4b+1) -> lead\n"
            "  return:   lead into J(4b+4) pin j+1 -> pin <b>RI(j+1) -> cartridge -> pin <b>RO(Winv[j]+1) -> J(4b+2) -> lead\n"
            "Detect chain in series with the lid switches: open whenever a bay is empty, a cartridge is not seated or a lid is open.\n"
            "No 0 V and no supply on this board; the chain is the ILOCK loop of the bank (power-unit.md).",
            40.0, 330.0, 2.0)
    return sh


def cart_sheet(name: str) -> K.Sheet:
    w = C.table("rotor", name)
    winv = C.inverse(w)
    proj = f"cart_{name}"
    ids = K.Ids(NS)
    sh = K.Sheet(ids, proj, "A3", f"Relay rotor cartridge: rotor {name}",
                 ["Project Christopher - relay track, hardware/common/rotor-cartridge.md; generated by gen_cartridge.py",
                  f"Rotor {name}: {''.join(L(x) for x in w)} (line i -> W[i], entry side -> reflector side)",
                  "Outbound pad OI(i+1) -> OO(W[i]+1); return RI(j+1) -> RO(W^-1[j]+1); DT1-DT2 shorted (detect)"],
                 inst_path=f"/{ids('sheetfile', proj)}")
    netf = {"OI": lambda i: C.fwd_net(i, w), "OO": lambda i: C.fwd_net(winv[i], w),
            "RI": lambda i: C.ret_net(i, winv), "RO": lambda i: C.ret_net(w[i], winv)}
    for g, (grp, _) in enumerate(GROUPS):
        x = 40.0 + g * 80.0
        sh.text(f"{grp} = {GROUP_FN[grp]}", x - 10.0, 30.0, 2.0)
        for i in range(N):
            sh.part(S_ONE, x, 40.0 + i * 5.08, f"{grp}{i + 1}", f"PAD {grp} {L(i)}", f"{FP_LIB}:{FP_PAD}",
                    {"1": netf[grp](i)}, "", f"Cartridge pad {grp}, line {L(i)}", show_value=False)
    for d, (dn, _, _) in enumerate(DETECT):
        sh.part(S_ONE, 360.0, 60.0 + d * 10.16, dn, f"DETECT {d + 1}", f"{FP_LIB}:{FP_PAD}", {"1": "DET"}, "",
                "Detect pad (the pair is shorted)", show_value=False)
    sh.text(f"ROTOR {name} cartridge.  Passive: the same nets as the W card rotor_{name} (OW_<in><out>, RW_<in><out>).\n"
            f"W = {''.join(L(x) for x in w)}    Winv = {''.join(L(x) for x in winv)}\n"
            "Pads on the back side, facing the spring pins of the bay; the two keying holes are on the board (no net).",
            300.0, 120.0, 1.8)
    return sh


# ----------------------------------------------------------------------------- kicad-cli on staged copies
def stage(path: Path, work: Path) -> Path:
    """Copy a .kicad_sch / .kicad_pcb with its .kicad_pro, the fp-lib-table and the footprint library into work/stage
    and return the copy: kicad-cli then writes its .kicad_prl / lock files there, not next to the tracked files
    (a lock left in the synced folder by an interrupted run made later runs wait)."""
    d = work / "stage"
    d.mkdir(parents=True, exist_ok=True)
    for f in (path, path.with_suffix(".kicad_pro"), path.parent / "fp-lib-table"):
        if f.exists():
            shutil.copyfile(f, d / f.name)
    lib = path.parent / "Christopher.pretty"
    if lib.exists():
        shutil.copytree(lib, d / lib.name, dirs_exist_ok=True)
    return d / path.name


def cli(*args, timeout=120, tries=3):
    """kicad-cli with a timeout and retries (an occasional run never returns on this machine)."""
    for _ in range(tries):
        try:
            return subprocess.run([K.kicad_cli(), *args], check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
    raise SystemExit(f"kicad-cli {' '.join(args[:3])} did not finish in {tries} tries")


def export_netlist(sch: Path, work: Path) -> K.Netlist:
    s = stage(sch, work)
    out = work / (sch.stem + ".net")
    cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(s))
    return K.Netlist(out.read_text(encoding="utf8"))


def staged_pad_nets(pcb: Path, work: Path) -> dict:
    """K.pcb_pad_nets on a staged copy."""
    return K.pcb_pad_nets(stage(pcb, work), work)


def staged_drc(pcb: Path, work: Path) -> dict:
    """K.run_drc on a staged copy (the .kicad_pro next to it carries the board rules); adds the staged path."""
    s = stage(pcb, work)
    out = work / (pcb.stem + "_drc.json")
    cli("pcb", "drc", "--severity-all", "--format", "json", "-o", str(out), str(s))
    d = json.loads(out.read_text(encoding="utf8"))
    return {"violations": d.get("violations", []), "unconnected": d.get("unconnected_items", []),
            "parity": d.get("schematic_parity", []), "staged": s}


def main(outdir: Path = HERE):
    write_footprints(outdir / "Christopher.pretty")
    K.write_fp_lib_table(outdir, {FP_LIB: "${KIPRJMOD}/Christopher.pretty"})
    K.write_project_and_sheet(outdir, "rotor_socket", socket_sheet())
    print("wrote rotor_socket.kicad_sch")
    for proj, name in cart_projects():
        K.write_project_and_sheet(outdir, proj, cart_sheet(name))
        print(f"wrote {proj}.kicad_sch")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
