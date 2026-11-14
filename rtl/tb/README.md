# RTL testbenches (cocotb)

Run everything from WSL (Ubuntu) with `rtl/tb/run.sh [pytest args]`.

* `SIM=verilator` (default) or `SIM=icarus`; `M4_FULL=1` runs the whole 456,976-position M4 sweep; `WAVES=1` dumps waves.
* Needs OSS CAD Suite at `~/opt/oss-cad-suite` and a venv at `~/venvs/christopher-rtl` with `cocotb` (2.x) and `pytest`
  (override with `CHRISTOPHER_VENV`). Build directories go to `~/build/christopher-rtl` (`CHRISTOPHER_BUILD`), never into the repo.
* Filter: `rtl/tb/run.sh -k scrambler` (also `core`, `sweep`, `refmodel`). `--durations=0` is on by default; cocotb prints per-test real time.

## Files

| File | Purpose |
|---|---|
| `test_rtl.py` | pytest entry: builds each top level with `cocotb_tools.runner` and runs one cocotb module |
| `cocotb_scrambler.py` | `scrambler` vs the Python reference: 3000 random orders (all reflectors, greek on/off), exhaustive right-rotor sweep, AAAAA -> BDZGO |
| `cocotb_core.py` | `bombe_core` vs every vector in `data/vectors/rtl/*.jsonl`: `early_exit=0` compares `live`, `stop`, `test_register`; `early_exit=1` compares `stop`, `test_register` |
| `cocotb_sweep.py` | `bombe_sweep` vs `Bombe.run` stop lists (order, registers, `positions_tested`), with random `stop_ready` backpressure and a hold-while-stalled check; M4 compares the first 40 stops |
| `refmodel.py` | pure-Python scrambler (from `data/wirings/enigma.txt`), order parsing, vector loading |
| `test_refmodel.py` | checks the reference itself (BDZGO, involution without fixed points, B-THIN+BETA = B) — no simulator needed |
| `tbcommon.py` | clock/reset, port packing, configuration driving (unused edge slots and greek id get random junk) |
| `export_vectors.py` | regenerates the vector files from the Python package (Rust core); see `data/vectors/rtl/README.md` |

Testbenches drive and sample on the falling clock edge. Failure messages show letters (A..Z), the drum position and the menu.
