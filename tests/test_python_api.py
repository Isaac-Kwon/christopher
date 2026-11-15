"""Tests for the Python interface (`python/christopher`)."""

import json
from pathlib import Path

import pytest

from christopher import Bombe, Enigma, Menu, export_vectors, possible_offsets, search

ROOT = Path(__file__).resolve().parents[1]

PLAIN = (
    "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTXSTAERKEDREI"
)
CRIB = "WETTERVORHERSAGE"
OFFSET = PLAIN.index(CRIB)
KEY = dict(
    rotors=("IV", "I", "V"), reflector="B", rings="AKH", positions="QWE",
    plugboard="AQ BZ CR DM EV FW GK HN IU JP",
)


def test_known_answer():
    e = Enigma("enigma-i", rotors=("I", "II", "III"), reflector="B", rings="AAA", positions="AAA")
    assert e.encrypt("AAAAA") == "BDZGO"
    assert e.positions == "AAF"


def test_round_trip_drops_non_letters_and_resets():
    e = Enigma("enigma-i", rotors=("II", "IV", "V"), reflector="B", rings="BUL", positions="BLA", plugboard="AV BS CG")
    ct = e.encrypt("Wetter, vorhersage!")
    assert ct.isalpha() and len(ct) == 16
    assert e.positions != "BLA"
    assert e.reset().encrypt(ct) == "WETTERVORHERSAGE"


def test_number_style_rings_and_positions():
    letters = Enigma("enigma-i", rotors=("II", "IV", "V"), rings="BUL", positions="BLA")
    for rings in ("02 21 12", [2, 21, 12], "B U L"):
        e = Enigma("enigma-i", rotors=("II", "IV", "V"), rings=rings, positions=[2, 12, 1])
        assert e.rings == "BUL" and e.start == "BLA"
        assert e.encrypt("HELLOWORLD") == Enigma(
            "enigma-i", rotors=("II", "IV", "V"), rings="BUL", positions="BLA"
        ).encrypt("HELLOWORLD")
    assert letters.start == "BLA"


def test_bad_settings_raise_value_error():
    with pytest.raises(ValueError):
        Enigma("enigma-i", rotors=("I", "I", "II"))          # rotor used twice
    with pytest.raises(ValueError):
        Enigma("enigma-i", rotors=("I", "II", "VI"))         # VI is not on the Enigma I
    with pytest.raises(ValueError):
        Enigma("enigma-i", rotors=("I", "II", "III"), rings="27 1 1")
    with pytest.raises(ValueError):
        Enigma("enigma-i", rotors=("I", "II", "III"), plugboard="AB AC")


def test_m4_construction():
    e = Enigma("m4", greek="BETA", rotors=("II", "IV", "I"), reflector="B-THIN", rings="AAAA", positions="AAAA")
    assert e.positions == "AAAA"
    ct = e.encrypt("ATTACKATDAWN")
    assert e.reset().encrypt(ct) == "ATTACKATDAWN"
    with pytest.raises(ValueError):
        Enigma("m4", rotors=("II", "IV", "I"), reflector="B-THIN", rings="AAAA", positions="AAAA")  # no greek


def test_menu_from_crib():
    ct = Enigma("enigma-i", **KEY).encrypt(PLAIN)
    assert OFFSET in possible_offsets(ct, CRIB)
    menu = Menu.from_crib(ct, CRIB, OFFSET)
    assert len(menu) == len(CRIB)
    sub = menu.select(max_edges=12)
    assert len(sub) <= 12 and sub.loops >= 0
    assert sub.test_letter in sub.letters
    text = str(sub)
    assert "test letter" in text and "loops" in text
    with pytest.raises(ValueError):
        Menu.from_crib(ct, CRIB, len(ct))  # runs past the end


def test_search_finds_true_key():
    ct = Enigma("enigma-i", **KEY).encrypt(PLAIN)
    orders = [("B", "IV", "I", "V"), ("B", "I", "IV", "V"), ("B", "II", "III", "V"), "C IV-I-V", "B V-I-IV"]
    report = search(ct, CRIB, OFFSET, model="enigma-i", max_edges=12, wheel_orders=orders)
    assert report.orders_tried == len(orders)
    assert report.positions_tested == len(orders) * 26**3
    assert len(report.stops) >= 1
    best = report.solutions[0]
    assert best.wheel_order.rotors == ("IV", "I", "V")
    assert best.plaintext[OFFSET:OFFSET + len(CRIB)] == CRIB
    # The recovered key deciphers the whole message (letters whose stecker is unresolved may differ).
    assert best.enigma().encrypt(ct)[OFFSET:OFFSET + len(CRIB)] == CRIB


