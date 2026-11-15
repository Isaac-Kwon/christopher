"""Guards: transparent cover panels and fixed covers as thin plates (L3.9, D10, D11)."""
import params as P
from ._util import box, cyl

HOLE_D = 30.0        # (mod) crank access hole in the right cover / right rear post
SHUTTER_D = 28.0     # (mod) spring shutter: 1 mm radial gap to the hole (<= 4, D10/D11)


def crank_axis():
    """(Y, Z) of the motor axis = the crank access axis."""
    return P.SHAFTS["F"] + P.MOTOR_AXIS_DY, P.Z_LINE + P.MOTOR_AXIS_DZ


def guards():
    """Return {name: shape} (global coordinates)."""
    c, g = P.FIXED_COVER_T, P.COVER_GAP
    zf0, zf1 = P.Z_FRONT_FRAME, P.Z_COVER_OUT
    out = {}
    # front hinged cover: PC 5 t, 1820 x 440, X 30 ... 1850, Y 890 ... 1330
    out["cover_front"] = box(30.0, P.BANK_W - 30.0, P.BANK_Y0 + 20.0, P.BANK_Y1 - 20.0, zf0, zf1)
    # fixed front trim 3 t around it with a 2 mm gap
    t1 = zf0 + c
    out["trim_left"] = box(0.0, 30.0 - g, P.BANK_Y0, P.BANK_Y1, zf0, t1)
    out["trim_right"] = box(P.BANK_W - 30.0 + g, P.BANK_W, P.BANK_Y0, P.BANK_Y1, zf0, t1)
    out["trim_bottom"] = box(30.0 - g, P.BANK_W - 30.0 + g, P.BANK_Y0, P.BANK_Y0 + 20.0 - g, zf0, t1)
    out["trim_top"] = box(30.0 - g, P.BANK_W - 30.0 + g, P.BANK_Y1 - 20.0 + g, P.BANK_Y1, zf0, t1)
    # fixed covers (3 t): top, rear, left and right sides
    out["cover_top"] = box(c, P.BANK_W - c, P.BANK_Y1 - c, P.BANK_Y1, P.Z_REAR_COVER_IN, zf0)
    out["cover_rear"] = box(0.0, P.BANK_W, P.BANK_Y0, P.BANK_Y1, P.Z_REAR_COVER_IN - c, P.Z_REAR_COVER_IN)
    out["cover_left"] = box(0.0, c, P.BANK_Y0, P.BANK_Y1, P.Z_REAR_COVER_IN, zf0)
    ym, zm = crank_axis()
    right = box(P.BANK_W - c, P.BANK_W, P.BANK_Y0, P.BANK_Y1, P.Z_REAR_COVER_IN, zf0)
    right = right.cut(cyl(HOLE_D / 2, (P.BANK_W - c - 1, ym, zm), (P.BANK_W + 1, ym, zm)))
    out["cover_right"] = right
    out["shutter"] = cyl(SHUTTER_D / 2, (P.BANK_W - c, ym, zm), (P.BANK_W, ym, zm))
    return out
