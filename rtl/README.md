# RTL — Phase 2

SystemVerilog model of the bombe, verified against the Rust core (`crates/christopher-core`).
Simulators: Verilator (primary) and Icarus Verilog, from OSS CAD Suite in WSL. Testbenches: cocotb.

## Layout

```
rtl/
├── gen/gen_wirings.py     generates src/wirings_pkg.sv from data/wirings/enigma.txt (single source of truth)
├── src/
│   ├── wirings_pkg.sv     GENERATED — rotor/reflector tables and lookup functions
│   ├── scrambler.sv       one drum set: 26-entry permutation for given wheel order + core offsets
│   ├── bombe_core.sv      one bombe position: menu wiring, diagonal board, spreading to a fixed point
│   └── bombe_sweep.sv     drives bombe_core through every drum position, emits stops
└── tb/                    cocotb testbenches (see tb/README.md)
```

## Conventions

* Letters are 5-bit values 0..25 (A = 0).
* Rotor ids: 0=I 1=II 2=III 3=IV 4=V 5=VI 6=VII 7=VIII 8=BETA 9=GAMMA.
  Reflector ids: 0=B 1=C 2=B-THIN 3=C-THIN. (Order of `data/wirings/enigma.txt`.)
* **Position** = core offsets (window − ring) of the Enigma rotors at the moment the *first crib
  letter* is enciphered — identical to `christopher_core::bombe::Position`. Edge with crib position
  `pos` uses right core `(pos_r + pos) mod 26`, and middle core `pos_m + 1` when
  `turnover_en && pos >= turnover_at`.
* **Live state** = 26 cables × 26 wires, packed as `live[c*26 + w]` (cable c = letter c, wire w).
  Identical to the Rust `[u32; 26]` where `live_rust[c] bit w`.

## `scrambler` (combinational)

```systemverilog
module scrambler (
  input  logic [1:0] refl_id,
  input  logic       greek_en,
  input  logic [3:0] greek_id, left_id, middle_id, right_id,
  input  logic [4:0] core_g, core_l, core_m, core_r,
  output logic [25:0][4:0] perm        // perm[c] = scrambler output for input letter c
);
```

## `bombe_core`

```systemverilog
module bombe_core #(parameter int MAX_EDGES = 16) (
  input  logic clk, rst_n,
  // wheel order
  input  logic [1:0] refl_id,
  input  logic       greek_en,
  input  logic [3:0] greek_id, left_id, middle_id, right_id,
  // menu (edge i: plain letter edge_a[i] — cipher letter edge_b[i] at crib position edge_pos[i])
  input  logic [MAX_EDGES-1:0]      edge_en,
  input  logic [MAX_EDGES-1:0][4:0] edge_a,
  input  logic [MAX_EDGES-1:0][4:0] edge_b,
  input  logic [MAX_EDGES-1:0][7:0] edge_pos,
  input  logic [4:0] test_letter, input_wire,
  input  logic       diagonal_en,
  input  logic       turnover_en,
  input  logic [7:0] turnover_at,
  input  logic       early_exit,      // finish as soon as the test register is fully live
  // drum position (core offsets at the first crib letter)
  input  logic [4:0] pos_g, pos_l, pos_m, pos_r,
  // control: pulse start for one cycle; hold all inputs stable until done
  input  logic       start,
  output logic       busy,
  output logic       done,            // one-cycle pulse; outputs below valid from then until next start
  output logic       stop,            // test register not fully live
  output logic [25:0] test_register,
  output logic [26*26-1:0] live,      // complete fixed point when early_exit = 0
  output logic [15:0] iterations      // spreading cycles used
);
```

Timing: `start` → one cycle to latch the edge permutations → spreading, one cycle per step (every
edge, both directions, and the diagonal board in parallel) until nothing changes (or the test
register is full with `early_exit`) → `done`.

## `bombe_sweep`

Same wheel-order/menu/option inputs as `bombe_core` (no `pos_*`, no `early_exit` — always 1).

```systemverilog
  input  logic        start,
  output logic        busy,
  output logic        done,            // one-cycle pulse after the last position
  output logic        stop_valid,      // valid/ready handshake; sweep stalls while not accepted
  input  logic        stop_ready,
  output logic [4:0]  stop_g, stop_l, stop_m, stop_r,
  output logic [25:0] stop_register,
  output logic [31:0] positions_tested
```

Sweep order: greek (only if `greek_en`, else 0) outermost, then left, middle, right innermost —
the same order as `christopher_core::bombe::run_slice`, so stop lists compare element by element.
