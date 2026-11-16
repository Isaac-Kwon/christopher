# Diagonal board, five boards (common part)

Generated KiCad 10 schematics and **placement-only** PCBs of Welchman's diagonal board, split in five boards as decided in
`docs/decisions/0002-gear-detail-decisions.md` section 9 and `hardware/common/diagonal-board.md`; connector rules from
`docs/decisions/0003-interfaces.md` (5.1 gender, 5.2 pins, 7 grounding/CHASSIS). The letter jacks live on these boards
(design brief 4.3/4.4); everything else of the menu panel is on `hardware/common/patch-panel`. Build products of the scripts, do not edit by hand.

## Design

| Board | Letters (jack columns: upper / lower) | Jacks | Junction headers | Size (placed) | Left edge on the panel |
|---|---|---:|---|---|---:|
| D1 | A B C / N O P | 12 | 3 right (edge) | 331.6 x 130 | 17.8 |
| D2 | D E F / Q R S | 12 | 3 left (gap col 1-2), 3 right (edge) | 327.6 x 130 | 351.8 |
| D3 | G H I / T U V | 12 | 3 left (gap col 1-2), 3 right (edge) | 327.6 x 130 | 681.8 |
| D4 | J K / W X | 8 | 3 left (gap col 1-2), 3 right (edge) | 217.6 x 130 | 1,011.8 |
| D5 | L M / Y Z | 8 | 3 left (gap col 1-2) | 190.5 x 130 | 1,231.8 |

Panel x is measured from the left end of the 1,440 mm bank span (`hardware/gear/layout.md` 3.9/3.10; the relay machine's panel is the
same). The five boards cover 17.8 ... 1,422.2 (1,404.5 mm, 2.4 mm air between boards, 17.8 mm free at each end).

* **Letter jack** (2 per letter, 52 in all): male D-sub 37 (`DSUB-37_Pins_Vertical..._MountingHoles`, shells through the panel), pins 1-26 =
  wire A..Z of the letter's cable, **27-29 = 0 V reference tied here** (common side), 30-37 not connected, shell pads = `CHASSIS`. The
  two jacks of a letter are in parallel (all 26 lines). Panel order as in `layout.md` 3.10: **one 13 x 4 grid at 110 x 30 mm over all
  five boards** (the 110 mm column pitch also holds across a board boundary), column centres at 60 + 110 j (j = 0..12) from the left end of
  the bank span, upper letter's two jacks in rows 1-2, lower letter's in rows 3-4. The grid is 1,390.5 mm wide (jack courtyards).
* **Diagonal links:** wire w of letter c <-> wire c of letter w; 325 link nets `L_XY` (X < Y) with four jack pins each (2 jacks of X, 2 of Y),
  26 self nets `S_X` (the own wire, two pins, linked to nothing).
* **Junctions:** per boundary 3 x 64-pin IDC headers (2 x 32, 2.54): `XRn` on the right edge of board k, `XLn` on the left edge of board k+1
  (n = 1..3), 64-way flat cable, pin p to pin p of the same n. The links crossing the boundary are sorted by (X, Y); link i takes connector
  i // 64 + 1, pin i % 64 + 1; a link passing over a board joins its left and right header pin. Crossing links per boundary k(26-k):
  **120 / 168 / 144 / 88** (verified); junction pins spare: 68 / 20 / 44 / 100 after reserving **connector 3, pins 61-64 for the 0 V reference**
  (each boundary, all four pins). Headers are on the **back**, vertical, pin 1 at the top, identical orientation on every board, three side by side (pitch 10.8).
* **Where the headers sit (staggered).** A block of three headers is 31.6 mm wide; the gap between two jack columns is 39.5 mm, so only
  ONE block fits into a gap. At each boundary, `XR1..XR3` of board k fill the boundary gap at the right edge of board k (XR1 nearest the edge);
  `XL1..XL3` of board k+1 sit in the gap between its jack columns 1 and 2 (XL1 nearest the boundary). The three ribbons of a boundary
  therefore **nest** (XR1-XL1 innermost; pin-1 spans 91.4 / 113.0 / 134.6 mm) and pass over the back of the first jack column of board k+1.
  Ribbon length about 150-200 mm (to be measured with real sockets). The earlier layout (both blocks at the edges, XR1 and XL1 both on the
  left of their blocks) would also have crossed the ribbons: equal spans shifted by 10.8 mm cannot be laid as flat U-loops.
* **Grounding:** one `GND` net per board, joined across boards by the four 0 V junction pins; `J0V` (2-pole 5.08 terminal, both pins one net) on D1 is the single
  connection to the 0 V of the power supply (the patch panel has the other `J0V`: connect both to the same 0 V point, never to PE). Each board has `CHASSIS` (shells + M3
  mounting holes with plated pads) tied to 0 V only through 1 Mohm || 10 nF (R1, C1; decision 0003 7.1).
* 4 layers (routing density 168 through-nets over D2/D3; the user routes), 4 or 6 M3 holes.

## Files

