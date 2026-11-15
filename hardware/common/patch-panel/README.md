# Menu patch panel board (common part)

Generated KiCad 10 schematic and **placement-only** PCB of the small board behind the operator panel that carries what the menu
plugging needs besides the letter jacks. Design basis: `hardware/common/design-brief.md` 4.3 (patch panel), 4.5 (test register, test voltage),
`hardware/common/diagonal-board.md` section 4 (what is *not* on the diagonal boards), `hardware/common/test-register/README.md` (J1 cable, J5 TEST lead),
`docs/decisions/0003-interfaces.md` 5.1, 5.2, 7. Build product of the scripts, do not edit by hand.

## Decision: letter jacks stay on the diagonal boards

The design brief puts the letter jacks on the diagonal-board backplane (4.4: "glyph jacks on a backplane PCB with fixed wiring"), and
`diagonal-board.md` section 4 says commons, register-cable jack and searching jack are *not* part of the diagonal nets and go on a separate small
board. So the patch panel is **not folded** into the diagonal boards; it is this separate board (339.5 x 160 mm, one board).

## Content

| Part | Count | Function |
|---|---:|---|
| `CM11..CM44` commons | 4 groups x 4 male D-sub 37 | the four jacks of a group are in parallel (26 lines): one letter can feed more than two scramblers |
| `JR1` / `JR2` register jack | 2 male D-sub 37 | `JR1` front (operator), `JR2` rear (cable to the test register J1), parallel; a cable between the test letter's jack and `JR1` brings that letter's 26 wires to the register |
| `JS1` searching jack + `TS1` hole field | 1 jack + 26 holes (2 x 13 pin socket) | a cable from the test letter's second jack to `JS1`; line k appears at hole k (silk A..Z) |
| `J_TEST` | 2-pole 5.08 terminal | 1 = `TEST` (register J5, +24 V during STROBE), 2 = 0 V |
| `PL1` test plug | flying lead (in the schematic, `on_board = no`) | the end of the TEST lead, pushed into ONE hole of `TS1` = the wire that gets the test voltage (the menu's input wire) |
| `J0V` | 2-pole 5.08 terminal (both pins one net) | the 0 V reference: ONE connection to the 0 V of the power supply (and to `J0V` on diagonal board D1) |
| `R1`, `C1`, 6 plated M3 holes | | `CHASSIS` (shells, holes) to 0 V by 1 Mohm || 10 nF (decision 0003 7.1) |

All jacks are **male** (decision 0003 5.1: boards and panel pins, cables female at both ends, 1:1), pins 1-26 = wire A..Z, **pins 27-29 tied to 0 V here**
(common side; the scrambler-side boards never tie them), 30-37 not connected, shell pads = `CHASSIS`. Layout: 85 x 30 mm grid, rows 1-4 = commons 1-4,
row 5 = `JR1`, `JR2`, `JS1` and the fourth slot with `TS1` (horizontal, hole letters on the silk), `J_TEST`, `J0V`. 4 layers (26-line commons).

## Files

| File | Purpose |
|---|---|
| `ki_common.py` | shared library (identical copy in the other two folders) |
| `gen_patch.py` | -> `patch_panel.kicad_pro/.kicad_sch` |
| `gen_patch_pcb.py` | placement -> `patch_panel.kicad_pcb` |
| `verify_patch.py` | ERC + netlist connectivity + test-plug simulation + negative tests |
| `verify_patch_pcb.py` | board vs schematic, DRC, geometry (pcbnew), negative test |

    python gen_patch.py && python gen_patch_pcb.py && python verify_patch.py && python verify_patch_pcb.py

Build output in `%LOCALAPPDATA%/christopher/patch-panel`.

## Results (KiCad 10.0.4)

| Item | Result |
|---|---|
| ERC (all severities) | 0 violations |
| Parts | 30 (19 jacks, `TS1`, `J_TEST`, `J0V`, `R1`, `C1`, 6 holes) + the flying plug `PL1` |
| Connectivity | each common wire = exactly the 4 jack pins of its group; `JR1`/`JR2` pin k on one net; `JS1` pin k = hole k only; 0 V net = pins 27-29 of all 19 jacks + `J_TEST` 2 + `J0V` + RC, no signal on it; CHASSIS separate from 0 V |
| Test-plug simulation | plug out: `TEST` isolated; plug in hole k (k = 1..26): `TEST` reaches exactly `JS1` pin k and hole k, nothing else |
| Negative tests | swapped wires on a common jack / on `JS1` / in the hole field, `TEST` <-> 0 V swapped on `J_TEST`, wire <-> 0 V pin on `JR2`: all detected |
| Placement DRC | **0 violations**; unconnected = ratsnest 470 (pcbnew count = sum(pads-1)); pad nets = schematic; grid and hole-field order checked from the board; negative test detected |

## Open questions

1. Who connects the register: the permanent register cable (J1) could also go straight to a letter jack; `JR1`/`JR2` are provided because the brief lists a register-cable jack on the panel.
   If the permanent cable is preferred, `JR2`/`JR1` can be dropped.
2. The test plug is a one-pin flying lead into a 2.54 pin-socket field. A sturdier banana/4 mm field and a plug with shrouded 24 V contact (touch protection, 24 V is extra-low voltage)
   are a panel-design choice; contact wear counts like the letter jacks.
3. Whether TEST should also be offered on a third letter (the British machine had a test plug per bank) is outside this design.
4. Board position and size (339.5 x 160) follow the commons grid (layout.md 3.10 "4 x 4 below the drive"); the real cut-out drawing is a mechanical task.
5. Routing is not done (user rule): 16 x 26 common nets (4 pads each), 26 register nets, 26 search nets.
