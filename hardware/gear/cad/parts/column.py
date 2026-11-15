"""Column module frame: front/rear plates with bearing bores, spacer posts, blanking plate (L3.5, D10)."""
from functools import lru_cache
import params as P
from ._util import box, cylz, comp


def _bores(z0, z1):
    return [cylz(P.BRG608[1] / 2, z0 - 1, z1 + 1, 0.0, P.ROW_Y[r] - P.ROW_Y["mid"]) for r in P.ROW_ORDER]


@lru_cache(None)
def front_plate():
    """116 x 400 x 8 aluminium plate, Z -8 ... 0, centred on the middle row axis; 3 x O22 bearing bores."""
    z0, z1 = P.Z_PLATE_F
    s = box(-P.PLATE_W / 2, P.PLATE_W / 2, -P.PLATE_H / 2, P.PLATE_H / 2, z0, z1)
    return s.cut(*_bores(z0, z1))


@lru_cache(None)
def rear_plate():
    z0, z1 = P.Z_PLATE_R
    s = box(-P.PLATE_W / 2, P.PLATE_W / 2, -P.PLATE_H / 2, P.PLATE_H / 2, z0, z1)
    return s.cut(*_bores(z0, z1))


@lru_cache(None)
def spacer_posts():
    x, y = P.SPACER_POST_XY
    z0, z1 = P.Z_PLATE_R[1], P.Z_PLATE_F[0]
    return comp([cylz(P.SPACER_POST_D / 2, z0, z1, sx * x, sy * y) for sx in (-1, 1) for sy in (-1, 1)])


@lru_cache(None)
def blanking_plate():
    """116 x 400 x 3 aluminium blanking plate for an empty slot (D10 item 2), front plane."""
    return box(-P.PLATE_W / 2, P.PLATE_W / 2, -P.PLATE_H / 2, P.PLATE_H / 2, -P.FIXED_COVER_T, 0.0)
