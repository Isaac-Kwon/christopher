# Contact board, column terminal and reflector card (gear bombe, track G)

Generated KiCad 10 schematics, **placement-only** PCBs (no routing: the user routes), and a column-level verification for the
scrambler column of the gear bombe. Design basis: `hardware/gear/layout.md` 3.4, `hardware/gear/concept.md` 3.1,
`hardware/gear/drum-pcb/README.md` (ring radii, pad meaning), `docs/decisions/0002-gear-detail-decisions.md` section 3 (contact),
`docs/decisions/0003-interfaces.md` 5.1-5.2 and 7 (connectors, 27-29, CHASSIS). All files are build products of the scripts.

## Parts of a column

| Design | Per column | Content |
|---|---:|---|
| `contact_board` (station) | 3 (identical) | 110 x 110, 104 Mill-Max Omniball 7845-0 contacts on the four drum rings + four 2 x 13 IDC headers J1..J4 (ring 1..4) |
| `column_terminal` | 1 | the column's `IN` / `OUT` jacks: two male D-sub 37 (pins 1-26 = wire A..Z, 27-37 **not connected**, shell = CHASSIS) + two 2 x 13 IDC ribbon headers K1 (IN), K2 (OUT) |
| `reflector_B`, `reflector_C` | 1 of them | reflector wiring card: two 2 x 13 IDC headers K1 (from the top station's ring 3), K2 (to its ring 2) joined by the reflector permutation of `data/wirings/enigma.txt` |

Column chain (layout.md 3.4 / concept.md 3.1; lower = slow drum = Enigma right rotor, upper = fast drum = left rotor):

    IN jack - lower R4 | lower R3 - middle R4 | middle R3 - upper R4 | upper R3 - reflector card - upper R2 |
    upper R1 - middle R2 | middle R1 - lower R2 | lower R1 - OUT jack

= **8 ribbons**, 26 conductors each, pin k to pin k (4 between stations, 2 to the terminal, 2 to the reflector card). Ring Rr of a
station = contacts at r = 22 / 29 / 36 / 43 mm = the drum PCB's ring r (R4/R1 = entry-side rings, R3/R2 = reflector-side rings, copy A/B).
The reflector card hangs at the top of the column (bank top edge, visible, layout.md 3.4); the terminal sits at the foot of the column
on the panel (unit-jack row, pitch 120).

## Conventions (the part that must agree with the drum PCB)

**Contact numbering.** Contact position j (0..25 = A..Z) of ring r sits at angle j x 360/26 = 13.846 deg from the index mark (A at
12 o'clock, same mark as the drum) **counter-clockwise seen from the contact (front) side**. Reason: the drum README numbers its pads
clockwise *seen from the pad side*; the contact board's front looks at that face from the other side, so the same physical order
appears counter-clockwise. Net `R<r>_<letter>` joins contact `P<r><letter>` to pin (j+1) of header J<r>. `gen_contact_pcb.py --contacts-cw`
produces the mirror image if the drum turns the other way (same open question 2 as `drum-pcb/README.md`).

**Drum-to-contact rule used by the verification.** With the drum showing letter o at the index mark, contact position g touches pad
index (g + o) mod 26. Rotor wiring W (copy A: pad 4<i> - pad 3<W[i]>, copy B: pad 1<i> - pad 2<W[i]>) then gives, for the ring-4
contact at g, the ring-3 contact at (W[g+o] - o): exactly the core's rotor map `c -> W[c+o] - o`.

**Contact footprint.** No library footprint exists, so `Christopher.pretty/Omniball_7845-0-15-20-52-14-11-0.kicad_mod` is generated:
one through-hole pad, body outline Ø4.37, ball Ø3.96, courtyard Ø4.6. **Assumed (unconfirmed, no datasheet offline):** tail hole Ø1.7,
pad Ø2.8. Neighbour gap on ring 1 is 0.95 mm body to body (layout.md); inner contact body edge r = 19.8 vs centre hole r = 19.

**Board** (`contact_board`): 110 x 110, centre hole Ø38 (index flange), posts at (+-48, +-48): two M3 holes (3.2) and two dowel holes
(3.0) on the diagonal; 4 layers (routing is the user's). Contacts on the front, ribbon headers on the **back** (flipped footprints)
in the four corners at 45 deg: R2 top left, R3 top right, R1 bottom left, R4 bottom right, so that ring 3 -> next station's ring 4
and ring 1 <- next station's ring 2 are straight ribbons up/down; pin 1 at the end nearest the board's left (R1, R2) or right (R3, R4)
edge, so all ribbons run parallel without a twist (identical header orientation on both ends, standard IDC 1:1). The header tails stand
about 2 mm proud of the front, far below the drum pad face (9 mm), outside r = 46.
**No 0 V and no CHASSIS on the station board** (decision 0003 5.2 and the board list in section 8: scrambler side); the terminal board ties shells and mounting holes
to CHASSIS only. 0003 7.1's CHASSIS-0 V RC is not needed where there is no 0 V.

## Files

| File | Purpose |
|---|---|
| `ki_common.py` | shared generator/checker library (identical copy in `hardware/common/diagonal-board` and `patch-panel`) |
| `gen_contact.py` | footprint library + four schematics (`contact_board`, `column_terminal`, `reflector_B`, `reflector_C`) |
| `gen_contact_pcb.py` | placement of the four boards (netlist from the schematic via kicad-cli) |
| `verify_contact.py` | ERC + netlist checks + negative tests |
| `verify_contact_pcb.py` | board vs schematic, DRC, geometry from the board file, negative tests |
| `verify_column.py` | composes a whole column from the board files and checks it against the Enigma scrambler |
| `Christopher.pretty/`, `fp-lib-table` | generated Omniball footprint, project footprint library |

Regenerate and verify (KiCad 10 at `C:/Program Files/KiCad/10.0`, or set `KICAD_SHARE` / `KICAD_CLI`):

    python gen_contact.py && python gen_contact_pcb.py
    python verify_contact.py && python verify_contact_pcb.py
    "%LOCALAPPDATA%/christopher/venv/Scripts/python.exe" verify_column.py [--exhaustive II-IV-V | ALL]

Build output goes to `%LOCALAPPDATA%/christopher/contact-board`; nothing is written into the repository except the generated files.

## Results (KiCad 10.0.4)

| Item | Result |
|---|---|
| ERC (all severities) | contact_board 0, column_terminal 0, reflector_B 0, reflector_C 0 |
| Parts | station: 104 Omniball + 4 IDC headers (+ 4 board holes, not in the schematic); terminal: 2 D-sub 37 + 2 IDC + 4 plated CHASSIS holes; card: 2 IDC (+ 4 NPTH) |
| Netlist checks | all 104 contacts on 2-pad nets (contact ring r letter j - header J r pin j+1); terminal J1/J2 pin k - K1/K2 pin k, pins 27-37 open; cards equal the reflector tables |
| Placement DRC | **0 violations**; unconnected items = ratsnest: station 104, terminal 59, card 26 (pcbnew count equals sum(pads-1)) |
| Geometry from the board file | all 104 contacts on r = 22/29/36/43 within 0.005 mm and on the 13.846 deg grid within 0.005 deg (counter-clockwise), letter = position; headers on the back with pin 1 at the expected end |
| Column vs scrambler | 60 wheel orders of I-V (reflector B) x 40 drum positions (incl. corners 0/25 of all drums): 2400/2400 identical; **exhaustive `--exhaustive ALL`: all 60 orders x all 17,576 positions = 1,054,560/1,054,560 identical** (about 15 min); model vs `Bombe.trace` (one-edge menu, Rust core): 72/72 identical |
| Negative tests | netlists: swapped header/terminal/card pins, wrong card: detected; PCB: swapped header pad nets, a contact moved 1 mm, moved 0.5 mm radially: detected; column: mirrored board, swapped ribbon pins (3 ribbons), reflector C instead of B, reversed drum offsets, OUT ribbon on the wrong ring: detected |

Note on the reflector card: exchanging its two ribbons (ring 2 <-> ring 3) is *not* a fault - the reflector is an involution, so
ring3[j]-ring2[R[j]] and ring2[j]-ring3[R[j]] are the same set of connections; the card is symmetric and cannot be plugged in "wrong"
in that respect.

## Open questions / not done

1. **Omniball tail hole and pad** (assumed Ø1.7 / Ø2.8): confirm with the Mill-Max 7845-0 datasheet; check that hole and pad also keep the
   0.95 mm body gap on ring 1 and the pad at r = 20.6 clear of the Ø38 hole (1.6 mm now).
2. **Contact rotation sense** (counter-clockwise on the front) rests on the drum README's "letters clockwise seen from the pad side".
   If the mechanical design turns the drum the other way, regenerate with `--contacts-cw`; `verify_column.py` must then read the board as mirrored
   (its "board mirrored" negative test shows what a wrong sense does: every case fails).
3. **Reflector card is a 2-header card.** concept.md 3.1 says "26-pin header"; a card joining ring 3 to ring 2 needs 52 terminals, so two
   2 x 13 headers (ribbons) are used. Thin reflectors (B-THIN, C-THIN) for the M4 are a one-line addition (`reflector_sheet("B-THIN")`), not generated.
   The cards are placed only (routing = permutation, 26 nets).
4. **Terminal placement/size** (116 x 62) is a proposal; the real unit-jack row (pitch 120, jacks stacked IN above OUT) and the length
   of the 26-way ribbons (stations to the panel) are mechanical design. No panel cut-out drawing yet.
5. Header orientation (ribbon polarity) is given by the pin-1 rule above; check with the real IDC sockets that "same orientation on both
   ends" gives pin n - pin n for the 45 deg corner mounting.
6. Header height versus the 16.4 mm post and ribbon bend radius, back clearance of the vertical IDC headers (9.95 mm courtyard in the corner): mechanical check.
7. Contact-board CHASSIS: mounting holes are bare (non-plated); if the posts should bond to the frame (PE) a plated variant is a one-line change.
8. Routing: 104 nets from the rings to the corner headers (4 layers reserved); user task.
