"""Single source of dimensions for the gear-drum bombe CAD model (track G, item 8).

Units: mm and degrees.  Every value cites its origin:
  L<x.y>  = hardware/gear/layout.md section x.y
  D<n>    = docs/decisions/0002-gear-detail-decisions.md section n
  PCB     = hardware/gear/drum-pcb/README.md
  D1      = docs/decisions/0001-test-register-timing.md (via layout 3.8)
  (mod)   = a modelling choice made in this CAD (not a design value); see README.md
Tags (calc)/(est) of the source documents are not repeated.

Global frame: X = width (slot 1 at the left, seen by the visitor), Y = height from the
floor, Z = depth with Z = 0 on the front face of the column front plates (datum A, L header);
visitor side is +Z.
"""
import math

# ----------------------------------------------------------------------------------------
# Machine, frame, plinth  (L3.9, L3.11, D4, D12, D13)
# ----------------------------------------------------------------------------------------
N_SLOTS = 12                      # L3.9 (12-slot frame)
POPULATED = tuple(range(7, 13))   # D6: slots 7..12 are filled first
EMPTY = tuple(s for s in range(1, N_SLOTS + 1) if s not in POPULATED)
PITCH = 120.0                     # L3.6, D13: column pitch (also row pitch)
FRAME_END = 40.0                  # L3.9: 40 + 12*120 + 360 + 40
DRIVE_W = 360.0                   # D4: drive module width
BANK_W = FRAME_END + N_SLOTS * PITCH + DRIVE_W + FRAME_END     # = 1880  (L3.9, D13)
BANK_H = 480.0                    # L3.9
BANK_D = 330.0                    # L3.9
PLINTH_H = 400.0                  # D12
FLOOR_T = 50.0                    # L3.11 (floor 50 above plinth)
PANEL_H = 420.0                   # L3.10, D13
PANEL_W = 1800.0                  # D13
PANEL_PROTRUSION = 70.0           # L3.10 / L3.11 (depth 330 + 70 = 400)
BANK_Y0 = PLINTH_H + FLOOR_T + PANEL_H        # 870: bottom of the bank
BANK_Y1 = BANK_Y0 + BANK_H                    # 1350: top of the bank
ROW_REL = {"fast": 350.0, "mid": 230.0, "slow": 110.0}   # L3.9 row heights above bank floor
ROW_Y = {k: BANK_Y0 + v for k, v in ROW_REL.items()}     # 1220 / 1100 / 980  (D12)
ROW_ORDER = ("slow", "mid", "fast")           # bottom -> top
SLOT_X0 = FRAME_END                           # first slot starts here
DRIVE_X0 = SLOT_X0 + N_SLOTS * PITCH          # 1480: drive module start
ENVELOPE = (1880.0, 1350.0, 400.0)            # D12/D13/L3.11: W x H x D of the machine
ENVELOPE_TOL = 5.0                            # task item 4

Z_FRONT_FRAME = 100.0             # (mod) inner face of the front cover = L3.2 (+105...+100)
COVER_FRONT_T = 5.0               # L3.2 / L3.9: polycarbonate 5 t
Z_COVER_OUT = Z_FRONT_FRAME + COVER_FRONT_T   # +105
Z_REAR_COVER_IN = -225.0          # L3.2: rear cover at -225
FIXED_COVER_T = 3.0               # L3.9: fixed covers 3 t
FRAME_PROFILE = 40.0              # L4: aluminium profile 4040
REF_BAR_T = 12.0                  # L3.9 / L4: reference bar 12 x 50 flat (laid flat: 12 high, 50 deep) (mod)
REF_BAR_DEPTH = 50.0
MID_RAIL_Z = (-115.0, -75.0)      # (mod) cross rails behind the column rear plates
COVER_GAP = 2.0                   # (mod) gap between front cover and fixed trim, must be <= 4 (D10)
GUARD_GAP_MAX = 4.0               # D10
GUARD_TO_MOVING_MIN = 20.0        # D10

# Plinth: toe recess L: D12 (height 230, depth 150)
TOE_H, TOE_D = 230.0, 150.0

# ----------------------------------------------------------------------------------------
# Z stack of one drum station  (L3.2, D3)
# ----------------------------------------------------------------------------------------
Z_PAD = 27.0                      # pad plane
Z_KNOB_END = 69.0                 # knob front end (L3.2)
BOARD_Z0, BOARD_Z1 = 16.4, 18.0   # D3: contact board +18.0 ... +16.4
BOARD_POST_LEN = 16.4             # D3: board post length
PLATE_T = 8.0                     # L3.2 / L3.5: front/rear plate 8 t
Z_PLATE_F = (-PLATE_T, 0.0)       # front plate, datum A at Z = 0
Z_PLATE_R = (-66.0, -58.0)        # rear plate (L3.2: -58 ... -66)
Z_LINE = -109.0                   # L3.2: line shaft axis
PLATE_W, PLATE_H = 116.0, 400.0   # L3.5
SPACER_POST_D = 10.0              # (mod) 4 spacer posts between the plates (L3.5)
SPACER_POST_XY = (44.0, 60.0)     # (mod) x, y offsets of the 4 posts

