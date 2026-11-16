# Parametric CAD of the gear-drum bombe (track G, PLAN 0, item 8)

Script-generated solid model (CadQuery 2.8 on the project venv, OpenCascade kernel); no GUI CAD is needed.
Dimensions: `params.py` is the only place numbers live (the slot angles `SLOT_STEPS` are also read by the gear control-board model, `hardware/gear/control-board/firmware_model.py`); every value cites `layout.md` (L), decision 0002 (D),
the drum PCB README (PCB) or is marked `(mod)` = modelling choice. Inputs: `hardware/gear/layout.md`,
`docs/decisions/0002-gear-detail-decisions.md`, `hardware/gear/drum-pcb/README.md`.

## Regenerate

```
"%LOCALAPPDATA%\christopher\venv\Scripts\python.exe" -m pip install cadquery     # once (2.8.0 installed)
cd hardware/gear/cad
python check.py            # builds the machine (6 s), runs ~70 checks incl. the boolean interference scan (20 s), exit 1 on failure
python check.py --quick    # without the boolean scan
python assemble.py         # STEP (machine + subassemblies) and SVG views; --no-step / --no-svg / --png
python render_png.py       # shaded PNG views for the report: report/figures/generated/cad_{iso,front,section,station}.png (VTK, about 15 s)
```

Build products go **outside** the repo: `%LOCALAPPDATA%/christopher/cad` (`$XDG_CACHE_HOME/christopher/cad` elsewhere):
`gear_bombe_machine.step` (whole machine, ~420 named parts, colours), `sub_<group>.step` for `column_07 ... column_12`,
`drive`, `lines`, `frame`, `guards`, `base`, `sub_drum_station_mid_row.step`, `sub_blanking_plates.step`, and
`view_front/side/iso.svg`. The report drawings are written to `report/figures/generated/cad_*.svg`
(front, section through column 12, plan section through the middle row, drive-module gear plane, isometric).
SVG is kept (no Inkscape/rsvg on this machine; TeX Live has `dvisvgm` only for the other direction).

## Layout of the sources

| File | Content |
|---|---|
| `params.py` | all dimensions with source citations |
| `parts/drum.py` | PCB O96 (bore 8.6, 4 x O3.2, dowel O2, rim notch), 104 pads, body (O100 x 10 skirt + O96.3 seat + O70 x 18 grip + bush bore), bayonet knob (O32 x 14, funnel +-40, 1.8 / 90 deg ramp, end pocket, both sides) |
| `parts/index.py` | index disc O34 x 3 fused with 3 pins O3 x 6 (tilt atan(pi/26), 1.5*sqrt2 above the groove floor); flange O34 x 5.6 with 26 radial 90-deg V grooves, depth pi*r/26 |
| `parts/shaft.py` | O8 shaft with O6 collar and O3 cross pin, sleeve, shim, 608ZZ / 6004ZZ envelopes, mitre (pitch-cone envelope, 25T m2) for drum and line shafts |
| `parts/board.py` | contact board 110 x 110 x 1.6, O38 centre hole, 104 holes; 104 Mill-Max 7845-0 envelopes (body O4.37, plunger, ball O3.96, tail); 4 board posts |
| `parts/column.py` | front/rear plates 116 x 400 x 8 with 3 x O22 bores, spacer posts, blanking plate |
| `parts/line.py` | O20 line shaft, coupling O40 x 50, 16 mm plate bracket with three 6004, drum-change brake envelope on F (`f_brake`) |
| `parts/gearing.py` | 26:26:30 m1.5 train, single-tooth carry discs with locking arc (R_a = C - R_L - 0.05), 26-pocket lock discs, position discs, slot disc O160 with STROBE/JUDGE/CLEAR tracks (26 slots each, angles from the ms table via `params.track_deg`) |
| `parts/motor.py` | NEMA 34 body 86 x 86 x 147, shaft, HTD 5M 18T/72T pulleys, belt (tangent-path shell, 3.6 thick), torque limiter, U-fork sensor envelope |
| `parts/frame.py` | 4040 profiles and 12 x 50 reference bars as rectangles, plinth 400 with toe recess, floor slab, panel 1800 x 420 |
| `parts/guards.py` | front cover PC 5 t with 2 mm gap to the fixed trim, fixed covers 3 t, crank hole with spring shutter |
| `build.py` | placement: `build_column`, `build_drive`, `build_line_shafts`, `build_bank`, `build_machine` (flat list of parts with `role` moving/static) |
| `assemble.py` | STEP/SVG export. `check.py` | geometric checks |

