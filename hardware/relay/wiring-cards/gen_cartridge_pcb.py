#!/usr/bin/env python3
"""Generate the KiCad 10 PCBs of the rotor cartridge arrangement: deterministic PLACEMENT only (no routing).

    python gen_cartridge_pcb.py      # rotor_socket (116 x 166, 4 layers) and cart_I .. cart_VIII (34 x 118, 4 layers)

rotor_socket: per bay (datum = BAY_X, BAY_YC of gen_cartridge.py) the 4 x 26 spring pins and the two detect pins on
the FRONT, the two keying-dowel holes (NPTH 4.0 / 3.0, diagonal); the twelve male D-sub 37 on the BACK, turned so
that their pin rows run vertically, in six columns at BAY_X -+ 10 and two rows (no D-sub pin lies under a pin
field); four M3 holes (CHASSIS) and the four 2-pin chain/lid-switch headers in the band between the D-sub rows.
cart_<rotor>: the 4 x 26 + 2 pads on the BACK at exactly the datum-relative positions of the spring pins, the keying
holes 4.05 / 3.05 at the dowel positions, two NPTH 2.7 for the housing.  The verifier (verify_cartridge_pcb.py and
verify_cards.py) derives the pin-to-pad mating from these board files.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import gen_cards as CC
import gen_cartridge as G
import ki_common as K

HERE = Path(__file__).resolve().parent
UID_NS = "6f1c2b0e-52a1-4a43-9c1b-0b0b0b0b0f04"
N = 26

# D-sub 37 vertical on the back, rotation 90: pin 1 at the footprint origin, the row runs to -y, the second row
# at -2.84 in x; the courtyard centre is at (-1.42, -24.925) from the origin (checked against the library file
# by verify_cartridge_pcb.py through the pad positions).
DSUB_ROT = 90
DSUB_CENTRE_OFF = (-1.42, -24.925)
JST_CENTRE_OFF = (1.25, -0.525)          # 2-pin XH on the back: courtyard centre from pin 1


def lib() -> K.FpLib:
    return K.FpLib({G.FP_LIB: HERE / "Christopher.pretty"})


def socket_placements() -> tuple[dict, list]:
    P, holes = {}, []
    for bay in G.BAYS:
        bx, by = G.BAY_X[bay], G.BAY_YC
        for grp, gx in G.GROUPS:
            for i in range(N):
                x, y = G.field(gx, i)
                P[G.pin_ref(bay, f"{grp}{i + 1}")] = dict(x=bx + x, y=by + y, rot=0, hide=True)
            col, row = G.DSUB_SPOT[grp]
            cx, cy = bx + col * G.DSUB_DX, G.DSUB_ROW_Y[row]
            P[G.sock_conn(bay, grp)] = dict(x=cx - DSUB_CENTRE_OFF[0], y=cy - DSUB_CENTRE_OFF[1], rot=DSUB_ROT, flip=True)
        for dn, dx, dy in G.DETECT:
            P[G.pin_ref(bay, dn)] = dict(x=bx + dx, y=by + dy, rot=0, hide=True)
        for ref, d, dx, dy in G.DOWELS:
            holes.append((f"{bay}{ref}", bx + dx, by + dy, d))
    for k, x in enumerate(G.MOUNT_X, 1):
        P[f"H{k}"] = dict(x=x, y=G.MOUNT_Y, rot=0, hide=True)
    for ref, x in G.JST_X.items():
        P[ref] = dict(x=x - JST_CENTRE_OFF[0], y=G.MOUNT_Y - JST_CENTRE_OFF[1], rot=0, flip=True)
    return P, holes


def cart_placements() -> tuple[dict, list]:
    P, holes = {}, []
    cx, cy = G.CART_DATUM
    for grp, gx in G.GROUPS:
        for i in range(N):
            x, y = G.field(gx, i)
            P[f"{grp}{i + 1}"] = dict(x=cx + x, y=cy + y, rot=0, flip=True, hide=True)
    for dn, dx, dy in G.DETECT:
        P[dn] = dict(x=cx + dx, y=cy + dy, rot=0, flip=True, hide=True)
    for ref, d, dx, dy in G.DOWELS:
        holes.append((f"K{ref[2:]}", cx + dx, cy + dy, d + G.HOLE_PLAY))
    for k, (x, y) in enumerate(G.CART_SCREWS, 1):
        holes.append((f"NH{k}", x, y, 2.7))
    return P, holes


def socket_silk(uid: K.Ids) -> str:
    o = K.gr_text(uid, "t1", "ROTOR SOCKET", G.SOCK_W / 2, 3.2, 0, "F.SilkS", 1.6, 0.25)
    for bay in G.BAYS:
        bx = G.BAY_X[bay]
        o += K.gr_text(uid, f"b{bay}", f"BAY {bay}", bx, G.BAY_YC - 47.5, 0, "F.SilkS", 1.5, 0.22)
        o += K.gr_text(uid, f"a{bay}", "A", bx - 6.0, G.BAY_YC - 31.75, 0, "F.SilkS", 1.0, 0.15)
        o += K.gr_text(uid, f"z{bay}", "Z", bx - 6.0, G.BAY_YC + 31.75, 0, "F.SilkS", 1.0, 0.15)
        for grp, gx in G.GROUPS:
            col, row = G.DSUB_SPOT[grp]
            o += K.gr_text(uid, f"g{bay}{grp}", f"{G.sock_conn(bay, grp)} {grp}", bx + col * G.DSUB_DX,
                           G.DSUB_ROW_Y[row], 90, "B.SilkS", 1.2, 0.18, mirror=True)
    o += K.gr_text(uid, "t2", "FRONT: PINS. BACK: LEADS FROM THE UNIT", G.SOCK_W / 2, G.SOCK_H - 1.8, 0, "F.SilkS", 1.0, 0.15)
    return o


def cart_silk(uid: K.Ids, name: str) -> str:
    w = CC.table("rotor", name)
    cx = G.CART_W / 2
    o = K.gr_text(uid, "t1", f"ROTOR {name}", cx, 11.0, 0, "F.SilkS", 3.0, 0.45)
    o += K.gr_text(uid, "t2", "THIS END UP", cx, 106.0, 0, "F.SilkS", 1.2, 0.18)
    for i in range(N):      # the wiring as a column, row A at the top like the pads behind it
        o += K.gr_text(uid, f"w{i}", f"{CC.L(i)}-{CC.L(w[i])}", cx + 11.0, G.CART_DATUM[1] + (i - 12.5) * G.PITCH, 0,
                       "F.SilkS", 1.0, 0.15)
    o += K.gr_text(uid, "t3", "PADS ON THE BACK", cx, G.CART_H - 2.0, 0, "B.SilkS", 1.0, 0.15, mirror=True)
    return o


def build(proj: str, nl: K.Netlist, name: str | None) -> str:
    uid = K.Ids(UID_NS)
    if proj == "rotor_socket":
        P, holes = socket_placements()
        extra = socket_silk(uid) + K.edge_rect(uid, G.SOCK_W, G.SOCK_H)
        title = "Relay rotor socket board: three cartridge bays of one unit"
        comments = ["Generated by gen_cartridge_pcb.py - placement only, routing by hand",
                    "Front: spring pins of bays L M R; back: 12 D-sub 37 for the leads; dowel holes NPTH 4.0 / 3.0"]
    else:
        P, holes = cart_placements()
        extra = cart_silk(uid, name) + K.edge_rect(uid, G.CART_W, G.CART_H)
        title = f"Relay rotor cartridge: rotor {name}"
        comments = ["Generated by gen_cartridge_pcb.py - placement only, routing by hand",
                    "Pads on the back side (hard gold), keying holes 4.05 / 3.05; housing screws 2.7"]
    return K.build_pcb("gen_cartridge_pcb.py", title, "A4", comments, nl, P, lib(), uid, holes=holes, extra=extra,
                       four_layers=True)


def projects() -> list[tuple[str, str | None]]:
    return [("rotor_socket", None)] + G.cart_projects()


def main():
    for proj, name in projects():
        with tempfile.TemporaryDirectory() as td:
            nl = G.export_netlist(HERE / f"{proj}.kicad_sch", Path(td))
        (HERE / f"{proj}.kicad_pcb").write_text(build(proj, nl, name), encoding="utf8", newline="\n")
        K.merge_board_rules(HERE / f"{proj}.kicad_pro", track=0.2, clear=0.15, via_d=0.5, via_drill=0.25)
        print(f"wrote {proj}.kicad_pcb")


if __name__ == "__main__":
    main()
