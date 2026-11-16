"""Assembly of the parts into the column module, the drive module, the bank and the whole machine.

`Model` is a flat list of placed parts (name, shape, group, role, colour).  Roles: 'moving' (rotates
or slides in operation) and 'static'.  Groups name the subassembly for the STEP export.
"""
from dataclasses import dataclass, field
from typing import Any

import params as P
from parts import drum, index, shaft, board, column, line, gearing, motor, frame, guards
from parts._util import V, comp, cyl, tube, box, cylz, tubez, along_x

COLORS = {"alu": (0.75, 0.77, 0.80), "steel": (0.35, 0.37, 0.40), "brass": (0.85, 0.65, 0.20),
          "pcb": (0.10, 0.45, 0.25), "gold": (0.95, 0.80, 0.20), "black": (0.12, 0.12, 0.12),
          "pc": (0.7, 0.85, 0.95), "drum": (0.82, 0.30, 0.25), "motor": (0.20, 0.30, 0.55),
          "cover": (0.85, 0.88, 0.90), "belt": (0.08, 0.08, 0.08)}


@dataclass
class Part:
    name: str
    shape: Any
    group: str
    role: str
    color: str = "steel"


@dataclass
class Model:
    parts: list = field(default_factory=list)

    def add(self, name, shape, group, role, color="steel"):
        self.parts.append(Part(name, shape, group, role, color))
        return shape

    def groups(self):
        return sorted({p.group for p in self.parts})

    def select(self, *prefixes):
        return [p for p in self.parts if any(p.group.startswith(x) for x in prefixes)]


def at(shape, x, y, z=0.0):
    return shape.translate(V(x, y, z))


# ---------------------------------------------------------------------------------------------
def build_column(m, slot, grp=None):
    """Column module of slot `slot` (3 drums, 3 contact boards, plates, shafts, mitres)."""
    g = grp or f"column_{slot:02d}"
    x = P.slot_x(slot)
    ymid = P.ROW_Y["mid"]
    s = f"s{slot:02d}"
    m.add(f"{s}_plate_front", at(column.front_plate(), x, ymid), g, "static", "alu")
    m.add(f"{s}_plate_rear", at(column.rear_plate(), x, ymid), g, "static", "alu")
    m.add(f"{s}_spacer_posts", at(column.spacer_posts(), x, ymid), g, "static", "steel")
    for row in P.ROW_ORDER:
        y = P.ROW_Y[row]
        n = f"{s}_{row}"
        m.add(f"{n}_bearing_front", at(shaft.bearing_608(-P.BRG608[2]), x, y), g, "static", "steel")
        m.add(f"{n}_bearing_rear", at(shaft.bearing_608(P.Z_PLATE_R[0]), x, y), g, "static", "steel")
        m.add(f"{n}_shaft", at(shaft.drum_shaft(), x, y), g, "moving", "steel")
        m.add(f"{n}_sleeve", at(shaft.sleeve(), x, y), g, "moving", "steel")
        m.add(f"{n}_flange", at(index.index_flange(), x, y), g, "moving", "steel")
        m.add(f"{n}_shim", at(shaft.shim(), x, y), g, "moving", "steel")
        m.add(f"{n}_mitre_drum", at(shaft.drum_mitre(), x, y), g, "moving", "brass")
        m.add(f"{n}_board", at(board.contact_board(), x, y), g, "static", "pcb")
        m.add(f"{n}_contacts", at(board.contact_pins(), x, y), g, "static", "gold")
        m.add(f"{n}_board_posts", at(board.board_posts(), x, y), g, "static", "steel")
        m.add(f"{n}_drum_pcb", at(drum.drum_pcb(), x, y), g, "moving", "pcb")
        m.add(f"{n}_drum_pads", at(drum.drum_pads(), x, y), g, "moving", "gold")
        m.add(f"{n}_drum_body", at(drum.drum_body(), x, y), g, "moving", "drum")
        m.add(f"{n}_drum_knob", at(drum.drum_knob(), x, y), g, "moving", "black")
        m.add(f"{n}_index_disc", at(index.index_disc(), x, y), g, "moving", "alu")


def build_blank(m, slot):
    m.add(f"s{slot:02d}_blank", at(column.blanking_plate(), P.slot_x(slot), P.ROW_Y["mid"]), f"blank_{slot:02d}", "static", "alu")


# ---------------------------------------------------------------------------------------------
def build_line_shafts(m):
    """Line shafts, mitres, plate brackets for the populated slots (segment A, slots 7-12)."""
    g = "lines"
    for row in P.ROW_ORDER:
        y = P.ROW_Y[row]
        m.add(f"line_{row}_A", line.line_shaft(y, *P.LINE_SEG_A), g, "moving", "steel")
        if P.INCLUDE_LINE_B:
            m.add(f"line_{row}_B", line.line_shaft(y, P.SLOT_X0 - 20.0, P.BRACKET_X[0] + 16.0), g, "moving", "steel")
    for slot in P.POPULATED:
        x = P.slot_x(slot)
        for row in P.ROW_ORDER:
            sign = -1 if row == "mid" else 1               # D7: middle row on the opposite side
            m.add(f"s{slot:02d}_{row}_mitre_line", at(shaft.line_mitre(sign), x, P.ROW_Y[row]), g, "moving", "brass")
    for i, xb in enumerate(P.BRACKET_X):
        m.add(f"bracket_{i}", line.bracket(xb), g, "static", "alu")
        m.add(f"bracket_{i}_bearings", line.bracket_bearings(xb), g, "static", "steel")


