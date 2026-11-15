"""Motor (NEMA 34 envelope), HTD 5M pulleys and belt, torque limiter, sensor fork  (D5, L3.7, L3.8)."""
import math
from functools import lru_cache
import cadquery as cq
import params as P
from ._util import V, box, cyl, tube, comp


def motor_axis_yz():
    f = P.SHAFTS["F"]
    return f + P.MOTOR_AXIS_DY, P.Z_LINE + P.MOTOR_AXIS_DZ


def motor_body(x_face):
    """86 x 86 x 147 body, mounting face at x_face (towards the plate), extending to smaller X."""
    ym, zm = motor_axis_yz()
    h = P.MOTOR_BODY / 2
    return box(x_face - P.MOTOR_LEN, x_face, ym - h, ym + h, zm - h, zm + h)


def motor_shaft(x0, x1):
    ym, zm = motor_axis_yz()
    return cyl(P.MOTOR_SHAFT_D / 2, (x0, ym, zm), (x1, ym, zm))


def pulley_radius(teeth):
    return teeth * P.HTD_PITCH / math.pi / 2 - P.PULLEY_OD_TRIM


def small_pulley(x0, x1):
    ym, zm = motor_axis_yz()
    return tube(pulley_radius(P.HTD_T_SMALL), P.MOTOR_SHAFT_D / 2, (x0, ym, zm), (x1, ym, zm))


def large_pulley(x0, x1):
    return tube(pulley_radius(P.HTD_T_LARGE), P.LINE_D / 2, (x0, P.SHAFTS["F"], P.Z_LINE), (x1, P.SHAFTS["F"], P.Z_LINE))


def torque_limiter(x0, x1):
    return tube(P.LIMITER_R, P.LINE_D / 2, (x0, P.SHAFTS["F"], P.Z_LINE), (x1, P.SHAFTS["F"], P.Z_LINE))


def _hull(x0, width, c1, r1, c2, r2):
    """Convex hull (tangent belt path) of two circles (centres c = (y, z)) extruded along +X."""
    (y1, z1), (y2, z2) = c1, c2
    dx, dy = y2 - y1, z2 - z1
    d = math.hypot(dx, dy)
    u = (dx / d, dy / d)
    n = (-u[1], u[0])
    sa = (r2 - r1) / d
    ca = math.sqrt(1 - sa * sa)
    nu = [(-sa * u[0] + s * ca * n[0], -sa * u[1] + s * ca * n[1]) for s in (1, -1)]
    p = lambda c, r, v: (c[0] + r * v[0], c[1] + r * v[1])
    t1a, t2a = p(c1, r1, nu[0]), p(c2, r2, nu[0])
    t2b, t1b = p(c2, r2, nu[1]), p(c1, r1, nu[1])
    m2, m1 = p(c2, r2, u), p(c1, r1, (-u[0], -u[1]))
    wp = (cq.Workplane("YZ", origin=(x0, 0, 0)).moveTo(*t1a).lineTo(*t2a).threePointArc(m2, t2b)
          .lineTo(*t1b).threePointArc(m1, t1a).close().extrude(width))
    return wp.val()


def belt(x0, x1):
    """HTD 5M belt envelope: shell of thickness 3.6 around the tangent path of the two pulleys."""
    ym, zm = motor_axis_yz()
    c1, c2 = (ym, zm), (P.SHAFTS["F"], P.Z_LINE)
    r1, r2 = pulley_radius(P.HTD_T_SMALL), pulley_radius(P.HTD_T_LARGE)
    w = x1 - x0
    outer = _hull(x0, w, c1, r1 + P.BELT_T, c2, r2 + P.BELT_T)
    inner = _hull(x0 - 1, w + 2, c1, r1, c2, r2)
    return outer.cut(inner)


def belt_length():
    """Belt pitch length from the pulley pitch circles and the real centre distance (D5 formula)."""
    ym, zm = motor_axis_yz()
    c = math.hypot(P.SHAFTS["F"] - ym, P.Z_LINE - zm)
    d1, d2 = P.PULLEY_PD
    return 2 * c + math.pi * (d1 + d2) / 2 + (d2 - d1) ** 2 / (4 * c)


def sensor_fork(x0, x1, y, z, direction):
    """U-fork envelope of the 3 radial slot sensors: two prongs astride the disc and a bridge outside
    the rim; placed on the disc radius pointing along `direction` ('+z')."""
    gap0 = P.DRIVE_X0 + 0.5 * (P.U["slotdisc"][0] + P.U["slotdisc"][1]) - 2.5   # 5 mm gap centred on the disc
    gap1 = gap0 + 5.0
    r0, r1 = P.SENSOR_R
    bridge_r0 = P.SLOTDISC_D / 2 + 1.0
    w = 30.0                                  # tangential width of the sensor stack
    if direction != "+z":
        raise ValueError("only +z fork is modelled")
    prong_a = box(x0, gap0, y - w / 2, y + w / 2, z + r0, z + bridge_r0 + 4.0)
    prong_b = box(gap1, x1, y - w / 2, y + w / 2, z + r0, z + bridge_r0 + 4.0)
    bridge = box(x0, x1, y - w / 2, y + w / 2, z + bridge_r0, z + bridge_r0 + 4.0)
    return prong_a.fuse(prong_b, bridge).clean()
