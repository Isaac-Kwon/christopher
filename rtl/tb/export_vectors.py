"""Export RTL reference vectors from the Python package (Rust core) into data/vectors/rtl/.

Run on the Windows side (the venv with the built `christopher` package):

    "$LOCALAPPDATA/christopher/venv/Scripts/python.exe" rtl/tb/export_vectors.py

Core vectors (JSONL, one object per line) extend the format of christopher.vectors.export_vectors
with the options the RTL needs:
    wheel_order, position [g,l,m,r] core offsets, edges [[plain,cipher,pos]..], test_letter,
    input_wire, diagonal (bool), turnover_at (int|null), live [26 ints], stop (bool)
Sweep references (JSON): the same menu/options plus the complete ordered stop list from Bombe.run
    {"name", "wheel_order", "edges", "test_letter", "input_wire", "diagonal", "turnover_at",
     "positions_tested", "stops": [{"pos": [g,l,m,r], "register": mask}, ..]}
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from christopher._text import A
from christopher.bombe import Bombe, WheelOrder
from christopher.menu import Menu

FULL = (1 << 26) - 1
OUT = Path(__file__).resolve().parents[2] / "data" / "vectors" / "rtl"

# Menu of the existing enigma-i_B-III-V-II.jsonl vector file (plain, cipher, crib position).
EDGES = [[22, 8, 0], [4, 11, 1], [19, 11, 3], [4, 8, 4], [17, 8, 5], [17, 12, 8], [4, 0, 10],
         [0, 22, 13], [4, 20, 15], [5, 12, 16], [4, 16, 18], [17, 8, 19], [8, 19, 21], [4, 16, 22]]
TEST, WIRE = 4, 0


def menu_of(edges, test=TEST, wire=WIRE) -> Menu:
    return Menu.from_edges([(chr(A + p), chr(A + c), k) for p, c, k in edges], chr(A + test), chr(A + wire))


def stops_of(bombe, order, turnover_at):
    stops = bombe.run(order, turnover_at=turnover_at, parallel=False)
    rows = [(tuple(s._pos), s.test_register) for s in stops]
    assert rows == sorted(rows), "Bombe.run stops are not in sweep order"
    return rows


def core_vectors(name, order_s, edges, diagonal=True, turnover_at=None, sample=40, max_stops=40, seed=1):
    """Stops (capped, evenly thinned) plus `sample` random non-stop positions."""
    order = WheelOrder.parse(order_s)
    bombe = Bombe(diagonal_board=diagonal).plug_up(menu_of(edges))
    stops = [p for p, _ in stops_of(bombe, order, turnover_at)]
    if len(stops) > max_stops:
        stops = stops[:: len(stops) // max_stops][:max_stops]
    rng = random.Random(seed)
    taken, wanted = set(stops), list(stops)
    span = 26 if order.greek else 1
    while len(wanted) < len(stops) + sample:
        p = (rng.randrange(span), rng.randrange(26), rng.randrange(26), rng.randrange(26))
        if p not in taken:
            taken.add(p)
            wanted.append(p)
    with open(OUT / f"{name}.jsonl", "w", encoding="ascii", newline="\n") as f:
        for p in wanted:
            live = bombe.trace(order, p, turnover_at=turnover_at)
            row = {"wheel_order": str(order), "position": list(p), "edges": edges, "test_letter": TEST,
                   "input_wire": WIRE, "diagonal": diagonal, "turnover_at": turnover_at,
                   "live": live, "stop": live[TEST] != FULL}
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"{name}.jsonl: {len(wanted)} vectors ({len(stops)} stops)")


def sweep_ref(name, order_s, edges, diagonal=True, turnover_at=None):
    order = WheelOrder.parse(order_s)
    bombe = Bombe(diagonal_board=diagonal).plug_up(menu_of(edges))
    rows = stops_of(bombe, order, turnover_at)
    ref = {"name": name, "wheel_order": str(order), "edges": edges, "test_letter": TEST, "input_wire": WIRE,
           "diagonal": diagonal, "turnover_at": turnover_at, "positions_tested": bombe.positions_tested,
           "stops": [{"pos": list(p), "register": r} for p, r in rows]}
    (OUT / f"sweep_{name}.json").write_text(json.dumps(ref, separators=(",", ":")) + "\n", encoding="ascii", newline="\n")
    print(f"sweep_{name}.json: {len(rows)} stops, {bombe.positions_tested} positions")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    # Menu prefixes tuned so each case has a useful number of stops (stop counts for B I-II-III:
    # 11 edges 85, 8 edges 1378, 13 edges without diagonal board 33).
    e8, e11, e13 = EDGES[:8], EDGES[:11], EDGES[:13]
    core_vectors("enigma-i_turnover5_B-I-II-III", "B I-II-III", e11, turnover_at=5)
    core_vectors("enigma-i_nodiag_B-II-IV-V", "B II-IV-V", e13, diagonal=False)
    core_vectors("enigma-i_weak_C-III-V-II", "C III-V-II", e8)
    core_vectors("m4_B-THIN_BETA_II-IV-I", "B-THIN BETA II-IV-I", e11, sample=60, max_stops=40)
    core_vectors("m4_C-THIN_GAMMA_VI-VIII-VII_turnover9", "C-THIN GAMMA VI-VIII-VII", e11, turnover_at=9,
                 sample=40, max_stops=40)
    sweep_ref("enigma-i_B-III-V-II_14edges", "B III-V-II", EDGES)
    sweep_ref("enigma-i_B-III-V-II_11edges", "B III-V-II", e11)
    sweep_ref("enigma-i_C-V-IV-I_13edges_nodiag_turnover5", "C V-IV-I", e13, diagonal=False, turnover_at=5)
    sweep_ref("m4_B-THIN_BETA_II-IV-I_11edges", "B-THIN BETA II-IV-I", e11)  # tb compares a prefix
