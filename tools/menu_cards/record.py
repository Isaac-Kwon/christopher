"""Scenario record (JSON) and its independent re-verification."""

from __future__ import annotations

from christopher import Bombe, Enigma, Menu, WheelOrder

from . import engine
from .engine import A, L, N, Candidate

SCHEMA = 1


def groups5(text: str) -> str:
    return " ".join(text[i:i + 5] for i in range(0, len(text), 5))


def _letters(core) -> str:
    return "".join(L(x % 26) for x in core)


def make_record(c: Candidate, ev: dict, seed: int) -> dict:
    spec = c.spec
    menu = c.menu
    key = c.key
    true = ev["true"]
    core = [N(ch) for ch in true]
    rings = key["rings"]
    win0 = c.states[0]
    # Window letters = core letters + ring letters (a check of the whole offset logic).
    assert win0 == _letters([core[i] + N(rings[i]) for i in range(3)])
    edges = []
    for unit, e in enumerate(menu.edges, 1):
        k = e.pos
        drums = _letters([core[0], core[1], core[2] + k])
        edges.append(dict(
            unit=unit, plain=e.plain, cipher=e.cipher, link=k + 1, crib_index=k,
            drum_offset_core=[0, 0, k],                 # relative to the true stop position
            drum_offset_letters="AA" + L(k % 26),
            drums_at_true_stop=drums,                   # core letters of this unit's drums at the true stop
            enigma_window=c.states[k],                  # window letters of the real Enigma for this letter
        ))
    stops = []
    for s, is_true, sols, exact, crib_ok in ev["checked"]:
        stops.append(dict(
            drums=s.position, is_true=is_true,
            test_register_live=s.live, live_count=s.test_register.bit_count(),
            stecker_candidates=s.stecker_candidates,
            checking_solutions=len(sols),
        ))
    best = next(sol for s, t, sols, ex, cr in ev["checked"] if t for sol in ex)
    plug = {}
    for pair in key["plugboard"].split():
        plug[pair[0]] = pair[1]
        plug[pair[1]] = pair[0]
    lo = c.offset
    return dict(
        schema=SCHEMA,
        id=spec.name,
        mode=spec.mode,
        seed=seed,
        attempts=c.tries,
        search_seconds=round(c.seconds, 1),
        ciphertext=c.ciphertext,
        ciphertext_groups=groups5(c.ciphertext),
        plaintext=c.plaintext,
        plaintext_message_id=c.msg_id,
        crib=c.crib,
        offset=lo,                                     # 0-based index of the first crib letter
        crib_letters_1based=[lo + 1, lo + len(c.crib)],
        crib_cipher=c.ciphertext[lo:lo + len(c.crib)],
        menu=dict(
            edges=edges,
            n_edges=len(menu),
            letters=menu.letters,
            n_letters=len(menu.letters),
            loops=menu.loops,
            span=menu.span,
            test_letter=menu.test_letter,
            input_wire=menu.input_wire,
            link_numbers=[e["link"] for e in edges],
        ),
        wheel_order=dict(
            reflector=key["reflector"], rotors_left_to_right=list(key["rotors"]),
            text=str(WheelOrder(key["reflector"], tuple(key["rotors"]))),
        ),
        true_stop=dict(
            drums_core_letters=true,
            core_offsets=core,
            enigma_window_at_crib_start=win0,
            fixed_right_drum=true[2] if spec.mode == "slice" else None,
            test_register_live=next(s["test_register_live"] for s in stops if s["is_true"]),
            test_letter_stecker=plug.get(menu.test_letter, menu.test_letter),
        ),
        run=dict(
            positions=ev["positions"],
            range=("right (slow) drum fixed, left and middle sweep 26x26" if spec.mode == "slice" else "all 17,576 positions"),
            expected_stops=stops,
            false_stops=ev["false_in_mode"],
            false_stops_true_order_full_run=ev["false_full_order"],
            false_stops_all_120_orders_full_run=ev["false_all_orders"],
        ),
        key=dict(
            model=engine.MODEL, rotors=list(key["rotors"]), reflector=key["reflector"],
            rings=key["rings"], start=key["positions"], plugboard=key["plugboard"],
        ),
        recovered=dict(
            rings=best.rings, start=best.positions, plugboard=best.plugboard,
            unresolved=best.unresolved, guessed=best.guessed, plaintext_equals=True,
        ),
        crib_has_middle_rotor_turnover=False,
    )


