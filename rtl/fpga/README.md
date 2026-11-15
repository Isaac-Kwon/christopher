# FPGA bombe — Phase 2 hardware

Targets: **Nexys Video** (Artix-7 XC7A200T-1SBG484C, 100 MHz board clock, USB-UART) and
**PYNQ-Z1/Z2** (Zynq XC7Z020-1CLG400C, PL clock from the PS, AXI4-Lite from the ARM).
Toolchain: Vivado 2025.1 (non-project Tcl flow). Simulation/verification: Verilator + cocotb in WSL.

`rtl/src/*.sv` remains the *reference* RTL (fully parallel, simulation only — one scrambler alone
is ~19.7k LUTs). The FPGA engine below recomputes only what changes, like the real machine.

## Layout

```
rtl/fpga/
├── src/
│   ├── bombe_engine.sv      one bombe (sweep of one wheel order) behind a register bus
│   ├── bombe_array.sv       N engines, register windows at base 0x1000*k (+ global ID/INFO block)
│   ├── rotor_rom.sv         (part of the engine) wiring ROM generated from data/wirings/enigma.txt
│   ├── uart_bridge.sv       Nexys Video: UART <-> register bus
│   └── axil_bridge.sv       PYNQ: AXI4-Lite slave <-> register bus
├── boards/
│   ├── nexys_video/         top.sv, constraints .xdc, build.tcl
│   └── pynq/                block design Tcl (Zynq PS + bridge + array), constraints, build.tcl
├── host/                    Python host: drive engines over UART (PC) or MMIO (PYNQ ARM)
└── tb/                      cocotb tests for the engine/array/bridges
```

## Register bus (inside the FPGA)

Single clock domain. One request per cycle at most:

```systemverilog
input  logic        bus_req,     // request valid (one cycle)
input  logic        bus_we,      // 1 = write
input  logic [15:0] bus_addr,    // byte address, word aligned
input  logic [31:0] bus_wdata,
output logic        bus_ack,     // one cycle, 1..2 cycles after bus_req
output logic [31:0] bus_rdata    // valid with bus_ack (reads)
```

`bombe_array` decodes `bus_addr[15:12]` = engine index (0x0..0xE), `0xF` = global block.

## Engine register map (offsets within an engine's 4 KiB window)

| Offset | Name | Access | Fields |
|---|---|---|---|
| 0x000 | CTRL | W | bit0 START (begins a sweep with the current configuration), bit1 ABORT, bit2 STEP (exhibit mode: release a held engine by one position; ignored unless STATUS.HOLD) |
| 0x004 | STATUS | R | bit0 BUSY, bit1 DONE (sticky until next START), bit2 STOP_AVAILABLE, bit3 STOP_OVERFLOW (sticky), bit4 HOLD (waiting for STEP) |
| 0x008 | ORDER | RW | [1:0] reflector id, [2] greek_en, [7:4] greek id, [11:8] left id, [15:12] middle id, [19:16] right id |
| 0x00C | OPTIONS | RW | [4:0] test letter, [12:8] input wire, [16] diagonal_en, [17] turnover_en, [31:24] turnover_at |
| 0x010 | EDGE_EN | RW | bit i enables edge i (MAX_EDGES ≤ 32) |
| 0x014 | POSITIONS | R | positions tested in the current/last sweep |
| 0x018 | CYCLES | R | clock cycles of the current/last sweep (wraps) |
| 0x01C | STOP_POS | R | head of the stop FIFO: [31] valid, [24:20] greek, [19:15] left, [14:10] middle, [9:5] right |
| 0x020 | STOP_REG | R | head of the stop FIFO: [25:0] test register; **reading STOP_REG pops the entry** |
| 0x024 | PARAMS | R | [7:0] MAX_EDGES, [15:8] stop FIFO depth (log2), [31:16] engine version (2 = exhibit registers present) |
| 0x028 | PACE | RW | exhibit: idle clock cycles after every position (0 = full speed, the default) |
| 0x02C | DEMO | RW | exhibit: bit0 SINGLE_STEP (hold after every position until STEP), bit1 PAUSE_ON_STOP (hold after a stop until STEP) |
| 0x030 | DRUM | R | live drum position, same layout as STOP_POS bits [24:5], bit31 = 0 |
| 0x034 | TESTREG | R | live test register [25:0] (cable `test_letter`, current propagation step) |
| 0x038 | SNAP_POS | R | position of the snapshot: [31] valid, [30] it was a stop, [24:5] g/l/m/r as STOP_POS |
| 0x03C | SNAP_ITERS | R | [15:0] propagation steps the snapshot position needed |
| 0x100 + 4*i | EDGE[i] | RW | [4:0] plain letter a, [12:8] cipher letter b, [23:16] crib position |

