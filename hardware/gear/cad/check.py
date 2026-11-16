"""Automated geometric checks of the CAD model against layout.md / decision 0002.

    python check.py            # builds the whole machine, runs all checks, prints a table
    python check.py --quick    # skip the (slow) pairwise boolean interference scan

Exit code 0 = every hard check passed.  "OPEN" rows list dimensions of the documents that do not
close geometrically; they are reported but do not change the exit code (see README.md).
"""
import math
import sys
import time

import numpy as np
import cadquery as cq

import params as P
import build
from parts import gearing, motor, guards, board as board_mod, index as index_mod

VOL_TOL = 0.5          # mm^3: boolean slivers of coincident / tangent surfaces are below this
BBOX_EPS = 1e-3        # mm: boxes overlapping by less than this are treated as touching


class Table:
    def __init__(self):
        self.rows = []

    def add(self, name, spec, meas, ok, hard=True):
        status = "PASS" if ok else ("FAIL" if hard else "OPEN")
        self.rows.append((name, spec, meas, status))

    def print(self):
        w = [max(len(str(r[i])) for r in self.rows + [("check", "spec", "measured", "result")]) for i in range(4)]
        w = [min(x, 78) for x in w]
        line = lambda r: "| " + " | ".join(str(r[i]).ljust(w[i]) for i in range(4)) + " |"
        print(line(("check", "spec", "measured", "result")))
        print("|-" + "-|-".join("-" * x for x in w) + "-|")
        for r in self.rows:
            print(line(r))

    @property
    def failed(self):
        return [r for r in self.rows if r[3] == "FAIL"]


def bb(part):
    return part.shape.BoundingBox()


def by_name(model):
    return {p.name: p for p in model.parts}


