"""Mechanical specification of the offset dial (README section 4): the one place where the rotor (wiper comb) and the
detent are described in numbers.  The generator draws the code tracks under these comb tips; the verifier models the
rotor from these numbers and reads everything else (copper, nets, letters) from the generated board.

Angles: visitor's view (the B side of the stator board), clockwise from 12 o'clock; position p = letter A + p sits at
p * ALPHA.  The board file is drawn from the F side, so a visitor angle phi is the point (-R sin phi, -R cos phi)
relative to the dial centre in KiCad coordinates (x right, y down).
"""
from __future__ import annotations

import math

N = 26
ALPHA = 360.0 / N                 # detent pitch 13.846 deg
CENTER = (29.0, 29.0)             # dial centre on the 116 x 58 board
BOARD = (116.0, 58.0)

# rotor: one wiper comb (a single conductor = COM bridge), 11 tips on one radial line, aligned with the knob pointer
R0, PITCH, NTIPS = 9.0, 1.7, 11   # tip radii R0 + j * PITCH (j = 0: COM ring)
TIP_R = 0.30                      # contact patch of a tip (dimple, conservative radius used by the verifier)

# stator copper (the generator's rules, checked by the verifier against the tolerances below)
TRACK_W = 1.2                     # ring width -> radial gap PITCH - TRACK_W = 0.5 mm between rings
SLOT = 0.5                        # slot between a code arc and the next arc (different net / dummy island), mm
SHAFT_HOLE = 10.2                 # NPTH for the front bushing (M10 x 0.75, 6 mm shaft)

# tolerances the code must survive at every detent (README 4.3)
DET_TOL_DEG = 3.0                 # angular error of the rotor at rest (detent, hub, comb, knob set screw)
RAD_TOL = 0.3                     # radial error of a tip (eccentricity of shaft / board / comb)

# visitor-side scale
R_SCALE, LETTER_H = 24.0, 2.6     # letter centres, letter height
R_TICK = (20.6, 21.8)             # tick marks between knob skirt (r = 20) and the letters
KNOB_D = 40.0


def track_radius(j: int) -> float:
    return R0 + PITCH * j


def xy(phi_deg: float, r: float, center=CENTER) -> tuple[float, float]:
    """Board (KiCad, F-side view) coordinates of visitor angle phi (clockwise from 12 o'clock) at radius r."""
    a = math.radians(phi_deg)
    return center[0] - r * math.sin(a), center[1] - r * math.cos(a)