Coordinates: X width (slot 1 left), Y height above the floor, Z depth with Z = 0 on the front face of the column front plates;
visitor side +Z. Rows: slow 980, middle 1100, fast 1220 (D12). Slot centres X = 40 + 120 (n - 0.5); drive module X 1480 ... 1840.

## What is modelled, what is simplified

Modelled as solids with the real positions: the whole Z stack of one drum station (L3.2: pad plane +27, board +16.4 ... +18.0, flange
+18 ... +23.6 with 0.4 gap to the index disc, plates, 608 bearings, shaft -95 ... +63, mitre hub -69 ... -92.5, line axis -109);
the three-pin / V-groove index in its seated pose (pins tangent to the groove faces); the bayonet cam track with the cross pin in its
end pocket; 104 contacts and 104 pads per drum; 12 slots (7-12 populated, 1-6 blanking plates 3 t); line shafts (segment A only,
D6), mitres on the opposite side for the middle row (D7), three brackets at the slot boundaries 6|7, 8|9, 10|11; the complete drive
module (front plate 360 x 400, plates A/B, F/I/C/M/S shafts, couplings, the drum-change brake on F (envelope O64 x 33 at u 59-92,
`hardware/common/rotor-cartridge.md` 4.3), 26:26:30 train, both carries with locking arcs and 26-pocket
lock discs, position discs, slot disc + sensor fork, NEMA 34 + HTD 5M belt + torque limiter, indicator column of three fixed drums);
frame, plinth 400, panel, covers, shutter.

Simplified: gears are pitch-circle cylinders / pitch-cone envelopes (no involute teeth; mating envelopes touch with zero volume);
single-tooth discs show one tooth in a static pose (pointing away from the mating shaft); the disc spring, screws (M3, M5, M6), pins
in the posts, gold plating (0.035 mm, embedded in the PCB face), letter ring and printing, ribbon cables, headers, sensors' electronics,
brackets' fixings, the drive module's mounting to the frame, the display, the common panel's interior, hinges and gas springs are not
modelled. Ball tips are exactly tangent to the pad plane (compressed state, 9.0 working height).

## Check results (`python check.py`, last run: 80 PASS, 0 FAIL, 6 OPEN)

| Group | Result |
|---|---|
| Interference (boolean intersection volume, 683 bbox-overlapping pairs of 418 parts, 665 tested, the other 18 are the embedded pad/PCB pairs) | moving vs static 0, moving vs moving 0, static vs static 0; largest sliver 0.033 mm3 (tangent pin / V-groove faces); a negative self-test (drum shifted 1 mm onto the contacts) is detected |
| Shaft centres | F-I 39, I-C 42, C-M 39, sum 120, M-S 120, all five on Z = -109; mesh radii F-I 39, I-C 42, carry 1 13 + 26 = 39, carry 2 81 + 39 = 120; belt length 450.0 (C = 103.6, motor axis 70 behind / 76.4 above F) |
| Drum station | pad plane +27.000; contact ball top 27.000 (9.0 above the board); board +16.4 ... +18.0; board posts 16.4; flange +18 ... +23.6; disc +24 ... +27 (gap 0.4); pin bottoms 0.71 / 1.37 below the peak plane (spec 0.7 / 1.4); V-groove volume within 0.5 % of the analytic value; 104 contacts / 104 pads |
| Pitch | drum pitch 120 vertical and horizontal, neighbouring drum bodies 20 apart, 18 drums, 6 blanking plates, plate gaps 4.0 |
| Envelope | 1880.0 x 1350.0 x 403.0 (spec 1880 x 1350 x 400 +- 5); plinth 400; bank width 1880 over the side covers |
| Guards | front cover to fixed trim gaps 2.0 (4 sides), shutter gap 1.0, plate and blanking-plate gaps 4.0 (all <= 4); front cover to the nearest moving part (knob) 31 (>= 20); motor to rear cover 3.0, to top cover 7.6 |
| Slot disc | STROBE r72 -0.94 ... +0.94 (base slot, calibrated by the sensor bracket), JUDGE r62 +1.73 ... +4.25, CLEAR r52 +4.88 ... +8.96 (deg from the pad centre; connector-face windows of layout 3.8 = `SLOT_STEPS`, motor steps of 0.03 deg) |
| Slot timing | decision 0003 3.3 at 60 rpm with 0.5 ms tolerance margin: L1 +1.02, L2 (calibrated form, after the 0.5 ms calibration residual) +0.56, L3 +0.58, L4 +0.52, L5 +0.58, C6 +2.23 ms; stop zone CLEAR end ... next STROBE 44 steps (worst 19.9); k = 25 JUDGE contact end 305.50 deg (10.7 deg before carry 1), CLEAR slot end 308.96 deg. OPEN: L2 in its uncalibrated form (-0.44 ms; not applicable, the gear STROBE is calibrated) |
| Drum-change brake on F | clearance to the carry-2 tooth sweep (tip R84 on M) 4.00 (>= 2); 1.0 to the F coupling and 1.0 to the end of the idler shaft I (a first placement at u 61-97 overlapped the I shaft stub by 105 mm3 and was moved); holding torque 4.0 > 2 x 1.5 N m (two drums slipping their index) |

