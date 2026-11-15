"""Tests for the exhibit menu-card designer (`tools/menu_cards`)."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from christopher import Enigma, Menu  # noqa: E402
from menu_cards import card, engine, record, texts  # noqa: E402
from menu_cards.scenarios import DEFAULT_SET  # noqa: E402

JSON_DIR = ROOT / "hardware" / "common" / "menu-cards"
RECORDS = sorted(p for p in JSON_DIR.glob("*.json") if p.name != "index.json")


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_texts_are_enigma_style():
    for msg_id, raw, cribs in texts.MESSAGES:
        plain = texts.clean(raw)
        assert plain.isalpha() and plain.isupper() and "X" in plain
        assert not any(ch in raw for ch in "ÄÖÜß")
        for crib in cribs:
            assert crib in plain, (msg_id, crib)


def test_spec_loops():
    assert engine.Spec("a", "slice", 6, 4).loops == 3
    assert engine.Spec("b", "full", 12, 10).loops == 3


def test_default_set_covers_the_requested_shapes():
    shapes = {(s.mode, s.edges, s.letters, s.loops) for s, _ in DEFAULT_SET}
    assert shapes == {("slice", 6, 4, 3), ("full", 12, 10, 3)}
    assert sum(s.mode == "slice" for s, _ in DEFAULT_SET) >= 4
    assert sum(s.mode == "full" for s, _ in DEFAULT_SET) >= 2


def test_committed_records_exist():
    assert len(RECORDS) >= 6


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.stem)
def test_record_structure_and_verification(path):
    rec = load(path)
    m = rec["menu"]
    # Structure of the menu exactly as requested, edges inside a short crib span, no turnover.
    assert m["n_edges"] == len(m["edges"])
    assert m["n_letters"] == len(m["letters"])
    assert m["loops"] == m["n_edges"] - m["n_letters"] + 1
    assert m["span"] <= len(rec["crib"])
    assert rec["crib_has_middle_rotor_turnover"] is False
    assert len({tuple(sorted((e["plain"], e["cipher"]))) for e in m["edges"]}) == m["n_edges"]  # simple graph
    # The true stop is among the expected stops and false stops are few.
    stops = rec["run"]["expected_stops"]
    assert sum(s["is_true"] for s in stops) == 1
    assert rec["run"]["false_stops"] == len(stops) - 1 <= 2
    assert rec["true_stop"]["drums_core_letters"] in [s["drums"] for s in stops]
    # Re-verification with the software bombe and checking machine.
    assert record.verify_record(rec) == []


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.stem)
def test_search_is_deterministic(path):
    """The seed and attempt number in the record reproduce the same scenario."""
    rec = load(path)
    spec = next(s for s, seed in DEFAULT_SET if s.name == rec["id"])
    r = engine.attempt(spec, rec["seed"], rec["attempts"] - 1)
    assert r is not None
    c, ev = r
    assert c.ciphertext == rec["ciphertext"]
    assert ev["true"] == rec["true_stop"]["drums_core_letters"]
    assert ev["false_in_mode"] == rec["run"]["false_stops"]


def test_tampered_record_is_rejected():
    rec = load(RECORDS[0])
    bad = copy.deepcopy(rec)
    bad["key"]["plugboard"] = ""
    assert record.verify_record(bad)
    bad = copy.deepcopy(rec)
    bad["menu"]["loops"] += 1
    assert record.verify_record(bad)


def test_window_letters_are_core_plus_ring():
    rec = load(RECORDS[0])
    k = rec["key"]
    core = rec["true_stop"]["core_offsets"]
    win = rec["true_stop"]["enigma_window_at_crib_start"]
    assert win == "".join(chr(65 + (core[i] + ord(k["rings"][i]) - 65) % 26) for i in range(3))
    # Per-unit drum letters: right drum advances by the crib index.
    for e in rec["menu"]["edges"]:
        assert e["drums_at_true_stop"][2] == chr(65 + (core[2] + e["crib_index"]) % 26)
        assert e["drum_offset_core"] == [0, 0, e["crib_index"]]


def test_solver_makes_the_requested_graph():
    """The plugboard search on its own: all cipher letters stay inside the wanted letter set."""
    import random

    spec = engine.Spec("t", "slice", 6, 4)
    rng = random.Random(5)
    for _ in range(4000):
        c = engine.build_candidate(spec, rng)
        if c:
            assert (len(c.menu), len(c.menu.letters), c.menu.loops) == (6, 4, 3)
            full = Menu.from_crib(c.ciphertext, c.crib, c.offset)
            assert len(full) == len(c.crib)
            assert Enigma("enigma-i", **c.key).encrypt(c.plaintext) == c.ciphertext
            return
    pytest.skip("no candidate in 4000 tries")


def test_card_source_has_front_back_and_answer():
    rec = load(RECORDS[0])
    tex = card.tex_source(rec)
    assert tex.count("\\newpage") == 1 and "\\begin{tikzpicture}" in tex
    for e in rec["menu"]["edges"]:
        assert e["drum_offset_letters"] in tex
    assert rec["plaintext"][:5] in tex.replace(" ", "")


def test_no_personal_paths_in_sources():
    bad = ("C:" + "/Users/", "C:" + "\\Users\\", "/mnt" + "/c/", "/ho" + "me/", "Drop" + "box")
    files = list((ROOT / "tools" / "menu_cards").glob("*")) + list(JSON_DIR.glob("*.json"))
    for p in files:
        if p.suffix not in {".py", ".md", ".json"}:
            continue
        text = p.read_text(encoding="utf-8")
        for b in bad:
            assert b not in text, (p, b)