def verify_record(rec: dict) -> list[str]:
    """Rebuild everything from the record with the software core; returns a list of problems."""
    bad: list[str] = []
    k = rec["key"]
    enc = Enigma(k["model"], rotors=k["rotors"], reflector=k["reflector"], rings=k["rings"],
                 positions=k["start"], plugboard=k["plugboard"])
    ct = enc.encrypt(rec["plaintext"])
    if ct != rec["ciphertext"]:
        bad.append("ciphertext does not match the key and plaintext")
    off, crib = rec["offset"], rec["crib"]
    if rec["plaintext"][off:off + len(crib)] != crib:
        bad.append("crib is not in the plaintext at the offset")
    if any(ch == c for ch, c in zip(crib, ct[off:])):
        bad.append("crib letter equals the cipher letter above it")
    # No middle-rotor turnover over the crib.
    e = Enigma(k["model"], rotors=k["rotors"], reflector=k["reflector"], rings=k["rings"], positions=k["start"])
    seen = set()
    for i, ch in enumerate(rec["plaintext"][:off + len(crib)]):
        e.encrypt(ch)
        if i >= off:
            seen.add(e.positions[:2])
    if len(seen) != 1:
        bad.append("left/middle rotor moves inside the crib")
    full = Menu.from_crib(ct, crib, off)
    sub = engine.submenu(full, ct, [e_["crib_index"] for e_ in rec["menu"]["edges"]], rec["menu"]["n_edges"])
    m = rec["menu"]
    if (len(sub), len(sub.letters), sub.loops) != (m["n_edges"], m["n_letters"], m["loops"]):
        bad.append(f"menu structure differs: {len(sub)}/{len(sub.letters)}/{sub.loops}")
    if sub.letters != m["letters"] or sub.test_letter != m["test_letter"] or sub.input_wire != m["input_wire"]:
        bad.append("menu letters / test letter / input wire differ")
    for e_, ed in zip(sub.edges, m["edges"]):
        if (e_.plain, e_.cipher, e_.pos + 1) != (ed["plain"], ed["cipher"], ed["link"]):
            bad.append(f"edge differs: {e_}")
    order = WheelOrder(rec["wheel_order"]["reflector"], tuple(rec["wheel_order"]["rotors_left_to_right"]))
    bombe = Bombe(k["model"]).plug_up(sub)
    ts = rec["true_stop"]
    stops = bombe.run(order, fixed_right=ts["fixed_right_drum"] if rec["mode"] == "slice" else None)
    got = sorted(s.position for s in stops)
    want = sorted(s["drums"] for s in rec["run"]["expected_stops"])
    if got != want:
        bad.append(f"stops differ: {got} vs {want}")
    if ts["drums_core_letters"] not in got:
        bad.append("true stop not among the stops")
    if len(got) - 1 != rec["run"]["false_stops"]:
        bad.append("false stop count differs")
    # True stop = core position of the rotors at the first crib letter: window - ring.
    win = ts["enigma_window_at_crib_start"]
    core = "".join(L((N(win[i]) - N(k["rings"][i])) % 26) for i in range(3))
    if core != ts["drums_core_letters"]:
        bad.append("window letters and ring do not give the true stop")
    # Checking machine.
    good = False
    for s in stops:
        sols = s.check()
        if s.position == ts["drums_core_letters"]:
            good = any(sol.plaintext == rec["plaintext"] for sol in sols)
        elif sols:
            bad.append(f"false stop {s.position} survives the checking machine")
    if not good:
        bad.append("checking machine does not recover the plaintext at the true stop")
    return bad
