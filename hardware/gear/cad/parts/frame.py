"""Bank frame (aluminium 4040 profiles and 12 x 50 reference bars as rectangles), plinth, panel (L3.9, L4, D12)."""
import params as P
from ._util import box, cyl


def bank_frame():
    """Return {name: shape} of the frame members (global coordinates)."""
    c = P.FIXED_COVER_T                       # side/top covers are recessed into the profile faces (mod)
    pr = P.FRAME_PROFILE
    y0, y1 = P.BANK_Y0, P.BANK_Y1 - c         # profile top face is recessed under the top cover
    xl = (c, pr)                              # left posts   X 3 ... 40
    xr = (P.BANK_W - pr, P.BANK_W - c)        # right posts  X 1840 ... 1877
    zf = (P.Z_FRONT_FRAME - pr, P.Z_FRONT_FRAME)           # front post 60 ... 100
    zr = (P.Z_REAR_COVER_IN, P.Z_REAR_COVER_IN + pr)       # rear post -225 ... -185
    out = {}
    for side, xs in (("L", xl), ("R", xr)):
        out[f"post_front_{side}"] = box(xs[0], xs[1], y0, y1, zf[0], zf[1])
        out[f"post_rear_{side}"] = box(xs[0], xs[1], y0, y1, zr[0], zr[1])
        out[f"rail_bot_side_{side}"] = box(xs[0], xs[1], y0, y0 + pr, zr[1], zf[0])
        out[f"rail_top_side_{side}"] = box(xs[0], xs[1], y1 - pr, y1, zr[1], zf[0])
    x_in0, x_in1 = xl[1], xr[0]               # 40 ... 1840
    xb = (x_in0, P.DRIVE_X0)                  # rails stop at the drive module (motor, plates) 40 ... 1480
    out["rail_top_front"] = box(x_in0, x_in1, y1 - pr, y1, zf[0], zf[1])
    out["rail_bot_front"] = box(x_in0, x_in1, y0, y0 + 30.0, zf[0], zf[1])      # 4040 trimmed to 30 high (plates pass over it)
    out["rail_top_mid"] = box(xb[0], xb[1], y1 - pr, y1, P.MID_RAIL_Z[0], P.MID_RAIL_Z[1])
    out["rail_bot_mid"] = box(xb[0], xb[1], y0, y0 + pr, P.MID_RAIL_Z[0], P.MID_RAIL_Z[1])
    ym, zm = P.SHAFTS["F"] + P.MOTOR_AXIS_DY, P.Z_LINE + P.MOTOR_AXIS_DZ
    out["post_rear_R"] = out["post_rear_R"].cut(cyl(15.0, (xr[0] - 1, ym, zm), (xr[1] + 1, ym, zm)))  # crank hole O30
    out["rail_top_rear"] = box(xb[0], xb[1], y1 - pr, y1, zr[0], zr[1])
    out["rail_bot_rear"] = box(xb[0], xb[1], y0, y0 + pr, zr[0], zr[1])
    # reference bars 12 x 50 laid flat under / over the plates of slots 1-12, Z +5 ... -45
    zb = (5.0 - P.REF_BAR_DEPTH, 5.0)
    out["refbar_bot"] = box(P.SLOT_X0, P.DRIVE_X0, 900.0 - P.REF_BAR_T, 900.0, zb[0], zb[1])
    out["refbar_top"] = box(P.SLOT_X0, P.DRIVE_X0, 1300.0, 1300.0 + P.REF_BAR_T, zb[0], zb[1])
    return out


def plinth():
    """Plinth 400 high (D12) with the toe recess 230 x 150 at the front, full machine width."""
    z0, z1 = P.Z_REAR_COVER_IN, P.Z_COVER_OUT + P.PANEL_PROTRUSION     # -225 ... +175
    s = box(0.0, P.BANK_W, 0.0, P.PLINTH_H, z0, z1)
    return s.cut(box(-1.0, P.BANK_W + 1.0, -1.0, P.TOE_H, z1 - P.TOE_D, z1 + 1.0))


def floor_slab():
    z0, z1 = P.Z_REAR_COVER_IN, P.Z_COVER_OUT + P.PANEL_PROTRUSION
    return box(0.0, P.BANK_W, P.PLINTH_H, P.PLINTH_H + P.FLOOR_T, z0, z1)


def panel_cabinet():
    """Common-part panel 1800 x 420, protruding 70 in front of the bank (Z +175 ... -225)."""
    z0, z1 = P.Z_REAR_COVER_IN, P.Z_COVER_OUT + P.PANEL_PROTRUSION
    x0 = (P.BANK_W - P.PANEL_W) / 2
    return box(x0, x0 + P.PANEL_W, P.PLINTH_H + P.FLOOR_T, P.BANK_Y0, z0, z1)
