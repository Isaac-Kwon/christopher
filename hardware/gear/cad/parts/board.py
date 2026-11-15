"""Contact board with 104 Mill-Max Omniball envelopes, board posts  (L3.4, D3)."""
from functools import lru_cache
import cadquery as cq
import params as P
from ._util import V, cylz, box, comp, polar
from .drum import pad_angle


def contact_positions():
    """104 contact centres on the 4 rings, same angular grid as the pads (26 positions j)."""
    pts = []
    for ring, r in enumerate(P.RINGS_R):
        for j in range(P.N_CH):
            x, y = polar(r, pad_angle(j))
            pts.append((ring, j, x, y))
    return pts


@lru_cache(None)
def contact_board():
    z0, z1 = P.BOARD_Z0, P.BOARD_Z1
    h = P.BOARD_W / 2
    s = box(-h, h, -h, h, z0, z1)
    cuts = [cylz(P.BOARD_HOLE_D / 2, z0 - 1, z1 + 1)]
    for _, _, x, y in contact_positions():
        cuts.append(cylz(P.C_BOARD_HOLE_D / 2, z0 - 1, z1 + 1, x, y))
    return s.cut(*cuts)


@lru_cache(None)
def contact_pins():
    """104 spring-contact envelopes compressed to the working height 9.0 (ball top at the pad plane).
    Mill-Max 7845-0: O4.37 body, O3.2 plunger, O3.96 ball, 2.2 tail behind the board."""
    z_top = P.BOARD_Z1 + P.C_WORK_H            # 27.0
    zc_ball = z_top - P.C_BALL_D / 2
    z_body_top = P.BOARD_Z1 + 5.5
    body = cylz(P.C_BODY_D / 2, P.BOARD_Z0, z_body_top)
    plunger = cylz(P.C_PLUNGER_D / 2, z_body_top, zc_ball)
    ball = cq.Solid.makeSphere(P.C_BALL_D / 2, V(0, 0, zc_ball), V(0, 0, 1), -90, 90, 360)
    tail = cylz(P.C_TAIL_D / 2, P.BOARD_Z0 - P.C_TAIL_L, P.BOARD_Z0)
    pin = body.fuse(plunger, ball, tail).clean()
    return comp([pin.translate(V(x, y, 0)) for _, _, x, y in contact_positions()])


@lru_cache(None)
def board_posts():
    """4 posts O6 x 16.4 from the front plate (Z 0) to the board rear face, at (+-48, +-48)."""
    out = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            out.append(cylz(P.BOARD_POST_D / 2, 0.0, P.BOARD_POST_LEN,
                            sx * P.BOARD_POST_XY, sy * P.BOARD_POST_XY))
    return comp(out)
