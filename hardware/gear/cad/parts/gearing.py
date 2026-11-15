"""Cycle-shaft gearing envelopes: 26:30 m1.5 train, single-tooth carry discs with locking arc,
lock discs with 26 pockets, position discs, slot disc  (L3.7, L3.8, D8).

Gears are modelled at the pitch circle (tangent pairs touch, zero volume); no involute teeth.
Discs are built with their axis on +Z starting at z = 0 and placed with `along_x`.
"""
import math
from functools import lru_cache
import cadquery as cq
import params as P
from ._util import V, cylz, tubez, sector, rotz, polar, comp, along_x

BORE = P.LINE_D / 2


def gear_env(pitch_r, width, bore=BORE):
    """Spur-gear pitch-circle envelope, axis +Z, z = 0 ... width."""
    return tubez(pitch_r, bore, 0.0, width)


def lock_arc_radius(centre_dist, lock_r):
    """Radius of the convex locking arc on the driver: centre distance - lock disc radius - gap (D8)."""
    return centre_dist - lock_r - P.LOCK_GAP


def tooth_disc(r_arc, r_tip, tooth_deg, width, tooth_at=90.0):
    """Driver disc of one-tooth gear: body of the arc radius plus a single tooth sector (pitch-circle
    envelope up to r_tip) at angle tooth_at (deg).  Static pose: tooth points away from the mating shaft."""
    body = cylz(r_arc, 0.0, width)
    tooth = sector(r_arc - 0.5, r_tip, tooth_at - tooth_deg / 2, tooth_at + tooth_deg / 2, 0.0, width)
    return body.fuse(tooth).cut(cylz(BORE, -1, width + 1)).clean()


def arc_disc(r_arc, width):
    return tubez(r_arc, BORE, 0.0, width)


def lock_disc(r_l, pocket_half, width):
    """Driven-side lock disc: radius R_L, 26 concave pockets (circular notches of half width pocket_half)."""
    s = tubez(r_l, BORE, 0.0, width)
    cuts = []
    for k in range(P.N_CH):
        x, y = polar(r_l, 360.0 / P.N_CH * k + 90.0)
        cuts.append(cylz(pocket_half, -1, width + 1, x, y))
    return s.cut(*cuts)


@lru_cache(None)
def carry1():
    """Carry 1 (C -> M): C carries the one-tooth disc (13T-equivalent pitch circle O26, m2) and the
    locking arc, M carries the 26T gear (O52) and the lock disc R27."""
    r_arc = lock_arc_radius(P.C_CM, P.LOCK_R1)
    tip = P.CARRY1_DRV_PD / 2 + P.CARRY1_M          # tooth tip radius of the 13T equivalent
    return {
        "drv_tooth": tooth_disc(r_arc, tip, 360.0 / (2 * P.CARRY1_TOOTH_EQ_T), P.GEAR_W),
        "drv_arc": arc_disc(r_arc, P.LOCK_W),
        "drn_gear": gear_env(P.CARRY1_M * P.CARRY1_DRIVEN_T / 2, P.GEAR_W),
        "drn_lock": lock_disc(P.LOCK_R1, P.LOCK_POCKET_HALF[0], P.LOCK_W),
        "r_arc": r_arc,
    }


@lru_cache(None)
def carry2():
    """Carry 2 (M -> S): M carries the O168 tooth disc (54T-equivalent, m3) and the locking arc,
    S carries the 26T gear (O78) and the lock disc R43."""
    r_arc = lock_arc_radius(P.C_MS, P.LOCK_R2)
    tip = P.CARRY2_M * P.CARRY2_TOOTH_EQ_T / 2 + P.CARRY2_M     # 84
    return {
        "drv_tooth": tooth_disc(r_arc, tip, 360.0 / (2 * P.CARRY2_TOOTH_EQ_T), P.GEAR_W),
        "drv_arc": arc_disc(r_arc, P.LOCK_W),
        "drn_gear": gear_env(P.CARRY2_M * P.CARRY2_DRIVEN_T / 2, P.GEAR_W),
        "drn_lock": lock_disc(P.LOCK_R2, P.LOCK_POCKET_HALF[1], P.LOCK_W),
        "r_arc": r_arc,
    }


@lru_cache(None)
def train_gears():
    """F (26T), idler I (26T), C (30T): module 1.5, pitch circles O39 / O39 / O45, width 15."""
    return {"F": gear_env(P.GEAR_M15 * P.T_F / 2, P.TRAIN_W), "I": gear_env(P.GEAR_M15 * P.T_I / 2, P.TRAIN_W),
            "C": gear_env(P.GEAR_M15 * P.T_C / 2, P.TRAIN_W)}


@lru_cache(None)
def position_disc():
    """Position disc O56 x 1.5 (spec O60, see params), 26 slots + 1 home slot, bore O20."""
    r = P.POSDISC_D / 2
    s = tubez(r, BORE, 0.0, P.POSDISC_T)
    cuts = []
    for k in range(P.N_CH):
        a = 360.0 / P.N_CH * k
        cuts.append(sector(r - 5.0, r - 1.5, a - 1.2, a + 1.2, -1, P.POSDISC_T + 1))
    cuts.append(sector(r - 8.0, r - 1.5, 360.0 / P.N_CH * 0.5 - 2.5, 360.0 / P.N_CH * 0.5 + 2.5, -1, P.POSDISC_T + 1))
    return s.cut(*cuts)


def slot_angles():
    """{track: (r, a_start, a_end)} for slot k = 0 in deg of cycle-shaft angle (from params / L3.8)."""
    return {n: P.track_deg(n) for n in P.TRACKS}


@lru_cache(None)
def slot_disc():
    """O160 x 1.5 slot disc with STROBE / JUDGE / CLEAR tracks (26 slots each, k = 26..29 empty)."""
    s = tubez(P.SLOTDISC_D / 2, 15.0, 0.0, P.SLOTDISC_T)
    cuts = []
    for name in P.TRACKS:
        r, a0, a1 = P.track_deg(name)
        base = sector(r - P.SLOT_RADIAL_W / 2, r + P.SLOT_RADIAL_W / 2, a0, a1, -1, P.SLOTDISC_T + 1)
        for k in range(P.N_SLOT_POS):
            cuts.append(rotz(base, 90.0 + P.STEP_DEG * k))
    return s.cut(*cuts)


@lru_cache(None)
def slot_disc_hub():
    return tubez(15.0, BORE, 0.0, P.U["disc_hub"][1] - P.U["disc_hub"][0])