| 0x200 + 4*c | SNAP[c] | R | 26-bit snapshot of cable c (c = 0..25, bit w = wire w live), latched when the last position finished |

Ids and letters follow `rtl/README.md` (rotor 0=I … 7=VIII, 8=BETA, 9=GAMMA; reflector 0=B 1=C
2=B-THIN 3=C-THIN; letters 0..25). Positions are core offsets at the first crib letter — identical
to the Rust `Position` and to `rtl/src/bombe_sweep.sv`; the engine sweeps in the same order
(greek outermost, right innermost), so its stop list must equal `bombe_sweep`'s and
`christopher_core::bombe::run_slice`'s element by element.

### Exhibit mode (slow, visible sweeps)

All defaults (`PACE = 0`, `DEMO = 0`) leave the engine exactly as before (same cycles per position).
* **PACE** counts clock cycles (50 MHz system clock, 20 ns each; no prescaler): after each position the
  engine waits PACE cycles before moving the drums on, so one position takes about `16 + PACE` cycles
  (natural time is 12..17). 2 positions/s = `PACE = 24_999_984`; the host computes it
  (`bombe_fpga.driver.pace_for_rate`). Changes take effect on the next position.
* **SINGLE_STEP** (DEMO bit0): START computes position 0 and holds (STATUS.HOLD = 1); every write of
  CTRL.STEP advances exactly one position and holds again. Clearing the bit releases the hold. STEP while
  not holding is ignored. After a STEP, STATUS.HOLD of the old position may be visible for a couple of
  cycles: wait for POSITIONS to change.
* **PAUSE_ON_STOP** (DEMO bit1): runs at the paced speed but holds on every stop (STOP_POS/STOP_REG still
  receive it) until STEP or until the bit is cleared.
* **Snapshot**: when a position finishes (fixed point, or test register full) the 26 cable words are copied
  into a read-only register bank together with SNAP_POS/SNAP_ITERS, so the window is stable while the engine
  computes the next position... except at the instant the next position finishes. For stops the snapshot is
  the full fixed point (equal to `Bombe.trace`); for non-stops the engine abandons the position as soon as the
  test register is full, so the snapshot is a partial state. To read coherently while running, bracket the
  26 reads with two POSITIONS reads (`Engine.snapshot()` does) or hold with SINGLE_STEP/PAUSE_ON_STOP.
* Synthesis: the snapshot is 676 + ~40 plain flip-flops loaded from `cab_q`; the 26:1 readout mux sits on the
  bus side, not on the propagation path.

Host visualizer: `python -m bombe_fpga demo` (fake board, no hardware) or `--uart COM5 demo` (see
`host/bombe_fpga/visual.py`).

When the stop FIFO is full the engine stalls (no stops are lost); STOP_OVERFLOW is reserved for
diagnostics and should never be set.

## Global block (0xF000)

| Offset | Name | Fields |
|---|---|---|
| 0xF000 | ID | 0x424F4D42 ("BOMB") |
| 0xF004 | INFO | [7:0] number of engines, [15:8] MAX_EDGES, [31:16] clock MHz |

## UART protocol (Nexys Video)

8N1, 921,600 baud (generic parameter). Little-endian.

* Write: host sends `0x57 'W'`, addr[15:0], data[31:0] (7 bytes) → FPGA answers `0x06`.
* Read: host sends `0x52 'R'`, addr[15:0] (3 bytes) → FPGA answers data[31:0] (4 bytes).
* Any other first byte is ignored (resynchronisation: host may send 8 × 0x00 then retry).