# ----------------------------------------------------------------------------------------
# Drum, PCB, index  (L3.1, PCB, D1, D2)
# ----------------------------------------------------------------------------------------
N_CH = 26
CH_STEP = 360.0 / N_CH            # 13.846
LETTER_A_DEG = 90.0               # (mod) letter A / reference mark at 12 o'clock, letters CCW seen from +Z
PCB_D, PCB_T = 96.0, 1.6          # PCB
PCB_BORE_D = 8.6                  # PCB
PCB_SCREW_R, PCB_SCREW_D = 7.5, 3.2     # PCB: 4 x O3.2 at r 7.5, 45 + 90k deg
PCB_DOWEL_D = 2.0                 # PCB: O2 dowel hole at r 7.5 towards letter A
PCB_NOTCH_R = 1.2                 # PCB: rim notch R1.2 half way between Z and A
RINGS_R = (22.0, 29.0, 36.0, 43.0)      # PCB
PAD_RADIAL = 3.6                  # PCB
PAD_TANG_FRAC = 0.5               # PCB: tangential width = 50 % of ring pitch
PAD_T = 0.035                     # (mod) pad plating thickness, embedded flush with the pad face
BODY_REAR_D, BODY_REAR_L = 100.0, 10.0  # L3.1
BODY_POCKET_D = 96.3              # L3.1
GRIP_D, GRIP_L = 70.0, 18.0       # L3.1
KNOB_D, KNOB_L = 32.0, 14.0       # L3.1
BUSH_ID = 8.12                    # L3.1: bush O8 with 0.03-0.06 clearance -> 0.06 radial (mod)
DRUM_BODY_L = BODY_REAR_L + GRIP_L    # 28 (L3.1)
INDEX_DISC_D, INDEX_DISC_T = 34.0, 3.0  # L3.1 / D2
INDEX_PIN_D, INDEX_PIN_L = 3.0, 6.0     # D2: O3 h6 x 6
INDEX_PIN_IDX = (0, 9, 17)        # D2: 0, 9, 17 -> 0 / 124.6 / 235.4 deg
INDEX_BAND = (11.0, 16.5)         # D2: V-groove band r = 11-16.5
FLANGE_D, FLANGE_T = 34.0, 5.6    # D2: O34 x 5.6, Z +23.6 ... +18.0
FLANGE_Z0 = 18.0                  # D2
FLANGE_Z1 = FLANGE_Z0 + FLANGE_T  # 23.6
GROOVE_SLOPE = math.pi / N_CH     # D2: depth = pi r / 26  (floor line inclination atan = 6.9 deg)
GROOVE_R_CUT = (10.5, 17.5)       # (mod) cutter extends beyond the 11-16.5 band so that the
                                  # 6 mm pin (5.96 mm radial footprint at 6.9 deg) fits: OPEN ITEM
PIN_CENTRE_OVER_FLOOR = INDEX_PIN_D / 2 * math.sqrt(2)   # D2: 2.12 above the groove floor line
SHAFT_D = 8.0                     # L3.3
SHAFT_Z = (-95.0, 50.0)           # L3.2: shaft -95 ... +63
COLLAR_D, COLLAR_Z = 6.0, (50.0, 63.0)   # D1: O6 x 13 collar
XPIN_D, XPIN_L, XPIN_Z = 3.0, 12.0, 61.0 # D1: O3 cross pin, 3 mm protruding each side of O6 (Z mod)
SLEEVE_OD, SLEEVE_Z = 12.0, (0.0, 18.0)  # L3.2: sleeve 18 between front bearing and flange (OD mod)
SHIM_T = 3.0                      # L3.3: shim between rear bearing and hub (-66 ... -69)
BRG608 = (8.0, 22.0, 7.0)         # L3.3: 608ZZ  ID, OD, B
BRG6004 = (20.0, 42.0, 12.0)      # L3.6: 6004ZZ
# bayonet cam in the knob (D1)
KNOB_BORE_D = 8.6                 # (mod)
CAM_R = (4.0, 6.4)                # (mod) cam track radial extent (pin ends at r 6)
CAM_W = 3.4                       # (mod) axial width for the O3 pin
CAM_ANGLE_RAMP = 90.0             # D1: spiral 90 deg
CAM_LIFT = 1.8                    # D1: 1.8 / 90 deg
CAM_FUNNEL = 40.0                 # D1: funnel entry +-40 deg
CAM_POCKET_HALF = 22.0            # (mod) pocket half-angle (covers pin foot-print)

