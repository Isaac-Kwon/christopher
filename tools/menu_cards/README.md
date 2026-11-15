# menu_cards: exhibit menu-card designer

Finds a key, a German military-style plaintext and a crib so that the crib gives a bombe menu with an
exactly prescribed shape (for example 4 letters / 6 edges / 3 loops for the 6-unit "26 x 26 slice",
or 10 letters / 12 edges / 3 loops for the 12-unit full run), verifies it with the software bombe and
checking machine (`python/christopher`), and writes a JSON record plus a printable A5 card.

Natural cribs almost never give such dense menus, so the plugboard is *engineered*: for chosen crib
positions the cipher letter is `S(sigma_k(S(p_k)))` (S = plugboard, sigma_k = Enigma without
plugboard at that position), and a depth-first search picks S so that every cipher letter lands in
the wanted letter set. Everything that matters is then recomputed with the real software: the real
Enigma enciphers the message, `Menu.from_crib` builds the menu, `Menu.select` picks the test letter
and input wire, `Bombe.run` lists the stops and `Stop.check` recovers the key.

## Usage

Run from `tools/` (needs the `christopher` package; xelatex/latexmk with kotex for the PDFs):

```
python -m menu_cards build                      # the default set: 4 slice + 2 full scenarios
python -m menu_cards build --only slice-6-a     # one of them
python -m menu_cards find --name my-card --mode slice --edges 6 --letters 4 --false-max 1 --seed 5
python -m menu_cards card ../hardware/common/menu-cards/slice-6-a.json   # rebuild one PDF
python -m menu_cards verify ../hardware/common/menu-cards/*.json         # re-verify records
```

* JSON records go to `hardware/common/menu-cards/` (committed). PDFs, `.tex` and LaTeX logs go to
  `%LOCALAPPDATA%/christopher/menu-cards` (or `$XDG_DATA_HOME/christopher/menu-cards`), outside the
  repository; change it with `--pdf-dir`, skip it with `--no-pdf`.
* The search is deterministic: the result is the lowest-numbered successful attempt for a seed
  (`seed` and `attempts` are stored in the record), whatever `--workers` is (default: CPU count - 2).
  `python -m pytest tests/test_menu_cards.py` replays each record's attempt.

## Modes

* `slice`: the right (slowest) drum core is fixed at the true value (on both physical machines the left drum is the fast one); the left and middle drums run 26 x 26 =
  676 positions. False stops are counted inside this slice.
* `full`: all 17,576 positions of the wheel order printed on the card.

Both records also store the false-stop count of a full run of the true wheel order and of a full run
over all 60 wheel orders (the visitor does not do the latter on the exhibit machines).

## Constraints the generator enforces

* Exactly `edges` / `letters` / `loops` (simple graph, connected, loops = edges - letters + 1).
* All edges lie inside the crib (12 or 16 letters here) and the left and middle rotor do not move
  during the whole crib (no middle-rotor turnover), so the physical machine needs no hoppity.
* Enigma I, rotors I-V, reflector B, 10 plugboard pairs, rings and start random.
* The true stop is found, false stops lie in the requested range, no false stop survives the
  checking machine, and the checking machine recovers the exact plaintext at the true stop.

## Record fields (short)

`ciphertext`, `crib`, `offset` (0-based; `crib_letters_1based`), `menu.edges[]` (unit, plain, cipher,
`link` = crib position + 1 = the unit's drum offset, `drum_offset_core` = [0, 0, k] relative to the
stop, `drum_offset_letters`, `drums_at_true_stop`, `enigma_window`), `menu.test_letter`,
`menu.input_wire`, `wheel_order`, `true_stop` (`drums_core_letters` and `core_offsets` as the
software bombe reports them, `enigma_window_at_crib_start` = core + ring), `run.expected_stops[]`,
the false-stop counts, `key`, `recovered` (what the checking machine returned), `plaintext`.

Conventions: the bombe position reported by the software is the *core* position of the rotors at the
first crib letter; the unit for crib position k has the right drum k steps ahead and the left and
middle drums equal. The historical indicator-drum scale (reversed letters, per-drum offsets,
`docs/reference/bombe-mechanics.md` section 4) is not applied; the Enigma window letters are given
separately.

## Card

Front: ciphertext in groups of five, the crib strip (link numbers, crib letters, cipher letters; used
links coloured), the menu as a letter graph with numbered links, wheel order and run range. Back:
the plugging table (unit, link, IN/OUT letters, drum offset), test register instructions, a blank stop
log, and the answer (stops, key, plaintext) printed upside down at the bottom.

## Files

`engine.py` (search and evaluation), `record.py` (JSON and `verify_record`), `card.py` (LaTeX/TikZ),
`texts.py` (plaintexts and cribs), `scenarios.py` (default set and seeds), `__main__.py` (CLI).
