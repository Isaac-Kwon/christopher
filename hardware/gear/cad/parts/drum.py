"""Drum: PCB disc, pads, body + grip, bayonet knob (L3.1, D1, PCB README)."""
from functools import lru_cache
import params as P
from ._util import cylz, box, sector, rotz, comp, polar


def pad_angle(i):
    """Angle (deg, CCW seen from +Z) of pad / contact index i (letters run CCW seen from the visitor)."""
    return P.LETTER_A_DEG + i * P.CH_STEP


@lru_cache(None)
def drum_pcb():
    z0, z1 = P.Z_PAD, P.Z_PAD + P.PCB_T
    s = cylz(P.PCB_D / 2, z0, z1)
    cuts = [cylz(P.PCB_BORE_D / 2, z0 - 1, z1 + 1)]
    for k in range(4):
        x, y = polar(P.PCB_SCREW_R, 45 + 90 * k)
        cuts.append(cylz(P.PCB_SCREW_D / 2, z0 - 1, z1 + 1, x, y))
    x, y = polar(P.PCB_SCREW_R, P.LETTER_A_DEG)
    cuts.append(cylz(P.PCB_DOWEL_D / 2, z0 - 1, z1 + 1, x, y))
    x, y = polar(P.PCB_D / 2, P.LETTER_A_DEG - P.CH_STEP / 2)      # between Z and A
    cuts.append(cylz(P.PCB_NOTCH_R, z0 - 1, z1 + 1, x, y))
    return s.cut(*cuts)


@lru_cache(None)
def drum_pads():
    """104 gold pads (4 rings x 26), embedded flush with the pad face (Z 27 ... 27.035)."""
    z0 = P.Z_PAD
    out = []
    for r in P.RINGS_R:
        w = P.ring_pad_tang(r)
        pad = box(r - P.PAD_RADIAL / 2, r + P.PAD_RADIAL / 2, -w / 2, w / 2, z0, z0 + P.PAD_T)
        for i in range(P.N_CH):
            out.append(rotz(pad, pad_angle(i)))
    return comp(out)


@lru_cache(None)
def drum_body():
    """Rear skirt O100 x 10 with the O96.3 PCB seat, grip O70 x 18, bush bore."""
    z0 = P.Z_PAD
    z_r = z0 + P.BODY_REAR_L
    z_g = z_r + P.GRIP_L
    s = cylz(P.BODY_REAR_D / 2, z0, z_r).fuse(cylz(P.GRIP_D / 2, z_r, z_g))
    s = s.cut(cylz(P.BODY_POCKET_D / 2, z0 - 1, z0 + P.PCB_T))
    s = s.cut(cylz(P.BUSH_ID / 2, z0 + P.PCB_T - 0.5, z_g + 1))
    return s


@lru_cache(None)
def drum_knob():
    """Bayonet knob O32 x 14 with cam tracks (funnel +-40, 1.8 / 90 deg ramp, end pocket)."""
    z0 = P.Z_PAD + P.DRUM_BODY_L
    z1 = z0 + P.KNOB_L
    s = cylz(P.KNOB_D / 2, z0, z1)
    cuts = [cylz(P.KNOB_BORE_D / 2, z0 - 1, z1 - 2)]
    zp = P.XPIN_Z
    n = 6
    for side in (0.0, 180.0):
        for j in range(n + 1):
            a = -P.CAM_ANGLE_RAMP + j * P.CAM_ANGLE_RAMP / n
            zc = zp - P.CAM_LIFT + P.CAM_LIFT * j / n
            sec = sector(P.CAM_R[0], P.CAM_R[1], a - P.CAM_POCKET_HALF, a + P.CAM_POCKET_HALF,
                         zc - P.CAM_W / 2, zc + P.CAM_W / 2)
            cuts.append(rotz(sec, side))
        z_top = zp - P.CAM_LIFT + P.CAM_W / 2
        a0 = -P.CAM_ANGLE_RAMP
        fun = sector(P.CAM_R[0], P.CAM_R[1], a0 - P.CAM_FUNNEL, a0 + P.CAM_FUNNEL, z0 - 1, z_top)
        cuts.append(rotz(fun, side))
    return s.cut(*cuts)