def test_operator_workflow_matches_search():
    ct = Enigma("enigma-i", **KEY).encrypt(PLAIN)
    menu = Menu.from_crib(ct, CRIB, OFFSET).select(12)
    bombe = Bombe(model="enigma-i", diagonal_board=True).plug_up(menu)
    stops = bombe.run(wheel_orders="B IV-I-V")
    assert bombe.positions_tested == 26**3
    assert stops and all(s.wheel_order.rotors == ("IV", "I", "V") for s in stops)
    assert "STOP" in str(stops[0]) and "B IV-I-V" in str(stops[0])
    sols = [sol for s in stops for sol in s.check()]
    assert any(sol.plaintext[OFFSET:OFFSET + len(CRIB)] == CRIB for sol in sols)


def test_trace_returns_26_masks():
    ct = Enigma("enigma-i", **KEY).encrypt(PLAIN)
    menu = Menu.from_crib(ct, CRIB, OFFSET).select(12)
    bombe = Bombe().plug_up(menu)
    stop = bombe.run("B IV-I-V")[0]
    live = stop.trace()
    assert len(live) == 26 and all(0 <= m < 1 << 26 for m in live)
    assert live[ord(menu.test_letter) - 65] == stop.test_register
    grid = bombe.trace("B IV-I-V", stop.position, matrix=True)
    assert len(grid) == 26 and all(len(r) == 26 for r in grid)
    assert [sum(b << w for w, b in enumerate(r)) for r in grid] == live


def test_export_vectors(tmp_path):
    ct = Enigma("enigma-i", **KEY).encrypt(PLAIN)
    menu = Menu.from_crib(ct, CRIB, OFFSET).select(12)
    out = tmp_path / "v.jsonl"
    n = export_vectors(menu, "B IV-I-V", out, sample=5)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == n
    assert sum(r["stop"] for r in rows) == n - 5
    assert all(len(r["live"]) == 26 and len(r["position"]) == 4 for r in rows)


def test_historical_m4_message():
    data = json.loads((ROOT / "data" / "vectors" / "historical.json").read_text())
    entry = next(d for d in data if d["id"] == "m4-triton-shark-1945-03-06")
    e = Enigma(
        "m4", greek=entry["greek"], rotors=entry["rotors"], reflector=entry["reflector"],
        rings=entry["rings"], positions=entry["start"], plugboard=entry["plugboard"],
    )
    # "ciphertext" is the message body (the indicator groups are already stripped).
    assert e.encrypt(entry["ciphertext"]) == entry["plaintext"]
    assert e.reset().encrypt(entry["plaintext"]) == entry["ciphertext"]


# --- sub-menus, test letter, slice runs -------------------------------------------------------

def _slice_setup():
    from christopher import Bombe, Enigma, Menu

    key = dict(rotors=("III", "V", "II"), reflector="B", rings="GMC", positions="KDC",
               plugboard="AQ BZ CR DM EV FW GK HN IU JP")
    pt = "ANXOBERKOMMANDODERWEHRMACHTXWETTERVORHERSAGEFUERDIENORDSEEXHEUTEKLARXWINDAUSWESTX"
    ct = Enigma("enigma-i", **key).encrypt(pt)
    menu = Menu.from_crib(ct, "WETTERVORHERSAGEFUERDIE", 28).select(12)
    return Bombe, Enigma, menu, ct, key


def test_subset_keeps_crib_and_can_be_checked():
    Bombe, _, menu, _, _ = _slice_setup()
    keep = [e.pos for e in menu.edges][:9]
    sub = menu.subset(keep)
    assert [e.pos for e in sub.edges] == sorted(keep)
    assert (sub.offset, sub.crib) == (menu.offset, menu.crib)
    stops = Bombe("enigma-i").plug_up(sub).run("B III-V-II")
    sols = [s for st in stops for s in st.check()]
    assert any(s.plaintext[28:51] == "WETTERVORHERSAGEFUERDIE" for s in sols)
    with pytest.raises(ValueError):
        menu.subset([999])


def test_with_test_letter():
    _, _, menu, _, _ = _slice_setup()
    other = next(ch for ch in menu.letters if ch != menu.test_letter)
    m2 = menu.with_test(other, "B")
    assert (m2.test_letter, m2.input_wire) == (other, "B")
    assert m2.edges == menu.edges
    with pytest.raises(ValueError):
        menu.with_test("Q" if "Q" not in menu.letters else "J")


def test_slice_run_fixed_right_drum():
    Bombe, _, menu, _, _ = _slice_setup()
    bombe = Bombe("enigma-i").plug_up(menu)
    full = bombe.run("B III-V-II")
    true = [s for s in full if s.check()]
    assert true, "true stop expected in the full run"
    right = true[0].position[-1]
    sliced = bombe.run("B III-V-II", fixed_right=right)
    assert bombe.positions_tested == 676
    assert [s.position for s in sliced] == [s.position for s in full if s.position[-1] == right]
    with pytest.raises(ValueError):
        bombe.run("B III-V-II", fixed_right=26)