| File | Purpose |
|---|---|
| `ki_common.py` | shared library (identical copy in `gear/contact-board` and `common/patch-panel`) |
| `gen_diagonal.py` | letters + 325 links + junction pin assignment -> `d1..d5.kicad_pro/.kicad_sch` |
| `gen_diagonal_pcb.py` | placement -> `d1..d5.kicad_pcb` (netlist via kicad-cli), board positions on the panel (`panel_offsets`), kicad-cli helpers |
| `verify_diagonal.py` | composes the five netlists through the junction ribbons and checks the diagonal board, with negative tests |
| `verify_diagonal_pcb.py` | ERC, board vs schematic, DRC, geometry (pcbnew), the five boards on the panel (one jack grid, ribbons), determinism, negative tests |

    python gen_diagonal.py && python gen_diagonal_pcb.py
    python verify_diagonal.py && python verify_diagonal_pcb.py        # the PCB check takes a few minutes (kicad-cli, 5 boards)

KiCad 10 at `C:/Program Files/KiCad/10.0` (or `KICAD_SHARE`, `KICAD_CLI`); build output in `%LOCALAPPDATA%/christopher/diagonal-board`.
kicad-cli and pcbnew run on copies in `<build>/stage` with a timeout (it sometimes hangs on the synced work folder, and leaves
`.kicad_prl`/lock files next to the file it opens).

## Results (KiCad 10.0.4)

| Item | Result |
|---|---|
| ERC (all severities) | d1..d5: 0 violations |
| Parts | D1 24 (12 jacks, 3 headers, J0V, R1, C1, 6 holes), D2 26, D3 26, D4 20, D5 17 |
| Composition (independent of the generator's net names) | letters 6/6/6/4/4; **325 link nets, each exactly four jack pins**, 26 self nets of two pins; no other jack-pin connection; pins 27-29 of all 52 jacks on one 0 V net (with `J0V`, one RC per board); five CHASSIS nets, not shorted to 0 V; junction use 120/168/144/88 |
| Negative tests | swapped wires on a jack, wire <-> 0 V pin on a jack, two junction pins swapped (near and far side), signal <-> 0 V junction pin swapped, missing ribbon (signal connectors), missing 0 V ribbon: all detected |
| Placement DRC | **0 violations** on all five; unconnected = ratsnest (D1 365, D2 451, D3 463, D4 324, D5 248; pcbnew count = sum(pads-1)); pad nets = schematic (IPC-D-356); jacks on the 110 x 30 grid, headers on the back, pin 1 at the top, same pin-1 height at both ends of every ribbon; negative test (swapped pad nets) detected |
| Panel (five boards together) | jack column pitch 110.00 over all 13 columns (also across the 4 boundaries), first column centre 60.00; boards 17.7 ... 1,422.3 of the 1,440 bank span (outline incl. line width), air 2.3 between boards, longest 331.7 <= 350; the ribbons of every boundary nest, pin-1 spans 91.4 / 113.0 / 134.6 mm; negative tests (a board shifted 1 mm, two headers of a block exchanged, a board 30 mm longer, all boards 15 mm to the right) detected |
| Determinism | schematics and boards regenerate byte-identical |

## Open questions / deviations from the concept documents

1. **Panel pitch and board size (resolved 2026-11-16).** The first placement put both header blocks of a boundary at the board edges; two blocks
   (2 x 32 mm) do not fit into the 39.5 mm gap between two jack columns, so the jacks of neighbouring boards were 149.3 mm apart and the five boards
   1,561.7 mm long, 121.7 mm more than the 1,440 mm bank span (they ran into the control area, where the power-unit plate also sits). Now the blocks
   are staggered (section Design): every gap holds at most one block, the 110 mm pitch holds everywhere, the boards are 1,404.5 mm in all and fit the
   bank span with 17.8 mm at each end, and the longest board is 331.6 mm (`layout.md`: at most 350). Height 130 unchanged. Alternatives that were
   rejected (headers under the jack rows: +90 mm board height or collinear headers that a flat ribbon cannot join; fewer/longer boards: one
   edge-type boundary alone already gives 1,443.8 mm; a smaller pitch: 89.8 mm would be needed; moving the control area: conflicts with the power
   unit and keeps the uneven grid) are in `hardware/common/diagonal-board.md` section 7. Maker size limit assumed 400 mm for 4 layers (to confirm).
2. 0 V reference wiring: the four 0 V junction pins and `J0V` are an interpretation of "27-29 tied to 0 V at the panel" (0003 5.2 and the table in section 8); confirm that 4 ribbon conductors
   per boundary and one external lead are acceptable (0.2 A max on the reference).
3. Ribbon polarity: all headers are mounted identically (back side, pin 1 up); the assumption "pin n to pin n" for the flat cables must be confirmed with real sockets.
4. The vertical D-sub footprint has two plated shield pads per jack (`SH`, both on CHASSIS); the shell-to-panel contact itself is a mechanical matter.
5. Thickness/stack-up, ENIG, fab size limits, and 4 layers vs 6 for 168 through-nets are routing-phase decisions.
6. Routing is not done (user rule); the through-nets in D2/D3 need about 168 lanes (diagonal-board.md section 3: 4 layers suffice).
