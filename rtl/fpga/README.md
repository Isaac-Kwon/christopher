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
| 0x000 | CTRL | W | bit0 START (begins a sweep with the current configuration), bit1 ABORT |
| 0x004 | STATUS | R | bit0 BUSY, bit1 DONE (sticky until next START), bit2 STOP_AVAILABLE, bit3 STOP_OVERFLOW (sticky) |
| 0x008 | ORDER | RW | [1:0] reflector id, [2] greek_en, [7:4] greek id, [11:8] left id, [15:12] middle id, [19:16] right id |
| 0x00C | OPTIONS | RW | [4:0] test letter, [12:8] input wire, [16] diagonal_en, [17] turnover_en, [31:24] turnover_at |
| 0x010 | EDGE_EN | RW | bit i enables edge i (MAX_EDGES ≤ 32) |
| 0x014 | POSITIONS | R | positions tested in the current/last sweep |
| 0x018 | CYCLES | R | clock cycles of the current/last sweep (wraps) |
| 0x01C | STOP_POS | R | head of the stop FIFO: [31] valid, [24:20] greek, [19:15] left, [14:10] middle, [9:5] right |
| 0x020 | STOP_REG | R | head of the stop FIFO: [25:0] test register; **reading STOP_REG pops the entry** |
| 0x024 | PARAMS | R | [7:0] MAX_EDGES, [15:8] stop FIFO depth (log2), [31:16] engine version |
| 0x100 + 4*i | EDGE[i] | RW | [4:0] plain letter a, [12:8] cipher letter b, [23:16] crib position |

Ids and letters follow `rtl/README.md` (rotor 0=I … 7=VIII, 8=BETA, 9=GAMMA; reflector 0=B 1=C
2=B-THIN 3=C-THIN; letters 0..25). Positions are core offsets at the first crib letter — identical
to the Rust `Position` and to `rtl/src/bombe_sweep.sv`; the engine sweeps in the same order
(greek outermost, right innermost), so its stop list must equal `bombe_sweep`'s and
`christopher_core::bombe::run_slice`'s element by element.

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
