"""RTL test vectors (milestone 1.7): the bombe's 676-bit register state at chosen drum positions."""

from __future__ import annotations

import json
import random
from pathlib import Path

from christopher._text import A
from christopher.bombe import Bombe, WheelOrder, _drums
from christopher.menu import Menu

FULL = (1 << 26) - 1


def export_vectors(
    menu: Menu,
    wheel_order,
    path: str | Path,
    positions=None,
    sample: int = 64,
    seed: int = 0,
    diagonal_board: bool = True,
) -> int:
    """Write one JSON object per line to ``path``; return the number of lines.

    Each line: ``{"wheel_order": "B II-IV-V", "position": [g, l, m, r], "edges": [[plain, cipher, pos], ...],
    "test_letter": t, "input_wire": w, "live": [26 ints], "stop": bool}``.  Letters are 0..25
    (A = 0); ``position`` is the drum core offsets (greek is 0 without a greek wheel); ``live[a]`` is
    the bitmask of live wires on cable ``a`` after the current has settled, so ``live`` is the
    expected 26 x 26 = 676-bit register; ``stop`` is true when the test cable is not fully live.

    ``positions`` is a list of drum-letter strings ("BLA"); the default is every stop of this wheel
    order plus ``sample`` random non-stop positions (reproducible through ``seed``).
    """
    order = WheelOrder.parse(wheel_order)
    bombe = Bombe(diagonal_board=diagonal_board).plug_up(menu)
    wanted = [_drums(order, p) for p in positions] if positions is not None else None
    if wanted is None:
        stops = bombe.run(order)
        wanted = [s._pos for s in stops]
        taken, rng = set(wanted), random.Random(seed)
        span = 26 if order.greek else 1
        while len(wanted) < len(stops) + sample:
            p = (rng.randrange(span), rng.randrange(26), rng.randrange(26), rng.randrange(26))
            if p not in taken:
                taken.add(p)
                wanted.append(p)
    edges = [[ord(e.plain) - A, ord(e.cipher) - A, e.pos] for e in menu.edges]
    test, wire = ord(menu.test_letter) - A, ord(menu.input_wire) - A
    with open(path, "w", encoding="ascii") as f:
        for p in wanted:
            live = bombe.trace(order, p)
            row = {
                "wheel_order": str(order), "position": list(p), "edges": edges,
                "test_letter": test, "input_wire": wire, "live": live, "stop": live[test] != FULL,
            }
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return len(wanted)