# ----------------------------------------------------------------------------------------
# Contact board and contacts  (L3.4, D3)
# ----------------------------------------------------------------------------------------
BOARD_W = 110.0                   # L3.4
BOARD_T = BOARD_Z1 - BOARD_Z0     # 1.6
BOARD_HOLE_D = 38.0               # L3.4 centre hole
BOARD_POST_XY = 48.0              # L3.4: posts at (+-48, +-48)
BOARD_POST_D = 6.0                # (mod)
C_BODY_D = 4.37                   # D3 / L3.4
C_BALL_D = 3.96                   # D3
C_FREE_H = 9.75                   # D3
C_WORK_H = 9.0                    # D3: working height 9.0 (compression 0.76)
C_STROKE = 1.52                   # D3
C_BOARD_HOLE_D = 4.40             # (mod)
C_TAIL_D, C_TAIL_L = 1.6, 2.2     # D3: tail 2.2 behind the board
C_PLUNGER_D = 3.2                 # (mod)

# ----------------------------------------------------------------------------------------
# Mitre gears, line shafts, brackets  (L3.3, L3.6, D6, D7)
# ----------------------------------------------------------------------------------------
MITRE_M, MITRE_Z = 2.0, 25        # D7: module 2, 25 teeth
MITRE_PD = MITRE_M * MITRE_Z      # 50
MITRE_HUB_D = 40.0                # D7
MITRE_MOUNT = 40.0                # D7: mounting distance (apex to hub back)
MITRE_FACE_AX = 8.5               # (mod) axial extent of the tooth cone: pitch R25 -> R16.5 (12 face, 45 deg)
LINE_D = 20.0                     # L3.6
LINE_SEG_A = (752.0, 1512.0)      # (mod) seg A: slots 7-12 (bracket 6|7 at 760 ... coupling in drive)
INCLUDE_LINE_B = False            # D6: segment B (slots 1-6) is added on expansion only
BRACKET_X = (760.0, 1000.0, 1240.0)   # D7: plate bracket every two columns on the slot boundaries
BRACKET_T = 16.0                  # D7: thickness <= 16 (+-8 around the boundary)
BRACKET_Z = (-143.0, -75.0)       # (mod)
COUPLING_OD, COUPLING_L = 40.0, 50.0  # D6

# ----------------------------------------------------------------------------------------
# Drive module (L3.7, D4, D5, D8)
# ----------------------------------------------------------------------------------------
D_CHAMBERS = {"bank": (7.0, 97.0), "plateA": (97.0, 105.0), "mid": (105.0, 255.0),
              "plateB": (255.0, 263.0), "outer": (263.0, 353.0)}   # L3.7: 90/8/150/8/90 (+7 margin)