# ---------------------------------------------------------------------------------------------
def build_drive(m):
    g = "drive"
    X0 = P.DRIVE_X0
    U = P.U
    Y = P.SHAFTS
    zl = P.Z_LINE
    ux = lambda u: X0 + u
    # --- plates
    ymid = P.ROW_Y["mid"]
    fp = box(X0, X0 + P.DRIVE_W, 900.0, 1300.0, -P.PLATE_T, 0.0)
    xi = X0 + U["indicator"]
    bores = [cylz(P.BRG608[1] / 2, -P.PLATE_T - 1, 1, xi, P.ROW_Y[r]) for r in P.ROW_ORDER]
    m.add("drive_plate_front", fp.cut(*bores), g, "static", "alu")
    ub = P.D_CHAMBERS
    brg = P.BRG6804
    pa0, pa1 = X0 + ub["plateA"][0], X0 + ub["plateA"][1]
    pb0, pb1 = X0 + ub["plateB"][0], X0 + ub["plateB"][1]
    plate_a = box(pa0, pa1, P.PLATE_A_Y[0], P.PLATE_A_Y[1], P.PLATE_A_Z[0], P.PLATE_A_Z[1])
    plate_b = box(pb0, pb1, P.PLATE_B_Y[0], P.PLATE_B_Y[1], P.PLATE_B_Z[0], P.PLATE_B_Z[1])
    cuts_a = [cyl(brg[1] / 2, (pa0 - 1, y, zl), (pa1 + 1, y, zl)) for y in Y.values()]
    ym_m, zm_m = motor.motor_axis_yz()
    cuts_b = [cyl(brg[1] / 2, (pb0 - 1, y, zl), (pb1 + 1, y, zl)) for y in Y.values()]
    cuts_b.append(cyl(15.0, (pb0 - 1, ym_m, zm_m), (pb1 + 1, ym_m, zm_m)))
    m.add("drive_plate_A", plate_a.cut(*cuts_a), g, "static", "alu")
    m.add("drive_plate_B", plate_b.cut(*cuts_b), g, "static", "alu")
    # --- shafts (O20) and their 6804 bearings in plates A and B
    ends = {"F": (34.0, U["F_end"]), "M": (34.0, 265.0), "S": (34.0, 265.0), "I": (93.0, 265.0), "C": (93.0, U["C_end"])}
    for k, (u0, u1) in ends.items():
        m.add(f"drive_shaft_{k}", line.line_shaft(Y[k], ux(u0), ux(u1)), g, "moving", "steel")
        for tag, p0, p1 in (("A", pa0, pa1), ("B", pb0, pb1)):
            c = 0.5 * (p0 + p1)
            h = brg[2] / 2
            m.add(f"drive_bearing_{k}_{tag}", tube(brg[1] / 2, brg[0] / 2, (c - h, Y[k], zl), (c + h, Y[k], zl)), g, "static", "steel")
    # --- couplings (F, M, S) in the bank-side chamber
    for k in ("F", "M", "S"):
        m.add(f"drive_coupling_{k}", line.coupling(Y[k], ux(U["coupling"][0])), g, "moving", "steel")
    # --- drum-change brake on F (rotor-cartridge.md 3.3), flanged to plate A
    m.add("drive_brake_F", line.f_brake(Y["F"], ux(P.BRAKE_U[0]), ux(P.BRAKE_U[1])), g, "static", "black")
    # --- 26:30 train (F, I, C)
    tg = gearing.train_gears()
    for k in ("F", "I", "C"):
        m.add(f"drive_train_{k}", along_x(tg[k], ux(U["train"][0]), Y[k], zl), g, "moving", "steel")
    # --- carry 1 (C -> M), carry 2 (M -> S)
    c1, c2 = gearing.carry1(), gearing.carry2()
    m.add("carry1_tooth_C", along_x(c1["drv_tooth"], ux(U["carry1_tooth"][0]), Y["C"], zl), g, "moving", "steel")
    m.add("carry1_arc_C", along_x(c1["drv_arc"], ux(U["carry1_lock"][0]), Y["C"], zl), g, "moving", "steel")
    m.add("carry1_gear_M", along_x(c1["drn_gear"], ux(U["carry1_tooth"][0]), Y["M"], zl), g, "moving", "steel")
    m.add("carry1_lock_M", along_x(c1["drn_lock"], ux(U["carry1_lock"][0]), Y["M"], zl), g, "moving", "steel")
    m.add("carry2_tooth_M", along_x(c2["drv_tooth"], ux(U["carry2_tooth"][0]), Y["M"], zl), g, "moving", "steel")
    m.add("carry2_arc_M", along_x(c2["drv_arc"], ux(U["carry2_lock"][0]), Y["M"], zl), g, "moving", "steel")
    m.add("carry2_gear_S", along_x(c2["drn_gear"], ux(U["carry2_tooth"][0]), Y["S"], zl), g, "moving", "steel")
    m.add("carry2_lock_S", along_x(c2["drn_lock"], ux(U["carry2_lock"][0]), Y["S"], zl), g, "moving", "steel")
    # --- position discs on F, M, S
    for k in ("F", "M", "S"):
        m.add(f"posdisc_{k}", along_x(gearing.position_disc(), ux(U["posdisc"][0]), Y[k], zl), g, "moving", "steel")
    # --- motor, pulleys, belt, torque limiter
    m.add("motor_body", motor.motor_body(pb0), g, "static", "motor")
    m.add("motor_shaft", motor.motor_shaft(pb0, ux(U["motor_shaft_end"])), g, "moving", "steel")
    m.add("pulley_small", motor.small_pulley(ux(U["pulley"][0]), ux(U["pulley"][1])), g, "moving", "alu")
    m.add("pulley_large", motor.large_pulley(ux(U["pulley"][0]), ux(U["pulley"][1])), g, "moving", "alu")
    m.add("belt", motor.belt(ux(U["belt"][0]), ux(U["belt"][1])), g, "moving", "belt")
    m.add("torque_limiter", motor.torque_limiter(ux(U["pulley"][1]), ux(U["limiter"][1])), g, "moving", "steel")
    # --- slot disc + hub + sensor fork on C
    m.add("slot_disc_hub", along_x(gearing.slot_disc_hub(), ux(U["disc_hub"][0]), Y["C"], zl), g, "moving", "steel")
    m.add("slot_disc", along_x(gearing.slot_disc(), ux(U["slotdisc"][0]), Y["C"], zl), g, "moving", "black")
    m.add("slot_sensors", motor.sensor_fork(ux(U["sensor"][0]), ux(U["sensor"][1]), Y["C"], zl, "+z"), g, "static", "black")
    # --- indicator column (3 fixed drums on shafts, narrow rear plate, mitres)
    ypl = (940.0, 1260.0)
    rp = box(xi - P.INDICATOR_REAR_W / 2, xi + P.INDICATOR_REAR_W / 2, ypl[0], ypl[1], P.Z_PLATE_R[0], P.Z_PLATE_R[1])
    rcuts = [cylz(P.BRG608[1] / 2, P.Z_PLATE_R[0] - 1, P.Z_PLATE_R[1] + 1, xi, P.ROW_Y[r]) for r in P.ROW_ORDER]
    m.add("indicator_plate_rear", rp.cut(*rcuts), g, "static", "alu")
    posts = comp([cylz(P.SPACER_POST_D / 2, P.Z_PLATE_R[1], -P.PLATE_T, xi + sx * 28.0, ymid + sy * 60.0)
                  for sx in (-1, 1) for sy in (-1, 1)])
    m.add("indicator_posts", posts, g, "static", "steel")
    for row in P.ROW_ORDER:
        y = P.ROW_Y[row]
        n = f"indicator_{row}"
        m.add(f"{n}_shaft", cylz(P.SHAFT_D / 2, P.SHAFT_Z[0], P.INDICATOR_L, xi, y), g, "moving", "steel")
        m.add(f"{n}_drum", tubez(P.INDICATOR_D / 2, P.SHAFT_D / 2, 0.0, P.INDICATOR_L, xi, y), g, "moving", "drum")
        m.add(f"{n}_bearing_front", at(shaft.bearing_608(-P.BRG608[2]), xi, y), g, "static", "steel")
        m.add(f"{n}_bearing_rear", at(shaft.bearing_608(P.Z_PLATE_R[0]), xi, y), g, "static", "steel")
        m.add(f"{n}_shim", at(shaft.shim(), xi, y), g, "moving", "steel")
        m.add(f"{n}_mitre_drum", at(shaft.drum_mitre(), xi, y), g, "moving", "brass")
        sign = -1 if row == "mid" else 1
        m.add(f"{n}_mitre_line", at(shaft.line_mitre(sign), xi, y), g, "moving", "brass")


# ---------------------------------------------------------------------------------------------
def build_frame_and_guards(m):
    for n, s in frame.bank_frame().items():
        m.add(f"frame_{n}", s, "frame", "static", "alu")
    for n, s in guards.guards().items():
        col = "pc" if n.startswith("cover_front") else "cover"
        m.add(f"guard_{n}", s, "guards", "static", col)
    m.add("plinth", frame.plinth(), "base", "static", "black")
    m.add("floor_slab", frame.floor_slab(), "base", "static", "black")
    m.add("panel_cabinet", frame.panel_cabinet(), "base", "static", "cover")


def build_bank(m, with_base=True):
    for s in P.POPULATED:
        build_column(m, s)
    for s in P.EMPTY:
        build_blank(m, s)
    build_line_shafts(m)
    build_drive(m)
    if with_base:
        build_frame_and_guards(m)
    return m


def build_machine():
    return build_bank(Model())
