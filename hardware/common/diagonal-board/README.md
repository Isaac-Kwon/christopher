# Diagonal board, five boards (common part)

Generated KiCad 10 schematics and **placement-only** PCBs of Welchman's diagonal board, split in five boards as decided in
`docs/decisions/0002-gear-detail-decisions.md` section 9 and `hardware/common/diagonal-board.md`; connector rules from
`docs/decisions/0003-interfaces.md` (5.1 gender, 5.2 pins, 7 grounding/CHASSIS). The letter jacks live on these boards
(design brief 4.3/4.4); everything else of the menu panel is on `hardware/common/patch-panel`. Build products of the scripts, do not edit by hand.

## Design

| Board | Letters (jack columns: upper / lower) | Jacks | Junction headers | Size (placed) |
|---|---|---:|---|---|
| D1 | A B C / N O P | 12 | 3 right | 336.9 x 130 |
| D2 | D E F / Q R S | 12 | 3 left, 3 right | 369.3 x 130 |
| D3 | G H I / T U V | 12 | 3 left, 3 right | 369.3 x 130 |
| D4 | J K / W X | 8 | 3 left, 3 right | 259.3 x 130 |
| D5 | L M / Y Z | 8 | 3 left | 226.9 x 130 |

* **Letter jack** (2 per letter, 52 in all): male D-sub 37 (`DSUB-37_Pins_Vertical..._MountingHoles`, shells through the panel), pins 1-26 =
  wire A..Z of the letter's cable, **27-29 = 0 V reference tied here** (common side), 30-37 not connected, shell pads = `CHASSIS`. The
  two jacks of a letter are in parallel (all 26 lines). Panel order as in `layout.md` 3.10: jack columns at 110 mm pitch, upper letter's two
  jacks in rows 1-2, lower letter's in rows 3-4 (30 mm row pitch).
* **Diagonal links:** wire w of letter c <-> wire c of letter w; 325 link nets `L_XY` (X < Y) with four jack pins each (2 jacks of X, 2 of Y),
  26 self nets `S_X` (the own wire, two pins, linked to nothing).
* **Junctions:** per boundary 3 x 64-pin IDC headers (2 x 32, 2.54): `XRn` on the right edge of board k, `XLn` on the left edge of board k+1
  (n = 1..3), 64-way flat cable, pin p to pin p of the same n. The links crossing the boundary are sorted by (X, Y); link i takes connector
  i // 64 + 1, pin i % 64 + 1; a link passing over a board joins its left and right header pin. Crossing links per boundary k(26-k):
  **120 / 168 / 144 / 88** (verified); junction pins spare: 68 / 20 / 44 / 100 after reserving **connector 3, pins 61-64 for the 0 V reference**
  (each boundary, all four pins). Headers are on the **back**, vertical, pin 1 at the top, identical orientation on every board, three side by side (pitch 10.8).
* **Grounding:** one `GND` net per board, joined across boards by the four 0 V junction pins; `J0V` (2-pole 5.08 terminal, both pins one net) on D1 is the single
  connection to the 0 V of the power supply (the patch panel has the other `J0V`: connect both to the same 0 V point, never to PE). Each board has `CHASSIS` (shells + M3
  mounting holes with plated pads) tied to 0 V only through 1 Mohm || 10 nF (R1, C1; decision 0003 7.1).
* 4 layers (routing density 168 through-nets over D2/D3; the user routes), 4 or 6 M3 holes.

## Files

| File | Purpose |
|---|---|
| `ki_common.py` | shared library (identical copy in `gear/contact-board` and `common/patch-panel`) |
| `gen_diagonal.py` | letters + 325 links + junction pin assignment -> `d1..d5.kicad_pro/.kicad_sch` |
| `gen_diagonal_pcb.py` | placement -> `d1..d5.kicad_pcb` (netlist via kicad-cli) |
| `verify_diagonal.py` | composes the five netlists through the junction ribbons and checks the diagonal board, with negative tests |
| `verify_diagonal_pcb.py` | board vs schematic, DRC, geometry (pcbnew), negative test |

    python gen_diagonal.py && python gen_diagonal_pcb.py
    python verify_diagonal.py && python verify_diagonal_pcb.py        # the PCB check takes a few minutes (kicad-cli, 5 boards)

KiCad 10 at `C:/Program Files/KiCad/10.0` (or `KICAD_SHARE`, `KICAD_CLI`); build output in `%LOCALAPPDATA%/christopher/diagonal-board`.

## Results (KiCad 10.0.4)

| Item | Result |
|---|---|
| ERC (all severities) | d1..d5: 0 violations |
| Parts | D1 24 (12 jacks, 3 headers, J0V, R1, C1, 6 holes), D2 26, D3 26, D4 20, D5 17 |
| Composition (independent of the generator's net names) | letters 6/6/6/4/4; **325 link nets, each exactly four jack pins**, 26 self nets of two pins; no other jack-pin connection; pins 27-29 of all 52 jacks on one 0 V net (with `J0V`, one RC per board); five CHASSIS nets, not shorted to 0 V; junction use 120/168/144/88 |
| Negative tests | swapped wires on a jack, wire <-> 0 V pin on a jack, two junction pins swapped (near and far side), signal <-> 0 V junction pin swapped, missing ribbon (signal connectors), missing 0 V ribbon: all detected |
| Placement DRC | **0 violations** on all five; unconnected = ratsnest (D1 365, D2 451, D3 463, D4 324, D5 248; pcbnew count = sum(pads-1)); pad nets = schematic (IPC-D-356); jacks on the 110 x 30 grid, headers on the back, pin 1 at the top, same pin-1 height at both ends of every ribbon; negative test (swapped pad nets) detected |

## Open questions / deviations from the concept documents

1. **Panel pitch and board size.** The three 10.8 mm-pitch headers (32 mm) at each edge do not fit into the 40 mm gap between two jacks of the 110 mm
   panel grid together with the neighbour's headers. The boards therefore carry the jacks at 110 mm pitch *inside* a board but the last/first jack of
   neighbouring boards are **149.3 mm** apart (+39.3 mm at each of the 4 boundaries). Total width of the five boards 1,561.7 mm (the concept said about 1,430);
   D2/D3 are 369 mm long (concept: 330-340, `layout.md`: at most 350). Both fit the 1,800 panel; the panel cut-outs must follow the 149.3 mm spacing, or the headers must move
   behind the jack rows (only possible with a taller board). Maker size limits (400-500 mm) are still to be confirmed.
2. 0 V reference wiring: the four 0 V junction pins and `J0V` are an interpretation of "27-29 tied to 0 V at the panel" (0003 5.2 and the table in section 8); confirm that 4 ribbon conductors
   per boundary and one external lead are acceptable (0.2 A max on the reference).
3. Ribbon polarity: all headers are mounted identically (back side, pin 1 up); the assumption "pin n to pin n" for the flat cables must be confirmed with real sockets.
4. The vertical D-sub footprint has two plated shield pads per jack (`SH`, both on CHASSIS); the shell-to-panel contact itself is a mechanical matter.
5. Thickness/stack-up, ENIG, fab size limits, and 4 layers vs 6 for 168 through-nets are routing-phase decisions.
6. Routing is not done (user rule); the through-nets in D2/D3 need about 168 lanes (diagonal-board.md section 3: 4 layers suffice).
