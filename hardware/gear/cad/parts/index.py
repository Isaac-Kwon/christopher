"""Index disc with 3 dowel pins (drum side) and the V-groove flange (shaft side)  (D2)."""
import math
from functools import lru_cache
import cadquery as cq
import params as P
from ._util import V, cylz, rotz, polar
from .drum import pad_angle


@lru_cache(None)
def index_disc():
    """O34 x 3 disc on the pad face (Z 24 ... 27) fused with the three O3 x 6 pins (half buried,
    tilted by atan(pi/26) = 6.9 deg, centre line 1.5*sqrt2 above the groove floor line)."""
    z0, z1 = P.Z_PAD - P.INDEX_DISC_T, P.Z_PAD
    s = cylz(P.INDEX_DISC_D / 2, z0, z1)
    cuts = [cylz(P.PCB_BORE_D / 2, z0 - 1, z1 + 1)]
    for k in range(4):
        x, y = polar(P.PCB_SCREW_R, 45 + 90 * k)
        cuts.append(cylz(3.4 / 2, z0 - 1, z1 + 1, x, y))        # clearance holes for M3 (L5.1 step 1)
    s = s.cut(*cuts)
    rc = 0.5 * sum(P.INDEX_BAND)

    def zc(r):   # pin axis parallel to the groove floor line, 2.12 above it
        return P.FLANGE_Z1 - P.GROOVE_SLOPE * r + P.PIN_CENTRE_OVER_FLOOR
    t = math.atan(P.GROOVE_SLOPE)
    half = P.INDEX_PIN_L / 2
    pins = []
    for idx in P.INDEX_PIN_IDX:
        a = V(rc - half * math.cos(t), 0, zc(rc) + half * math.sin(t))
        b = V(rc + half * math.cos(t), 0, zc(rc) - half * math.sin(t))
        pin = cq.Solid.makeCylinder(P.INDEX_PIN_D / 2, (b - a).Length, a, (b - a).normalized())
        pins.append(rotz(pin, pad_angle(idx)))
    return s.fuse(*pins).clean()


def _groove_cutter():
    r1, r2 = P.GROOVE_R_CUT
    m = 0.6

    def tri(r):
        d = P.groove_depth(r)
        return [(-(d + m), m + P.FLANGE_Z1), (d + m, m + P.FLANGE_Z1), (0.0, -d + P.FLANGE_Z1)]
    wp = (cq.Workplane("YZ", origin=(r1, 0, 0)).polyline(tri(r1)).close()
          .workplane(offset=r2 - r1).polyline(tri(r2)).close().loft(ruled=True))
    return wp.val()


@lru_cache(None)
def index_flange():
    """O34 x 5.6 flange (Z 18 ... 23.6), O8 bore, 26 radial 90-deg V grooves, depth pi*r/26."""
    s = cylz(P.FLANGE_D / 2, P.FLANGE_Z0, P.FLANGE_Z1)
    s = s.cut(cylz(P.SHAFT_D / 2, P.FLANGE_Z0 - 1, P.FLANGE_Z1 + 1))
    cutter = _groove_cutter()
    cutters = [rotz(cutter, pad_angle(k)) for k in range(P.N_CH)]
    return s.cut(*cutters)