# axis heights relative to M (L3.7): F +120, I +81, C +39, M 0, S -120
Y_M = ROW_Y["mid"]
SHAFTS = {"F": Y_M + 120.0, "I": Y_M + 81.0, "C": Y_M + 39.0, "M": Y_M, "S": Y_M - 120.0}
C_FI, C_IC, C_CM, C_MS = 39.0, 42.0, 39.0, 120.0   # L3.7 spec centre distances
GEAR_M15 = 1.5                    # L3.7
T_F, T_I, T_C = 26, 26, 30        # L3.7
CARRY1_M, CARRY1_DRIVEN_T = 2.0, 26   # L3.7: one tooth (13T equivalent) : 26T
CARRY1_DRV_PD, CARRY1_TOOTH_EQ_T = 26.0, 13
CARRY2_M, CARRY2_DRIVEN_T, CARRY2_TOOTH_EQ_T = 3.0, 26, 54   # L3.7
LOCK_R1, LOCK_R2 = 27.0, 43.0     # D8: lock disc radius R_L (carry 1 / 2)
LOCK_GAP = 0.05                   # D8: radial gap 0.05
LOCK_POCKET_HALF = (2.8, 4.4)     # D8: pocket half width
GEAR_W = 12.0                     # L3.7: tooth width 12 (steel)
LOCK_W = 10.0                     # L3.7: lock disc about 10
TRAIN_W = 15.0                    # D4: 26:30 train 15
POSDISC_D, POSDISC_T = 56.0, 1.5  # L3.8 says O60; O56 modelled because O60 touches I/C shaft (39 - 10 = 29 < 30): OPEN ITEM
POSDISC_D_SPEC = 60.0
# u coordinates (drive-local, X = DRIVE_X0 + u)
U = {
    "coupling": (8.0, 58.0),
    "carry2_lock": (62.0, 72.0), "carry2_tooth": (74.0, 86.0),
    "indicator": 150.0,
    "train": (195.0, 210.0),
    "carry1_tooth": (214.0, 226.0), "carry1_lock": (228.0, 238.0),
    "posdisc": (244.0, 245.5),
    "pulley": (270.0, 292.0), "belt": (273.5, 288.5), "limiter": (292.0, 322.0),
    "F_end": 324.0, "disc_hub": (332.0, 340.0), "slotdisc": (335.0, 336.5),
    "sensor": (329.5, 342.5), "C_end": 346.0, "motor_shaft_end": 353.0,
}
# motor and belt (D5)
HTD_T_SMALL, HTD_T_LARGE, HTD_PITCH = 18, 72, 5.0
PULLEY_PD = (HTD_T_SMALL * HTD_PITCH / math.pi, HTD_T_LARGE * HTD_PITCH / math.pi)   # 28.65 / 114.59
PULLEY_OD_TRIM = 0.6              # (mod) tip radius = pitch radius - 0.6
BELT_T, BELT_W = 3.6, 15.0        # (mod) HTD 5M belt thickness, D5: width 15
BELT_TEETH = 90                   # D5: 5M-450
MOTOR_AXIS_DZ = -70.0             # L3.7: 70 behind F
MOTOR_CENTRE_DIST = 103.6         # D5: C = 103.6
MOTOR_AXIS_DY = math.sqrt(MOTOR_CENTRE_DIST ** 2 - MOTOR_AXIS_DZ ** 2)   # 76.4 (L3.7: about 76)
MOTOR_BODY = 86.0                 # D5: NEMA 34, 86 square
MOTOR_LEN = 147.0                 # D5: Leadshine CS-M23485 length 147
MOTOR_SHAFT_D = 14.0              # D5
LIMITER_R = 32.0                  # (mod)
PLATE_A_Y = (900.0, 1300.0)       # (mod)
PLATE_A_Z = (-160.0, -PLATE_T)    # (mod)
PLATE_B_Y = (900.0, 1342.0)       # (mod) carries the motor flange
PLATE_B_Z = (-222.0, -PLATE_T)    # (mod) up to the motor rear face
INDICATOR_D, INDICATOR_L = 70.0, 25.0   # L3.9
INDICATOR_REAR_W = 70.0           # (mod) narrow rear plate (a 116 plate would hit plate A)
SENSOR_R = (48.0, 86.0)           # (mod) fork envelope
# slot disc (L3.8, D1)
SLOTDISC_D, SLOTDISC_T = 160.0, 1.5   # L3.8
CYCLE_N = 30                      # L3.8: 30 positions per revolution
STEP_DEG = 360.0 / CYCLE_N        # 12 deg
STEP_MS = 38.46                   # D1
TRACKS = {"STROBE": (72.0, (0.0, 6.0)), "JUDGE": (62.0, (12.0, 19.0)),
          "CLEAR": (52.0, (21.0, 33.0))}      # L3.8: radius, (t_start, t_end) in ms from STROBE start
T_CENTRE_MS = 3.0                 # L3.8: full contact -3 ... +9 ms (centre at +3 ms of the STROBE clock)
SLOT_RADIAL_W = 2.0               # (mod)
N_SLOT_POS = 26                   # L3.8: k = 0..25 (k = 26..29 empty)
# layout.md 3.8 table (deg) for check.py
TRACK_SPEC_DEG = {"STROBE": (-0.94, 0.94), "JUDGE": (2.81, 4.99), "CLEAR": (5.62, 9.36)}

# ----------------------------------------------------------------------------------------
# Derived helpers
# ----------------------------------------------------------------------------------------
def slot_x(slot):
    """Centre X of slot 1..12."""
    return SLOT_X0 + PITCH * (slot - 0.5)


def track_deg(name):
    r, (t0, t1) = TRACKS[name]
    k = STEP_DEG / STEP_MS
    return r, (t0 - T_CENTRE_MS) * k, (t1 - T_CENTRE_MS) * k


def groove_depth(r):
    return GROOVE_SLOPE * r


def ring_pad_tang(r):
    return PAD_TANG_FRAC * 2 * math.pi * r / N_CH

# thin-section bearing for the drive-module shafts: the 6004 (OD 42) does not fit between shafts 39 apart
BRG6804 = (20.0, 32.0, 7.0)       # (mod) 6804ZZ 20 x 32 x 7 in plates A/B: OPEN ITEM (L3.6 lists 6004 for line shafts only)