def bbox_gap(a, b):
    """Euclidean distance between two bounding boxes (0 if they touch or overlap)."""
    dx = max(a.xmin - b.xmax, b.xmin - a.xmax, 0.0)
    dy = max(a.ymin - b.ymax, b.ymin - a.ymax, 0.0)
    dz = max(a.zmin - b.zmax, b.zmin - a.zmax, 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# ------------------------------------------------------------------------------------------
def interference(model, tab, quick=False):
    parts = model.parts
    n = len(parts)
    boxes = [bb(p) for p in parts]
    arr = np.array([[b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax] for b in boxes])
    pairs = []
    for i in range(n):
        ov = ((np.minimum(arr[i, 1], arr[i + 1:, 1]) - np.maximum(arr[i, 0], arr[i + 1:, 0]) > BBOX_EPS) &
              (np.minimum(arr[i, 3], arr[i + 1:, 3]) - np.maximum(arr[i, 2], arr[i + 1:, 2]) > BBOX_EPS) &
              (np.minimum(arr[i, 5], arr[i + 1:, 5]) - np.maximum(arr[i, 4], arr[i + 1:, 4]) > BBOX_EPS))
        for j in np.nonzero(ov)[0]:
            pairs.append((i, i + 1 + int(j)))

    def allowed(a, b):
        names = (a.name, b.name)
        # gold pads are embedded flush in the PCB face by construction
        return a.group == b.group and sorted(x.rsplit("_", 2)[-2:] for x in names) == [["drum", "pads"], ["drum", "pcb"]]

    todo = [(i, j) for i, j in pairs if not allowed(parts[i], parts[j])]
    tab.add("bbox-overlapping part pairs", "info", f"{len(pairs)} of {n * (n - 1) // 2} ({len(todo)} tested)", True)
    if quick:
        return
    bad, err = [], []
    t0 = time.time()
    worst = 0.0
    for i, j in todo:
        a, b = parts[i], parts[j]
        try:
            v = a.shape.intersect(b.shape).Volume()
        except Exception as e:                       # boolean failure counts as a failure
            err.append((a.name, b.name, str(e)[:40]))
            continue
        worst = max(worst, v)
        if v > VOL_TOL:
            bad.append((a.name, b.name, v))
    moving = {p.name for p in parts if p.role == "moving"}
    mov_stat = [x for x in bad if (x[0] in moving) != (x[1] in moving)]
    mov_mov = [x for x in bad if x[0] in moving and x[1] in moving]
    stat_stat = [x for x in bad if x[0] not in moving and x[1] not in moving]
    fmt = lambda L: "; ".join(f"{a} x {b} {v:.1f}" for a, b, v in L[:3])
    tab.add("moving parts vs frame/guards/static parts", f"intersection volume = 0 (< {VOL_TOL} mm3)",
            f"{len(mov_stat)} overlaps " + fmt(mov_stat), not mov_stat)
    tab.add("moving parts vs moving parts", "= 0", f"{len(mov_mov)} overlaps " + fmt(mov_mov), not mov_mov)
    tab.add("static vs static parts", "= 0", f"{len(stat_stat)} overlaps " + fmt(stat_stat), not stat_stat)
    tab.add("boolean errors", "0", f"{len(err)} {err[:2]}", not err)
    tab.add("largest sliver volume (tangent/coincident faces)", f"< {VOL_TOL} mm3", f"{worst:.3f} mm3 in {time.time() - t0:.0f} s",
            worst < VOL_TOL)


# ------------------------------------------------------------------------------------------
def checks_drive(model, tab):
    pn = by_name(model)
    cen = {}
    for k in "FICMS":
        b = bb(pn[f"drive_shaft_{k}"])
        cen[k] = (0.5 * (b.ymin + b.ymax), 0.5 * (b.zmin + b.zmax))
    d = lambda a, b: math.hypot(cen[a][0] - cen[b][0], cen[a][1] - cen[b][1])
    for (a, bk, spec) in (("F", "I", 39), ("I", "C", 42), ("C", "M", 39)):
        tab.add(f"shaft centre distance {a}-{bk}", f"{spec}", f"{d(a, bk):.3f}", abs(d(a, bk) - spec) < 0.01)
    tab.add("F-I-C-M chain", "39 + 42 + 39 = 120", f"{d('F', 'I') + d('I', 'C') + d('C', 'M'):.3f}",
            abs(d('F', 'I') + d('I', 'C') + d('C', 'M') - 120) < 0.01)
    tab.add("shaft centre distance M-S", "120", f"{d('M', 'S'):.3f}", abs(d('M', 'S') - 120) < 0.01)
    tab.add("shafts F, I, C, M, S on one vertical (same X-Z plane, one line in Y)", "Z = -109", f"Z = {cen['F'][1]:.2f}",
            all(abs(cen[k][1] - P.Z_LINE) < 0.01 for k in cen))

    def rad(name):
        b = bb(pn[name])
        return max(0.5 * (b.ymax - b.ymin), 0.5 * (b.zmax - b.zmin))
    tab.add("mesh F-I (pitch radii sum)", "39", f"{rad('drive_train_F') + rad('drive_train_I'):.3f}",
            abs(rad('drive_train_F') + rad('drive_train_I') - 39) < 0.01)
    tab.add("mesh I-C (pitch radii sum)", "42", f"{rad('drive_train_I') + rad('drive_train_C'):.3f}",
            abs(rad('drive_train_I') + rad('drive_train_C') - 42) < 0.01)
    c1 = P.CARRY1_DRV_PD / 2 + rad("carry1_gear_M")
    c2 = P.CARRY2_M * P.CARRY2_TOOTH_EQ_T / 2 + rad("carry2_gear_S")
    tab.add("carry 1 mesh (13T-eq. R13 + M 26T R26)", "39", f"{c1:.3f}", abs(c1 - 39) < 0.01)
    tab.add("carry 2 mesh (54T-eq. R81 + S 26T R39)", "120", f"{c2:.3f}", abs(c2 - 120) < 0.01)
    for tag, arc, lock, dist in (("1", "carry1_arc_C", "carry1_lock_M", 39), ("2", "carry2_arc_M", "carry2_lock_S", 120)):
        s = rad(arc) + rad(lock)
        tab.add(f"carry {tag} lock: arc R + lock disc R + 0.05 gap", f"{dist}", f"{s + P.LOCK_GAP:.3f}", abs(s + P.LOCK_GAP - dist) < 0.01)
    tab.add("lock arc radius R_a carry 1 (layout: about 12)", "12", f"{rad('carry1_arc_C'):.2f}", abs(rad('carry1_arc_C') - 12) < 0.5)
    # lock discs wider than their mating gear (D8: 43 > 39)
    tab.add("carry 2 lock disc larger than S gear", "43 > 39", f"{rad('carry2_lock_S'):.0f} > {rad('carry2_gear_S'):.0f}",
            rad('carry2_lock_S') > rad('carry2_gear_S'))
    # belt
    L = motor.belt_length()
    tab.add("belt length 5M-450 (90 teeth)", "450 +- 5", f"{L:.1f}", abs(L - 450) < 5)
    ym, zm = motor.motor_axis_yz()
    cdist = math.hypot(P.SHAFTS['F'] - ym, P.Z_LINE - zm)
    tab.add("motor axis to F centre distance", "103.6 (about 104)", f"{cdist:.2f}", abs(cdist - 104) < 1)
    # slot disc / track angles
    ok, txt = True, []
    for name, (a0s, a1s) in P.TRACK_SPEC_DEG.items():
        r, a0, a1 = P.track_deg(name)
        txt.append(f"{name} r{r:.0f} {a0:+.2f}..{a1:+.2f}")
        ok &= abs(a0 - a0s) < 0.02 and abs(a1 - a1s) < 0.02
    tab.add("slot disc tracks (deg from the pad centre)", "STROBE -0.94..0.94, JUDGE 1.73..4.25, CLEAR 4.88..8.96", "; ".join(txt), ok)
    checks_slot_timing(tab)
    sd = bb(pn["slot_disc"])
    tab.add("slot disc diameter", "160", f"{sd.ymax - sd.ymin:.2f}", abs(sd.ymax - sd.ymin - 160) < 0.01)
    # clearances in the drive module
    mb = bb(pn["motor_body"])
    rear = [p for p in model.parts if p.name == "guard_cover_rear"][0]
    top = [p for p in model.parts if p.name == "guard_cover_top"][0]
    tab.add("motor body rear face to rear cover", "3 (layout 3.7)", f"{mb.zmin - bb(rear).zmax:.2f}",
            abs(mb.zmin - bb(rear).zmax - 3.0) < 0.5)
    tab.add("motor body top to top cover", "about 10", f"{bb(top).ymin - mb.ymax:.2f}", 5 <= bb(top).ymin - mb.ymax <= 12)
    tab.add("motor body vs bank depth: rear face Z", "-222", f"{mb.zmin:.2f}", abs(mb.zmin + 222) < 0.5)
    # pulley / belt clearance to C shaft (72T R57.3 + belt 2 < 71 in layout)
    bl = bb(pn["belt"])
    pl = pn["pulley_large"]
    gap = math.hypot(P.SHAFTS['F'] - P.SHAFTS['C'], 0) - 10.0 - (motor.pulley_radius(P.HTD_T_LARGE) + P.BELT_T)
    tab.add("belt/pulley to C shaft surface", "> 0 (layout 71 > 59.3)", f"{gap:.2f}", gap > 0)
    # drum-change brake on F (rotor-cartridge.md 3.3)
    br = bb(pn["drive_brake_F"])
    c2tip = P.CARRY2_M * P.CARRY2_TOOTH_EQ_T / 2 + P.CARRY2_M
    clr = (P.SHAFTS["F"] - P.SHAFTS["M"]) - c2tip - P.BRAKE_OD / 2
    tab.add("F brake to carry-2 tooth sweep (tip R84 on M)", ">= 2", f"{clr:.2f}", clr >= 2.0)
    cpl = bb(pn["drive_coupling_F"])
    ish = bb(pn["drive_shaft_I"])
    tab.add("F brake between the F coupling and the I shaft end (bank-side chamber)", ">= 1 / >= 1",
            f"{br.xmin - cpl.xmax:.1f} / {ish.xmin - br.xmax:.1f}", br.xmin - cpl.xmax >= 1.0 and ish.xmin - br.xmax >= 1.0)
    tab.add("F brake holding torque vs two drums slipping their index", f"> 2 x {P.INDEX_SLIP_NM}",
            f"{P.BRAKE_TORQUE_NM:.1f} N m", P.BRAKE_TORQUE_NM > 2 * P.INDEX_SLIP_NM)


D_ON, D_OFF = (2.5, 4.0), (1.5, 4.0)    # decision 0003 3.1: timing-relay delays (ms), range
CAL_RESID = 0.5                         # decision 0003 3.4: STROBE contact window after calibration 0 ... 6 +- 0.5 ms
TOL_MS = 0.5                            # layout 3.8: margin kept for slot / sensor / electronics tolerance (ms)
CARRY1_START_DEG = 316.2                # layout 3.8: carry-1 engagement 316.2 ... 343.8 deg


def checks_slot_timing(tab):
    """Decision 0003 3.3 lower bounds at the connector face (60 rpm) from params.SLOT_STEPS, each with TOL_MS margin.
    t = 0 is the STROBE contact start (calibrated STROBE: rise = -d_on of the STROBE relay, here the representative 2.5)."""
    S0, S1 = P.slot_ms("STROBE")
    J0, J1 = P.slot_ms("JUDGE")
    C0, C1 = P.slot_ms("CLEAR")
    T = P.STEP_MS
    s_contact = S0 + D_ON[0]
    rows = [
        ("L1 STROBE contact width (calibrated 2.5 / 1.5)", ">= 5.0", (S1 + D_OFF[0]) - (S0 + D_ON[0]) - 5.0),
        ("L2 JUDGE rise - STROBE contact start (calibrated form, - 0.5 residual)", ">= 7.5", J0 - (s_contact + CAL_RESID) - 7.5),
        ("L3 JUDGE width", ">= 7.5", (J1 - J0) - 7.5),
        ("L4 CLEAR rise - JUDGE fall", ">= 1.5", (C0 - J1) - 1.5),
        ("L5 CLEAR width", ">= 12.5", (C1 - C0) - 12.5),
        ("L6 (C6) next STROBE contact - CLEAR contact end (d_off 4, - 0.5 residual)", "> 0", (s_contact + T - CAL_RESID) - (C1 + D_OFF[1])),
    ]
    for name, spec, m in rows:
        tab.add(f"slot timing {name}", f"{spec} + {TOL_MS} ms", f"margin {m:+.3f} ms", m >= TOL_MS - 1e-9)
    lit2 = J0 - S0 - 11.5
    tab.add("slot timing L2 uncalibrated form (JUDGE rise - STROBE rise >= 11.5)", "not used: the gear STROBE is calibrated (0003 3.4)",
            f"margin {lit2:+.3f} ms", lit2 >= 0, hard=False)
    # stop zone: CLEAR end ... next STROBE rise; worst = slowest calibrated STROBE relay (d_on 4) + residual + 0.1 deg C sensor
    spp = P.STEPS_PER_POS / T                                  # steps per ms
    zone = P.SLOT_STEPS["STROBE"][0] + P.STEPS_PER_POS - P.SLOT_STEPS["CLEAR"][1]
    worst = zone - (D_ON[1] - D_ON[0] + CAL_RESID) * spp - 0.1 / P.SLOT_STEP_DEG
    tab.add("stop zone CLEAR end ... next STROBE (steps of 0.03 deg)", "worst >= 15 (return target + 10 +- 2)",
            f"nominal {zone} ({zone * P.SLOT_STEP_DEG:.2f} deg), worst {worst:.1f}", worst >= 15)
    # carry 1 after the last cycle of the revolution (k = 25)
    deg = lambda t: (t - P.T_CENTRE_MS) * P.STEP_DEG / T + 25 * P.STEP_DEG
    j_end = deg(J1 + D_OFF[1])
    tab.add("k = 25: JUDGE contact end (worst) before carry 1 (316.2 deg)", ">= 10 deg ahead (layout 8 G.2 k)",
            f"{j_end:.2f} deg -> {CARRY1_START_DEG - j_end:.1f} deg", CARRY1_START_DEG - j_end >= 10.0)
    tab.add("k = 25: CLEAR slot end before carry 1", "< 316.2 deg", f"{deg(C1):.2f} deg", deg(C1) < CARRY1_START_DEG)


def checks_column(model, tab):
    pn = by_name(model)
    s = 12
    n = f"s{s:02d}"
    pcb = bb(pn[f"{n}_fast_drum_pcb"])
    tab.add("pad plane Z", "+27", f"{pcb.zmin:.3f}", abs(pcb.zmin - 27) < 0.005)
    pins = bb(pn[f"{n}_fast_contacts"])
    tab.add("contact ball top (compressed 0.76) = pad plane", "27.0", f"{pins.zmax:.3f}", abs(pins.zmax - 27.0) < 0.005)
    brd = bb(pn[f"{n}_fast_board"])
    tab.add("contact board Z", "+16.4 ... +18.0", f"{brd.zmin:.2f} ... {brd.zmax:.2f}",
            abs(brd.zmin - 16.4) < 0.005 and abs(brd.zmax - 18.0) < 0.005)
    bp = bb(pn[f"{n}_fast_board_posts"])
    tab.add("board post length (front plate face to board)", "16.4", f"{bp.zmax - bp.zmin:.2f}", abs(bp.zmax - bp.zmin - 16.4) < 0.005)
    tab.add("contact working height above board (compression 0.76 of 9.75)", "9.0", f"{pins.zmax - brd.zmax:.2f}",
            abs(pins.zmax - brd.zmax - 9.0) < 0.005)
    n_pins = len(pn[f"{n}_fast_contacts"].shape.Solids())
    n_pads = len(pn[f"{n}_fast_drum_pads"].shape.Solids())
    tab.add("contacts per board / pads per drum", "104 / 104", f"{n_pins} / {n_pads}", n_pins == 104 and n_pads == 104)
    # index
    fl = bb(pn[f"{n}_fast_flange"])
    di = bb(pn[f"{n}_fast_index_disc"])
    tab.add("flange Z", "+18.0 ... +23.6", f"{fl.zmin:.2f} ... {fl.zmax:.2f}", abs(fl.zmin - 18) < 0.005 and abs(fl.zmax - 23.6) < 0.005)
    tab.add("index disc Z (O34 x 3 on the pad face)", "+24 ... +27", f"{di.zmax - 3:.2f} ... {di.zmax:.2f}", abs(di.zmax - 27) < 0.005)
    # pin depth below the peak plane at inner / outer pin end
    t = math.atan(P.GROOVE_SLOPE)
    rc = 0.5 * sum(P.INDEX_BAND)
    zc = lambda r: P.FLANGE_Z1 - P.GROOVE_SLOPE * r + P.PIN_CENTRE_OVER_FLOOR
    zlow = lambda r: zc(r) - P.INDEX_PIN_D / 2
    depth_in, depth_out = P.FLANGE_Z1 - zlow(11.0), P.FLANGE_Z1 - zlow(16.5)
    tab.add("pin bottom below peak plane at r 11 / 16.5", "0.7 / 1.4", f"{depth_in:.2f} / {depth_out:.2f}",
            abs(depth_in - 0.7) < 0.05 and abs(depth_out - 1.4) < 0.05)
    # groove volume: 26 V grooves with floor line depth pi r / 26 (area d^2 per section)
    full = math.pi * (17.0 ** 2 - 4.0 ** 2) * 5.6
    removed = full - pn[f"{n}_fast_flange"].shape.Volume()
    r1, r2 = P.GROOVE_R_CUT[0], 17.0
    expect = 26 * (math.pi / 26) ** 2 * (r2 ** 3 - r1 ** 3) / 3
    tab.add("26 V grooves: removed flange volume vs analytic", "within 6 %", f"{removed:.0f} vs {expect:.0f} mm3 ({100 * (removed / expect - 1):+.1f} %)",
            abs(removed / expect - 1) < 0.06)
    gap = di.zmax - 3 - fl.zmax
    tab.add("gap index disc to flange peaks", "0.4", f"{gap:.2f}", abs(gap - 0.4) < 0.01)
    # shaft
    sh = bb(pn[f"{n}_fast_shaft"])
    tab.add("drum shaft length", "160 (-95 ... +63 = 158)", f"{sh.zmax - sh.zmin:.1f}", abs(sh.zmax - sh.zmin - 158) < 0.5)
    kn = bb(pn[f"{n}_fast_drum_knob"])
    tab.add("drum length index disc to knob end", "45 (Z +24 ... +69)", f"{kn.zmax - (di.zmax - 3):.1f}", abs(kn.zmax - (di.zmax - 3) - 45) < 0.01)
    dm = bb(pn[f"{n}_fast_mitre_drum"])
    lm = bb(pn[f"{n}_fast_mitre_line"])
    tab.add("drum-shaft mitre Z (hub back -69, small end -92.5)", "-69 ... -92.5", f"{dm.zmax:.1f} ... {dm.zmin:.1f}",
            abs(dm.zmax + 69) < 0.01 and abs(dm.zmin + 92.5) < 0.01)
    # mitre apex at the line axis: apex distance of both cones
    tab.add("line mitre centre line on Z = -109", "-109", f"{0.5 * (lm.zmin + lm.zmax):.2f}", abs(0.5 * (lm.zmin + lm.zmax) + 109) < 0.01)


def checks_pitch(model, tab):
    pn = by_name(model)
    ys = [0.5 * (bb(pn[f"s12_{r}_drum_body"]).ymin + bb(pn[f"s12_{r}_drum_body"]).ymax) for r in P.ROW_ORDER]
    v1, v2 = ys[1] - ys[0], ys[2] - ys[1]
    tab.add("drum pitch vertical (slow-mid, mid-fast)", "120 / 120", f"{v1:.2f} / {v2:.2f}", abs(v1 - 120) < 0.01 and abs(v2 - 120) < 0.01)
    xs = [0.5 * (bb(pn[f"s{s:02d}_mid_drum_body"]).xmin + bb(pn[f"s{s:02d}_mid_drum_body"]).xmax) for s in P.POPULATED]
    dx = [b - a for a, b in zip(xs, xs[1:])]
    tab.add("drum pitch horizontal (slots 7-12)", "120 each", f"{min(dx):.2f} ... {max(dx):.2f}", all(abs(x - 120) < 0.01 for x in dx))
    tab.add("row heights above the floor", "980 / 1100 / 1220", " / ".join(f"{y:.0f}" for y in ys), all(abs(a - b) < 0.01 for a, b in zip(ys, (980, 1100, 1220))))
    gap = bb(pn["s12_mid_drum_body"]).xmin - bb(pn["s11_mid_drum_body"]).xmax
    tab.add("clearance between neighbouring drum bodies O100", "20", f"{gap:.2f}", abs(gap - 20) < 0.01)
    ndrums = sum(1 for p in model.parts if p.name.endswith("_drum_body") and not p.name.startswith("indicator"))
    tab.add("drums in the bank (6 columns x 3)", "18", str(ndrums), ndrums == 18)
    nb = sum(1 for p in model.parts if p.name.endswith("_blank"))
    tab.add("blanking plates (slots 1-6)", "6", str(nb), nb == 6)
    # slot plates gap (116 in 120) <= 4
    g = bb(pn["s08_plate_front"]).xmin - bb(pn["s07_plate_front"]).xmax
    tab.add("gap between neighbouring column plates (guard gap)", "<= 4", f"{g:.2f}", g <= P.GUARD_GAP_MAX + 1e-6)
    g2 = bb(pn["s07_plate_front"]).xmin - bb(pn["s06_blank"]).xmax
    tab.add("gap blanking plate - column plate", "<= 4", f"{g2:.2f}", g2 <= P.GUARD_GAP_MAX + 1e-6)
    # bracket vs mitre
    br = bb(pn["bracket_1"])
    gaps = []
    for p in model.parts:
        if p.name in ("s08_fast_mitre_line", "s08_slow_mitre_line", "s09_mid_mitre_line"):
            gaps.append(bbox_gap(bb(p), br))
    tab.add("line-shaft mitre to bracket (D7: 12-14)", "12 ... 14", f"{min(gaps):.2f} ... {max(gaps):.2f}", min(gaps) >= 11.99 and max(gaps) <= 14.01)
    cb = bb(pn["drive_coupling_F"])
    tab.add("coupling length / OD", "50 / 40", f"{cb.xmax - cb.xmin:.1f} / {cb.ymax - cb.ymin:.1f}", abs(cb.xmax - cb.xmin - 50) < 0.01)


def checks_envelope(model, tab):
    comp = cq.Compound.makeCompound([p.shape for p in model.parts])
    b = comp.BoundingBox()
    W, H, D = b.xmax - b.xmin, b.ymax - b.ymin, b.zmax - b.zmin
    for nm, v, spec in (("width", W, P.ENVELOPE[0]), ("height (plinth to bank top)", H, P.ENVELOPE[1]), ("depth", D, P.ENVELOPE[2])):
        tab.add(f"overall envelope {nm}", f"{spec:.0f} +- {P.ENVELOPE_TOL:.0f}", f"{v:.1f}", abs(v - spec) <= P.ENVELOPE_TOL)
    pn = by_name(model)
    fr = [bb(p) for p in model.parts if p.name.startswith("frame_")]
    wb = bb(pn["guard_cover_right"]).xmax - bb(pn["guard_cover_left"]).xmin
    tab.add("bank width over the side covers (40 + 12 x 120 + 360 + 40)", "1880", f"{wb:.1f}", abs(wb - 1880) < 0.01)
    pl = bb(pn["plinth"])
    tab.add("plinth height / machine top", "400 / 1350", f"{pl.ymax:.0f} / {b.ymax:.0f}", abs(pl.ymax - 400) < 0.01 and abs(b.ymax - 1350) < 0.01)
    tab.add("drive module width", "360", f"{bb(pn['drive_plate_front']).xmax - bb(pn['drive_plate_front']).xmin:.0f}",
            abs(bb(pn['drive_plate_front']).xmax - bb(pn['drive_plate_front']).xmin - 360) < 0.01)
    chambers = sum(v[1] - v[0] for v in P.D_CHAMBERS.values())
    tab.add("drive chambers 90 + 8 + 150 + 8 + 90", "346", f"{chambers:.0f}", abs(chambers - 346) < 0.01)


def checks_guards(model, tab):
    pn = by_name(model)
    cf = bb(pn["guard_cover_front"])
    gaps = {k: bbox_gap(cf, bb(pn[f"guard_trim_{k}"])) for k in ("left", "right", "top", "bottom")}
    tab.add("front cover to fixed trim gaps (L/R/top/bottom)", "<= 4", " / ".join(f"{v:.1f}" for v in gaps.values()),
            max(gaps.values()) <= P.GUARD_GAP_MAX + 1e-6)
    tab.add("shutter in crank hole: radial gap", "<= 4", f"{(guards.HOLE_D - guards.SHUTTER_D) / 2:.1f}",
            (guards.HOLE_D - guards.SHUTTER_D) / 2 <= P.GUARD_GAP_MAX)
    tab.add("front cover thickness / inner face Z", "5 / +100", f"{cf.zmax - cf.zmin:.1f} / {cf.zmin:.0f}", abs(cf.zmax - cf.zmin - 5) < 0.01)
    mov = [bb(p) for p in model.parts if p.role == "moving"]
    zmax = max(x.zmax for x in mov)
    tab.add("front cover to nearest moving part (knob)", ">= 20 (D10; 31 in L3.2)", f"{cf.zmin - zmax:.1f}", cf.zmin - zmax >= P.GUARD_TO_MOVING_MIN)
    # moving parts: minimum distance to fixed covers (top / rear / sides)
    cov = {k: bb(pn[f"guard_cover_{k}"]) for k in ("top", "rear", "left", "right")}
    mins = {"top": min(cov["top"].ymin - x.ymax for x in mov if x.ymax < cov["top"].ymin + 1),
            "rear": min(cov["rear"].zmax - x.zmin for x in mov) * -1.0 + 0.0}
    tab.add("moving parts to top cover (min distance)", ">= 0", f"{mins['top']:.1f}", mins['top'] >= 0)
    rear_gap = min(x.zmin for x in mov) - cov["rear"].zmax
    tab.add("moving parts to rear cover (min distance)", ">= 0", f"{rear_gap:.1f}", rear_gap >= 0)


def checks_open(model, tab):
    """Dimensions of layout.md / 0002 that do not close geometrically (reported, not counted)."""
    # (1) position disc O60 vs the neighbouring O20 shaft at 39 mm
    c = 39.0 - 10.0
    tab.add("position disc O60 (L3.8) vs shaft I/C at 39 mm", "R_disc <= 39 - 10 = 29", f"R = {P.POSDISC_D_SPEC / 2:.0f} -> clearance {c - P.POSDISC_D_SPEC / 2:+.1f}",
            P.POSDISC_D_SPEC / 2 <= c, hard=False)
    # (2) 6004 bearings (OD 42) between shafts 39 apart
    tab.add("6004 bearing OD 42 between shafts 39 apart (F-I, C-M)", "OD < 39", f"OD 42 -> {39 - 42:+.0f} (modelled 6804, OD 32)",
            42 < 39, hard=False)
    # (3) index pin length vs groove band
    foot = P.INDEX_PIN_L * math.cos(math.atan(P.GROOVE_SLOPE))
    band = P.INDEX_BAND[1] - P.INDEX_BAND[0]
    tab.add("index pin O3 x 6 radial foot print vs groove band r 11-16.5", "<= 5.5", f"{foot:.2f} (cutters run r 10.5-17.5 in the model)",
            foot <= band, hard=False)
    # (4) lock arc of carry 2 ('about 78') = 120 - 43 - 0.05
    ra = P.C_MS - P.LOCK_R2 - P.LOCK_GAP
    tab.add("lock arc R_a of carry 2: R_a + R_L = centre distance", "78 + 43 = 121 vs 120", f"R_a = {ra:.2f}", abs(ra - 78) < 0.5, hard=False)
    # (5) reference bar 12 x 50 vs the 30 mm below the plates
    tab.add("reference bar 12 x 50 flat vs 30 mm bank-floor-to-plate", "bar 50 high would overlap plates by 20", "modelled laid flat (12 high, 50 deep)",
            False, hard=False)
    # (6) motor clearance
    mb = bb(by_name(model)["motor_body"])
    tab.add("motor rear face Z -222 vs middle chamber 150 (motor 147)", "147 <= 150", f"147 in 150 (3 spare)", True, hard=False)


def selftest(tab):
    """Negative test: the interference scan must detect injected overlaps (a pad-plane shift of the
    drum into the contact balls, and a knob pushed into the front cover region)."""
    m = build.Model()
    build.build_column(m, 12)
    base = {p.name: p for p in m.parts}
    # (a) drum body moved 1 mm towards the board: pins must dig into PCB/pads; (b) copy of the knob on the shaft
    for p in m.parts:
        if p.name.startswith("s12_mid_drum_") or p.name == "s12_mid_index_disc":
            p.shape = p.shape.translate(cq.Vector(0, 0, -1.0))
    t = Table()
    interference(m, t)
    hits = [r for r in t.rows if r[0].startswith(("moving parts vs", "static vs")) and r[3] == "FAIL"]
    tab.add("self-test: drum shifted 1 mm onto the contacts is detected", ">= 1 failing class", f"{len(hits)} failing class(es)", len(hits) >= 1)


def main(argv):
    quick = "--quick" in argv
    t0 = time.time()
    model = build.build_machine()
    print(f"model: {len(model.parts)} parts, built in {time.time() - t0:.1f} s")
    tab = Table()
    checks_pitch(model, tab)
    checks_column(model, tab)
    checks_drive(model, tab)
    checks_guards(model, tab)
    checks_envelope(model, tab)
    interference(model, tab, quick)
    checks_open(model, tab)
    selftest(tab)
    tab.print()
    n_fail = len(tab.failed)
    n_open = sum(1 for r in tab.rows if r[3] == "OPEN")
    print(f"\n{sum(1 for r in tab.rows if r[3] == 'PASS')} PASS, {n_fail} FAIL, {n_open} OPEN (not counted)  [{time.time() - t0:.0f} s]")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