## Open items: dimensions that do not close geometrically (reported by `check.py` as OPEN)

1. **Position disc O60 (L3.8) touches the neighbouring O20 shaft**: discs on F and M sit next to I and C, 39 mm away, so the radius must be
   <= 29; O60 gives -1.0. Modelled O56 (`POSDISC_D`). Decide O56 or move the disc to a plane where I / C are absent.
2. **6004 bearings (OD 42) cannot be placed on the 39 mm centres** F-I and C-M in the drive-module plates. Modelled 6804ZZ (20 x 32 x 7); the
   line-shaft brackets keep 6004 (120 mm apart). Needs a decision (thin-section bearing, or stagger plates A/B).
3. **Index pin O3 x 6 vs groove band r 11-16.5**: at the 6.9 deg tilt the pin projects 5.96 mm radially against 5.5 mm band. The groove cutters
   run r 10.5-17.5 in the model (they exit the O34 flange edge). Either shorten the pin to 5.4 or widen the band (flange O34 stays).
4. **Locking arc of carry 2**: `R_a` 'about 78' (layout) is the tooth-plane limit 120 - 42 (S tip); in the lock plane R_a + R_L = 120 needs
   R_a = 76.95 (R_L 43, gap 0.05). Modelled 76.95. Carry 1: 11.95 (layout about 12) is consistent.
5. **Reference bar 12 x 50** cannot stand 50 high: only 30 mm lie between the bank floor (870) and the plates (900). Modelled laid flat
   (12 high x 50 deep) under / over the plates, which also keeps the plates' forward extraction free. The front bottom 4040 rail is trimmed to 30 high.
6. **Side and top covers (3 t) must be recessed 3 mm into the 4040 end posts and rails** to keep the width 1880 (otherwise 1886, outside +-5);
   the rear cover (inner face Z -225, 3 t) makes the depth 403 (within +-5).
7. **Indicator column**: a 116 wide rear plate would cross drive plate A, so a 70 wide rear plate is modelled; the indicator axis sits 150 from
   the module start so that the middle-row mitre (u 110 ... 133.5) clears plate A and the other two (u 166.5 ... 190) clear the 26:30 train (from u 195).
8. **Mitre length**: the envelope spans apex distance 16.5 ... 40 (23.5 long, hub 15, teeth 8.5 face); layout says 'about 29' (catalogue not yet checked, PLAN item 8 / T12).
9. Motor: body 147 in the 150 chamber leaves 3; top gap 7.6 to the top cover and 3.0 to the rear cover (layout 'about 10' and 3). The rear rails, the
   rear right post (O30 hole) and plates A/B (up to Y 1342) were adapted around the motor; any growth of the motor needs the bank moved back (D5).
10. Not yet modelled / still open from the documents: ISO 13857 table 5 and ISO 13855:2024 text, mitre catalogue dimensions, motor speed-torque curve (T12);
    segment B of the line shafts and the 6|7 coupling (expansion); ribbon cables and headers; display; common-part interiors.
