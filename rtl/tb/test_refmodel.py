"""Sanity checks of the Python reference scrambler itself (pure Python, no simulator)."""

import random

from refmodel import ROTOR_ID, REFLECTOR_ID, letters, parse_order, scrambler


def enigma_i(order: str, window="AAA", ring="AAA"):
    """Plugboard-free Enigma I, no notch effects within a short message: yields a cipher function."""
    o = parse_order(order)
    core = [(ord(w) - ord(r)) % 26 for w, r in zip(window, ring)]

    def encipher(text):
        l, m, r = core
        out = []
        for ch in text:
            r = (r + 1) % 26  # right rotor steps before every key press
            perm = scrambler(o["refl_id"], None, o["left_id"], o["middle_id"], o["right_id"], 0, l, m, r)
            out.append(perm[ord(ch) - 65])
        return letters(out)

    return encipher


def test_aaaaa_gives_bdzgo():
    assert enigma_i("B I-II-III")("AAAAA") == "BDZGO"


def test_ids_follow_file_order():
    assert ROTOR_ID["I"] == 0 and ROTOR_ID["VIII"] == 7 and ROTOR_ID["BETA"] == 8 and ROTOR_ID["GAMMA"] == 9
    assert [REFLECTOR_ID[n] for n in ("B", "C", "B-THIN", "C-THIN")] == [0, 1, 2, 3]


def test_scrambler_is_fixed_point_free_involution():
    rng = random.Random(7)
    for _ in range(200):
        a, b, c = rng.sample(range(8), 3)
        p = scrambler(rng.randrange(4), rng.choice([None, 8, 9]), a, b, c, *(rng.randrange(26) for _ in range(4)))
        assert all(p[p[x]] == x and p[x] != x for x in range(26)), letters(p)


def test_thin_reflector_with_greek_at_a_equals_standard_reflector():
    """B-THIN + BETA at core 0 is the standard reflector B (documented M4 compatibility)."""
    rng = random.Random(3)
    for _ in range(50):
        a, b, c = rng.sample(range(8), 3)
        cores = [rng.randrange(26) for _ in range(3)]
        m4 = scrambler(2, 8, a, b, c, 0, *cores)
        m3 = scrambler(0, None, a, b, c, 0, *cores)
        assert m4 == m3, f"{letters(m4)} != {letters(m3)}"
