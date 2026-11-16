"""Line shafts (O20), plate brackets with 6004 bearings, rigid couplings  (L3.6, D6, D7)."""
from functools import lru_cache
import params as P
from ._util import cyl, tube, box, cylz, comp
from .shaft import bearing_6004


def line_shaft(y, x0, x1):
    return cyl(P.LINE_D / 2, (x0, y, P.Z_LINE), (x1, y, P.Z_LINE))


def coupling(y, x0):
    """Split-clamp rigid coupling O40 x 50 (bore O20)."""
    return tube(P.COUPLING_OD / 2, P.LINE_D / 2, (x0, y, P.Z_LINE), (x0 + P.COUPLING_L, y, P.Z_LINE))


def f_brake(y, x0, x1):
    """Spring-applied (power-off) brake on the fast line shaft F: envelope O BRAKE_OD x (x1 - x0), bore BRAKE_BORE."""
    return tube(P.BRAKE_OD / 2, P.BRAKE_BORE / 2, (x0, y, P.Z_LINE), (x1, y, P.Z_LINE))


def bracket(xc):
    """Vertical plate bracket 16 t on a slot boundary, Y 910 ... 1307 (between the rails), Z -143 ... -75,
    with three O42 bearing bores (one per line shaft)."""
    y0, y1 = P.BANK_Y0 + P.FRAME_PROFILE, P.BANK_Y1 - P.FIXED_COVER_T - P.FRAME_PROFILE
    s = box(xc - P.BRACKET_T / 2, xc + P.BRACKET_T / 2, y0, y1, P.BRACKET_Z[0], P.BRACKET_Z[1])
    cuts = [cyl(P.BRG6004[1] / 2, (xc - P.BRACKET_T, P.ROW_Y[r], P.Z_LINE), (xc + P.BRACKET_T, P.ROW_Y[r], P.Z_LINE))
            for r in P.ROW_ORDER]
    return s.cut(*cuts)


def bracket_bearings(xc):
    h = P.BRG6004[2] / 2
    return comp([bearing_6004((xc - h, P.ROW_Y[r], P.Z_LINE), (xc + h, P.ROW_Y[r], P.Z_LINE))
                 for r in P.ROW_ORDER])
