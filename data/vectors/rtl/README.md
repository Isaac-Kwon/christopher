# RTL reference vectors

Produced by `rtl/tb/export_vectors.py` (Python package `christopher` over the Rust core; run it with the
project venv's Python, e.g. `"$LOCALAPPDATA/christopher/venv/Scripts/python.exe" rtl/tb/export_vectors.py`).
Letters are 0..25 (A = 0); positions are core offsets `[greek, left, middle, right]` at the first crib letter.
Menus are prefixes of the 14-edge menu in `enigma-i_B-III-V-II.jsonl` (test letter E, input wire A).

## Core vectors (JSONL; one `bombe_core` run per line)
Fields: `wheel_order`, `position`, `edges` `[plain, cipher, pos]`, `test_letter`, `input_wire`, `live` (26 masks, `live[c]` bit w),
`stop`, and (new files) `diagonal` (bool), `turnover_at` (int or null). Files without the last two use diagonal board on, no turnover.
Each file holds the (thinned) stops of its wheel order plus random non-stop positions; `live` is `Bombe.trace` (full fixed point).

| File | Case |
|---|---|
| `enigma-i_B-III-V-II.jsonl` | original (`christopher.vectors.export_vectors`), 14 edges, 1 stop + 64 samples |
| `enigma-i_turnover5_B-I-II-III.jsonl` | 11 edges, `turnover_at=5` |
| `enigma-i_nodiag_B-II-IV-V.jsonl` | 13 edges, diagonal board off |
| `enigma-i_weak_C-III-V-II.jsonl` | 8 edges, reflector C (many stops) |
| `m4_B-THIN_BETA_II-IV-I.jsonl` | M4, 11 edges |
| `m4_C-THIN_GAMMA_VI-VIII-VII_turnover9.jsonl` | M4, C-THIN/GAMMA, 11 edges, `turnover_at=9` |

## Sweep references (JSON; one `bombe_sweep` run each)
`{name, wheel_order, edges, test_letter, input_wire, diagonal, turnover_at, positions_tested, stops: [{pos, register}]}`;
`stops` is the complete ordered `Bombe.run` list (sweep order: greek, left, middle, right), `register` is the test-register mask.

| File | Stops / positions |
|---|---|
| `sweep_enigma-i_B-III-V-II_14edges.json` | 1 / 17576 |
| `sweep_enigma-i_B-III-V-II_11edges.json` | 82 / 17576 |
| `sweep_enigma-i_C-V-IV-I_13edges_nodiag_turnover5.json` | 16 / 17576 |
| `sweep_m4_B-THIN_BETA_II-IV-I_11edges.json` | 2187 / 456976 (tb compares first 40 by default, all with `M4_FULL=1`) |
