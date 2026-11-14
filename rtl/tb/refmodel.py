"""Pure-Python reference for the RTL testbenches (no cocotb, no simulator needed).

Wiring tables come from data/wirings/enigma.txt, the same file the RTL generator reads.  The
scrambler follows christopher_core::bombe::WheelOrder::scrambler path by path.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIRINGS = ROOT / "data" / "wirings" / "enigma.txt"
VECTORS = ROOT / "data" / "vectors" / "rtl"
FULL = (1 << 26) - 1


def letters(values) -> str:
    """[0, 1, 25] -> 'ABZ'."""
    return "".join(chr(65 + v) for v in values)


def mask_letters(mask: int) -> str:
    """Bitmask of wires -> letters of the live wires ('-' when none)."""
    return letters(w for w in range(26) if mask >> w & 1) or "-"


def _load():
    rotors, reflectors = [], []  # (name, forward table, backward table), in file order
    for line in WIRINGS.read_text().splitlines():
        line = line.split("#")[0].split()
        if not line:
            continue
        kind, name, wiring = line[0], line[1], line[2]
        fwd = [ord(c) - 65 for c in wiring]
        bwd = [0] * 26
        for i, o in enumerate(fwd):
            bwd[o] = i
        if kind in ("rotor", "greek"):
            rotors.append((name, fwd, bwd))
        else:
            reflectors.append((name, fwd, bwd))
    return rotors, reflectors


_ROTORS, _REFLECTORS = _load()
ROTOR_ID = {n: i for i, (n, _, _) in enumerate(_ROTORS)}  # I..VIII = 0..7, BETA = 8, GAMMA = 9
REFLECTOR_ID = {n: i for i, (n, _, _) in enumerate(_REFLECTORS)}  # B, C, B-THIN, C-THIN


def parse_order(text: str) -> dict:
    """'B II-IV-V' or 'B-THIN BETA II-IV-I' -> ids as the RTL takes them."""
    head, *rest = text.split(None, 1)
    names = rest[0].replace("-", " ").split() if rest else []
    if names and names[0] in ("BETA", "GAMMA"):
        greek, names = ROTOR_ID[names[0]], names[1:]
    else:
        greek = None
    left, middle, right = (ROTOR_ID[n] for n in names)
    return {"refl_id": REFLECTOR_ID[head], "greek_id": greek, "left_id": left, "middle_id": middle,
            "right_id": right}


def _through(table, core, c):
    return (table[(c + core) % 26] - core) % 26


def scrambler(refl_id, greek_id, left_id, middle_id, right_id, core_g, core_l, core_m, core_r) -> list[int]:
    """Scrambler permutation (no plugboard): perm[c] for input letter c. greek_id None = three rotors."""
    perm = []
    for c in range(26):
        x = _through(_ROTORS[right_id][1], core_r, c)
        x = _through(_ROTORS[middle_id][1], core_m, x)
        x = _through(_ROTORS[left_id][1], core_l, x)
        if greek_id is not None:
            x = _through(_ROTORS[greek_id][1], core_g, x)
        x = _REFLECTORS[refl_id][1][x]
        if greek_id is not None:
            x = _through(_ROTORS[greek_id][2], core_g, x)
        x = _through(_ROTORS[left_id][2], core_l, x)
        x = _through(_ROTORS[middle_id][2], core_m, x)
        perm.append(_through(_ROTORS[right_id][2], core_r, x))
    return perm


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(l) for l in (VECTORS / name).read_text().splitlines() if l.strip()]


def load_json(name: str) -> dict:
    return json.loads((VECTORS / name).read_text())


def config_of(v: dict) -> dict:
    """Vector / sweep-reference dict -> everything the RTL needs except drum position and early_exit."""
    order = parse_order(v["wheel_order"])
    return {**order, "edges": v["edges"], "test_letter": v["test_letter"], "input_wire": v["input_wire"],
            "diagonal": v.get("diagonal", True), "turnover_at": v.get("turnover_at")}
