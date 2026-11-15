"""Engineering a scenario: key + plaintext such that a crib gives a menu of the wanted shape.

Natural cribs almost never give a dense menu (4 letters / 6 edges / 3 loops, or 10 letters /
12 edges / 3 loops), so we *choose* the plugboard.  For a chosen set of crib positions (the edges)
let S be the plugboard and sigma_k the Enigma without plugboard at crib position k.  The cipher
letter is ``c_k = S(sigma_k(S(p_k)))``.  A depth-first search assigns S step by step and keeps only
assignments whose cipher letters stay inside the wanted letter set.  Everything is then checked
with the real software: the real Enigma enciphers the message, the real ``Menu.from_crib`` and
``Menu.select`` build the menu, and the real bombe/checking machine verify it.
"""

from __future__ import annotations

import itertools
import random
import time
from dataclasses import dataclass, field

from christopher import Bombe, Enigma, Menu, WheelOrder

from . import texts

A = ord("A")
ROTORS = ("I", "II", "III", "IV", "V")
MODEL = "enigma-i"
REFLECTOR = "B"
# Notch letters of the Enigma I rotors (the rotor steps its left neighbour when it leaves this window letter).
NOTCH = {"I": "Q", "II": "E", "III": "V", "IV": "J", "V": "Z"}


def L(i: int) -> str:
    return chr(A + i)


def N(c: str) -> int:
    return ord(c) - A


@dataclass
class Spec:
    """What a scenario must look like."""

    name: str
    mode: str                 # "slice" (right/slow drum fixed, 676 positions) or "full" (17,576)
    edges: int                # number of scrambler units = menu edges
    letters: int              # letters (nodes) of the menu
    max_span: int = 12        # all edges lie in this many consecutive crib positions
    false_min: int = 0        # false stops wanted in the chosen mode (inclusive range)
    false_max: int = 1
    allow_orders: tuple = ()  # restrict wheel orders (tuples of 3 rotors), default all 60

    @property
    def loops(self) -> int:
        return self.edges - self.letters + 1


class _Budget(Exception):
    pass


def _components_connected(edge_list: list[tuple[int, int]]) -> bool:
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edge_list:
        parent[find(a)] = find(b)
    return len({find(x) for x in list(parent)}) == 1


def solve_plugboard(plains: list[int], sigmas: list[list[int]], n_letters: int, rng: random.Random,
                    budget: int = 4000):
    """Find an involution S with ``S(sigma_i(S(p_i)))`` giving a simple connected graph on n letters.

    Depth first over S(p_i), then S(y_i) with y_i = sigma_i(S(p_i)).  Returns S or None.
    """
    E = len(plains)
    S = [-1] * 26
    pairs: set[int] = set()
    nodes = [0]
    perm = list(range(26))
    rng.shuffle(perm)
    rot = rng.randrange(26)
    order = perm[rot:] + perm[:rot]
    found: list[list[int]] = []
    popcount = int.bit_count

    def connected() -> bool:
        parent: dict[int, int] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for e in pairs:
            parent[find(e >> 5)] = find(e & 31)
        return len({find(x) for x in list(parent)}) == 1

    def rec(i: int, mask: int) -> bool:
        nodes[0] += 1
        if nodes[0] > budget:
            raise _Budget
        if i == E:
            if popcount(mask) == n_letters and connected():
                found.append(list(S))
                return True
            return False
        p = plains[i]
        new_p = S[p] < 0
        opts_a = [p] + [x for x in order if S[x] < 0 and x != p] if new_p else [S[p]]
        for a in opts_a:
            if new_p:
                S[p] = a
                S[a] = p
            y = sigmas[i][a]
            if S[y] >= 0:
                opts_c = [S[y]]
            else:
                inm = [x for x in order if mask >> x & 1 and S[x] < 0 and x != y]
                oth = [] if popcount(mask | 1 << p) >= n_letters else [
                    x for x in order if not mask >> x & 1 and S[x] < 0 and x != y and x != p
                ]
                opts_c = [y] + inm + oth
            for c in opts_c:
                if c == p:
                    continue
                key = (min(p, c) << 5) | max(p, c)
                if key in pairs:
                    continue
                nm = mask | 1 << p | 1 << c
                if popcount(nm) > n_letters:
                    continue
                set_y = S[y] < 0
                if set_y:
                    S[y] = c
                    S[c] = y
                pairs.add(key)
                if rec(i + 1, nm):
                    return True
                pairs.discard(key)
                if set_y:
                    S[y] = -1
                    S[c] = -1
            if new_p:
                S[p] = -1
                S[a] = -1
        return False

    try:
        rec(0, 0)
    except _Budget:
        return None
    return found[0] if found else None


