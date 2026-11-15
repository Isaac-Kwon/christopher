"""Drum shaft, sleeve, shim, bearings, drum-side and line-side mitre gear envelopes (L3.2, L3.3, D1, D7)."""
from functools import lru_cache
import cadquery as cq
import params as P
from ._util import V, cyl, cylz, tubez, tube


@lru_cache(None)
def drum_shaft():
    """O8 ground rod Z -95 ... +50, O6 collar to +63, O3 x 12 cross pin (bayonet pin) at Z +61."""
    s = cylz(P.SHAFT_D / 2, P.SHAFT_Z[0], P.COLLAR_Z[0])
    s = s.fuse(cylz(P.COLLAR_D / 2, P.COLLAR_Z[0], P.COLLAR_Z[1]))
    pin = cyl(P.XPIN_D / 2, (-P.XPIN_L / 2, 0, P.XPIN_Z), (P.XPIN_L / 2, 0, P.XPIN_Z))
    return s.fuse(pin).clean()


@lru_cache(None)
def sleeve():
    return tubez(P.SLEEVE_OD / 2, P.SHAFT_D / 2, *P.SLEEVE_Z)


@lru_cache(None)
def shim():
    z1 = P.Z_PLATE_R[0]
    return tubez(P.SLEEVE_OD / 2, P.SHAFT_D / 2, z1 - P.SHIM_T, z1)


def bearing_608(z0):
    """608ZZ envelope (8 x 22 x 7), rear face at z0."""
    return tubez(P.BRG608[1] / 2, P.BRG608[0] / 2, z0, z0 + P.BRG608[2])


def bearing_6004(p0, p1):
    return tube(P.BRG6004[1] / 2, P.BRG6004[0] / 2, p0, p1)


def _mitre_pieces():
    """45-deg mitre, axis +Z, apex at the origin; teeth between d = 16.5 and 25 (pitch-cone envelope),
    hub R20 from d = 25 to 40 (mounting distance)."""
    d_big = P.MITRE_PD / 2
    d_small = d_big - P.MITRE_FACE_AX
    hub = cyl(P.MITRE_HUB_D / 2, (0, 0, d_big), (0, 0, P.MITRE_MOUNT))
    cone = cq.Solid.makeCone(d_big, d_small, P.MITRE_FACE_AX, V(0, 0, d_big), V(0, 0, -1))
    return hub, cone


@lru_cache(None)
def drum_mitre():
    """Mitre on the drum shaft: apex at Z = -109 (line axis), hub back face at Z = -69."""
    hub, cone = _mitre_pieces()
    s = hub.fuse(cone)
    s = s.cut(cylz(P.SHAFT_D / 2, -10, P.MITRE_MOUNT + 10))
    return s.translate(V(0, 0, P.Z_LINE))


@lru_cache(None)
def line_mitre(sign):
    """Mitre on the line shaft (axis X), apex at (0, 0, Z_LINE); sign +1: hub towards +X."""
    hub, cone = _mitre_pieces()
    s = hub.fuse(cone)
    s = s.cut(cylz(P.LINE_D / 2, -10, P.MITRE_MOUNT + 10))
    s = s.rotate(V(0, 0, 0), V(0, 1, 0), 90 if sign > 0 else -90)
    return s.translate(V(0, 0, P.Z_LINE))
