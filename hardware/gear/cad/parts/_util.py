"""Small CadQuery helpers shared by the part modules."""
import math
import cadquery as cq

V = cq.Vector
O = V(0, 0, 0)
ZAX = V(0, 0, 1)


def cyl(r, p0, p1):
    """Solid cylinder of radius r between points p0 and p1."""
    a, b = V(*p0), V(*p1)
    d = b - a
    return cq.Solid.makeCylinder(r, d.Length, a, d.normalized())


def tube(ro, ri, p0, p1, ext=1.0):
    """Hollow cylinder; the bore tool is extended by ext at both ends."""
    a, b = V(*p0), V(*p1)
    d = (b - a).normalized()
    outer = cyl(ro, p0, p1)
    inner = cyl(ri, tuple(a - d * ext), tuple(b + d * ext))
    return outer.cut(inner)


def cylz(r, z0, z1, x=0.0, y=0.0):
    return cyl(r, (x, y, z0), (x, y, z1))


def tubez(ro, ri, z0, z1, x=0.0, y=0.0):
    return tube(ro, ri, (x, y, z0), (x, y, z1))


def box(x0, x1, y0, y1, z0, z1):
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def sector(r1, r2, a0, a1, z0, z1):
    """Annular sector, angles a0..a1 (deg, CCW from +X), extruded between z0 and z1."""
    am = 0.5 * (a0 + a1)

    def p(r, a):
        return (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))

    wp = (cq.Workplane("XY", origin=(0, 0, z0)).moveTo(*p(r1, a0)).lineTo(*p(r2, a0))
          .threePointArc(p(r2, am), p(r2, a1)).lineTo(*p(r1, a1))
          .threePointArc(p(r1, am), p(r1, a0)).close().extrude(z1 - z0))
    return wp.val()


def rotz(shape, deg):
    return shape.rotate(O, ZAX, deg)


def comp(shapes):
    return cq.Compound.makeCompound(list(shapes))


def move(shape, x=0.0, y=0.0, z=0.0):
    return shape.translate(V(x, y, z))


def along_x(shape, x, y, z):
    """Part built with its axis on +Z (z = 0 at its start): put the axis on +X starting at X = x."""
    return shape.rotate(O, V(0, 1, 0), 90).translate(V(x, y, z))


def polar(r, deg):
    return r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg))