def window_states(key: dict, plain: str, upto: int) -> list[str]:
    """Window letters after each of the first ``upto`` letters (the state used to encipher that letter)."""
    e = Enigma(MODEL, **key)
    out = []
    for ch in plain[:upto]:
        e.encrypt(ch)
        out.append(e.positions)
    return out


def sigma_tables(key_nopb: dict, prev_state: str, count: int, states: list[str]) -> list[list[int]]:
    """Enigma permutations (no plugboard) for ``count`` consecutive letters starting after ``prev_state``."""
    e = Enigma(MODEL, **key_nopb)
    tabs = []
    prev = prev_state
    for k in range(count):
        row = []
        for x in range(26):
            e.positions = prev
            row.append(N(e.encrypt(L(x))))
        tabs.append(row)
        prev = states[k]
    return tabs


@dataclass
class Candidate:
    spec: Spec
    msg_id: str
    plaintext: str
    crib: str
    offset: int
    key: dict
    ciphertext: str
    link_positions: list[int]          # 0-based crib positions used as edges
    menu: Menu
    full_menu: Menu
    states: list[str]                  # window letters per crib position (state used for that letter)
    tries: int = 0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


def random_key(rng: random.Random, allow_orders=()) -> dict:
    if allow_orders:
        rotors = rng.choice(list(allow_orders))
    else:
        rotors = tuple(rng.sample(ROTORS, 3))
    rings = "".join(L(rng.randrange(26)) for _ in range(3))
    start = "".join(L(rng.randrange(26)) for _ in range(3))
    return dict(rotors=rotors, reflector=REFLECTOR, rings=rings, positions=start)


def plugboard_string(S: list[int], rng: random.Random, total_pairs: int = 10) -> str:
    pairs = [(x, S[x]) for x in range(26) if S[x] > x]
    free = [x for x in range(26) if S[x] < 0]
    rng.shuffle(free)
    while len(pairs) < total_pairs and len(free) >= 2:
        pairs.append((free.pop(), free.pop()))
    rng.shuffle(pairs)
    return " ".join(L(a) + L(b) for a, b in sorted(pairs))


def submenu(full: Menu, ciphertext: str, positions, n_edges: int) -> Menu:
    """Sub-menu of ``full`` on the given crib positions (``Menu.subset``: keeps the crib, picks the
    test letter the same way ``Menu.select`` does). ``ciphertext`` and ``n_edges`` are kept for the
    callers' signature; the sub-menu has exactly ``len(set(positions))`` edges."""
    del ciphertext, n_edges
    return full.subset(sorted(set(positions)))


def pick_positions(crib: str, spec: Spec, rng: random.Random) -> list[int] | None:
    """Crib positions (0-based) for the edges: the plain letters of the edges can be at most
    ``letters`` distinct ones, so favour the most frequent letters of the crib."""
    counts: dict[str, int] = {}
    for ch in crib:
        counts[ch] = counts.get(ch, 0) + 1
    keep = max(1, spec.letters - rng.choice((0, 0, 1, 2)))
    letters = sorted(counts, key=lambda ch: (-counts[ch], rng.random()))[:keep]
    cover = [k for k, ch in enumerate(crib) if ch in letters]
    if len(cover) < spec.edges:
        return None
    return sorted(rng.sample(cover, spec.edges))


def build_candidate(spec: Spec, rng: random.Random, msg=None, subsets: int = 16) -> Candidate | None:
    """One random key and message, several crib windows and edge subsets; None if none worked out."""
    if msg is None:
        msg = rng.choice(texts.MESSAGES)
    msg_id, raw, cribs = msg
    plain = texts.clean(raw)
    phrase = rng.choice(cribs)
    off0 = plain.index(phrase)
    key = random_key(rng, spec.allow_orders)
    key_nopb = {**key, "plugboard": ""}
    states = window_states(key_nopb, plain, off0 + len(phrase))
    sig_all = None
    span = min(spec.max_span, len(phrase))
    for _ in range(subsets):
        first = rng.randrange(0, len(phrase) - span + 1)
        offset = off0 + first
        crib = phrase[first:first + span]
        crib_states = states[offset:offset + span]
        # No middle-rotor turnover over the crib: left and middle window letters stay put from
        # the first crib letter to the last one.
        if any(s[:2] != crib_states[0][:2] for s in crib_states):
            continue
        positions = pick_positions(crib, spec, rng)
        if positions is None:
            continue
        pl = [N(crib[k]) for k in positions]
        if len(set(pl)) > spec.letters:
            continue
        if sig_all is None:
            prev0 = states[off0 - 1] if off0 > 0 else key["positions"]
            sig_all = sigma_tables(key_nopb, prev0, len(phrase), states[off0:off0 + len(phrase)])
        sig = [sig_all[first + k] for k in positions]
        S = solve_plugboard(pl, sig, spec.letters, rng)
        if S is None:
            continue
        keyp = {**key, "plugboard": plugboard_string(S, rng)}
        ct = Enigma(MODEL, **keyp).encrypt(plain)
        full = Menu.from_crib(ct, crib, offset)
        sub = submenu(full, ct, positions, spec.edges)
        if len(sub) != spec.edges or len(sub.letters) != spec.letters or sub.loops != spec.loops:
            continue
        return Candidate(spec, msg_id, plain, crib, offset, keyp, ct, positions, sub, full, crib_states)
    return None


def stops_for(menu: Menu, order: WheelOrder, mode: str, true_right: str | None):
    """Run the software bombe over one wheel order; in slice mode only the 676 positions with the
    right (slow) drum fixed at ``true_right``."""
    bombe = Bombe(MODEL).plug_up(menu)
    stops = bombe.run(order, fixed_right=true_right if mode == "slice" else None)
    return bombe, stops


def true_stop_letters(c: Candidate) -> str:
    """Drum letters of the true stop = core positions of the rotors at the first crib letter."""
    rings = c.key["rings"]
    w = c.states[0]
    return "".join(L((N(w[i]) - N(rings[i])) % 26) for i in range(3))


def evaluate(c: Candidate) -> dict | None:
    """Run the bombe and the checking machine; None if the candidate violates the spec."""
    spec = c.spec
    order = WheelOrder(REFLECTOR, tuple(c.key["rotors"]))
    true = true_stop_letters(c)
    bombe, stops = stops_for(c.menu, order, spec.mode, true[2])
    positions = 676 if spec.mode == "slice" else 17576
    true_stops = [s for s in stops if s.position == true]
    if len(true_stops) != 1:
        return None
    false = [s for s in stops if s.position != true]
    if not spec.false_min <= len(false) <= spec.false_max:
        return None
    # Checking machine on every stop of the chosen mode.
    checked = []
    plain_ok = False
    for s in stops:
        sols = s.check()
        is_true = s.position == true
        exact = [sol for sol in sols if sol.plaintext == c.plaintext]
        crib_ok = [
            sol for sol in sols
            if sol.plaintext[c.offset:c.offset + len(c.crib)] == c.crib
        ]
        checked.append((s, is_true, sols, exact, crib_ok))
        if is_true and exact:
            plain_ok = True
    if not plain_ok:
        return None
    # False stops must not survive the checking machine.
    for s, is_true, sols, exact, crib_ok in checked:
        if not is_true and sols:
            return None
    # Other wheel orders (the visitor does not know the order in the full hunt).
    all_orders = Bombe(MODEL).plug_up(c.menu).run("all")
    full_order = Bombe(MODEL).plug_up(c.menu).run(order)
    return dict(
        order=order, true=true, stops=stops, checked=checked, positions=positions,
        false_in_mode=len(false), false_full_order=len(full_order) - 1,
        false_all_orders=len(all_orders) - 1, bombe=bombe,
    )


def attempt(spec: Spec, seed: int, index: int, msg_ids=None):
    """Attempt number ``index`` of a deterministic search; returns (candidate, evaluation) or None."""
    rng = random.Random(seed * 1_000_003 + index)
    pool = [m for m in texts.MESSAGES if not msg_ids or m[0] in msg_ids]
    c = build_candidate(spec, rng, msg=rng.choice(pool))
    if c is None:
        return None
    ev = evaluate(c)
    if ev is None:
        return None
    return c, ev


def _worker(args):
    spec, seed, lo, hi, msg_ids = args
    for i in range(lo, hi):
        if attempt(spec, seed, i, msg_ids) is not None:
            return i
    return None


def generate(spec: Spec, seed: int, max_attempts: int = 400000, verbose=False, msg_ids=None,
             workers: int = 1, chunk: int = 20) -> tuple[Candidate, dict]:
    """Search until a scenario matching ``spec`` is found.

    Deterministic: the result is the lowest-numbered successful attempt for this seed, whatever the
    number of worker processes.
    """
    t0 = time.time()
    found = None
    if workers <= 1:
        for i in range(max_attempts):
            r = attempt(spec, seed, i, msg_ids)
            if r is not None:
                found = (i, r)
                break
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(workers) as pool:
            lo = 0
            while lo < max_attempts and found is None:
                jobs = [(spec, seed, lo + j * chunk, lo + (j + 1) * chunk, msg_ids) for j in range(workers)]
                hits = [h for h in pool.map(_worker, jobs) if h is not None]
                if hits:
                    i = min(hits)
                    found = (i, attempt(spec, seed, i, msg_ids))
                lo += workers * chunk
    if found is None:
        raise RuntimeError(f"no scenario found for {spec.name} within {max_attempts} attempts")
    i, (c, ev) = found
    c.tries = i + 1
    c.seconds = time.time() - t0
    if verbose:
        print(f"[{spec.name}] found at attempt {i + 1} in {c.seconds:.1f} s", flush=True)
    return c, ev
