"""Expected connectivity of the things the tester can test, from the project's reference model.

Node numbering matches the firmware protocol: A line i = node i, B line j = node 26 + j.

* Scrambler unit / stack: A = IN, B = OUT, ``A_i - B_sigma(i)``. ``sigma`` comes from the
  `christopher` package (the bombe trace of a one-edge menu, i.e. the Rust core's table
  scrambler), the authoritative reference.
* Single drum board (hardware/gear/drum-pcb/README.md): copy A = ring 4 (A side) to ring 3 (B side),
  copy B = ring 1 to ring 2, ``A_i - B_W[i]`` with W the rotor wiring of data/wirings/enigma.txt.
* Relay shifter (hardware/relay/concept.md): ``A_i - B_(i +- control)``, or one binary stage.
* Reflector board: pairs inside side A.

``fast_scrambler`` is an independent pure-table implementation used for the "which unit is this
really" search; the tests prove it equal to the christopher result.
"""

import os
from pathlib import Path

N = 26
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def a_node(i):
    return i % N


def b_node(j):
    return N + j % N


# --- wiring tables ----------------------------------------------------------------------------

_TABLE = None


def _find_wiring_file():
    env = os.environ.get("CHRISTOPHER_WIRINGS")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "data" / "wirings" / "enigma.txt"
        if cand.is_file():
            return cand
    raise FileNotFoundError("data/wirings/enigma.txt not found; set CHRISTOPHER_WIRINGS")


def wiring_table():
    """{(kind, name): [26 ints]} from data/wirings/enigma.txt."""
    global _TABLE
    if _TABLE is None:
        table = {}
        for line in _find_wiring_file().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, name, wiring = line.split()[:3]
            table[(kind, name)] = [LETTERS.index(c) for c in wiring]
        _TABLE = table
    return _TABLE


def rotor_wiring(name):
    return wiring_table()[("rotor", name.upper())]


def reflector_wiring(name):
    return wiring_table()[("reflector", name.upper())]


def inverse(perm):
    out = [0] * len(perm)
    for i, p in enumerate(perm):
        out[p] = i
    return out


# --- scrambler ----------------------------------------------------------------------------------


class Spec:
    """What a unit should do: expected links between nodes plus human labels."""

    def __init__(self, name, links, label_a, label_b, perm=None, kind="unit", context=None):
        self.name = name
        self.links = tuple(sorted((min(a, b), max(a, b)) for a, b in links))
        self.label_a = label_a    # function i -> text for side A line i
        self.label_b = label_b
        self.perm = perm          # A_i - B_perm[i] when a two-port permutation, else None
        self.kind = kind
        self.context = context or {}

    def label(self, node):
        return self.label_a(node) if node < N else self.label_b(node - N)

    def components(self):
        """{node: frozenset(component)} over the 52 nodes."""
        parent = list(range(2 * N))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self.links:
            parent[find(a)] = find(b)
        groups = {}
        for n in range(2 * N):
            groups.setdefault(find(n), set()).add(n)
        return {n: frozenset(g) for g in groups.values() for n in g}


def _letter_labels(prefix_a, prefix_b):
    return (lambda i: "%s-%s" % (prefix_a, LETTERS[i]), lambda j: "%s-%s" % (prefix_b, LETTERS[j]))


def _perm_links(perm):
    return [(a_node(i), b_node(p)) for i, p in enumerate(perm)]


def reference_scrambler(order, left, middle, right):
    """sigma of a wheel order at core offsets (the drum letters), via the christopher package.

    ``order`` is anything `christopher.WheelOrder.parse` accepts ("B II-IV-V"); offsets are ints
    0..25 or letters.
    """
    from christopher import Bombe, Menu, WheelOrder

    o = WheelOrder.parse(order)
    if o.greek:
        raise ValueError("the physical machines are three-rotor; no greek wheel")
    pos = "".join(c if isinstance(c, str) else LETTERS[c] for c in (left, middle, right))
    b = Bombe("m3", diagonal_board=False)
    b.plug_up(Menu.from_edges([("A", "B", 0)], "A", "A"))
    perm = []
    for w in range(N):
        live = b.trace(o, pos, input_wire=LETTERS[w])[1]
        outs = [x for x in range(N) if live >> x & 1]
        if len(outs) != 1:
            raise RuntimeError("reference trace gave %r for wire %d" % (outs, w))
        perm.append(outs[0])
    return perm


def unit_spec(order, left, middle, right):
    from christopher import WheelOrder

    o = WheelOrder.parse(order)
    perm = reference_scrambler(o, left, middle, right)
    la, lb = _letter_labels("IN", "OUT")
    pos = "".join(LETTERS[v] if isinstance(v, int) else v for v in (left, middle, right))
    return Spec("scrambler %s at %s" % (o, pos), _perm_links(perm), la, lb, perm, "unit",
                {"order": o, "pos": tuple(LETTERS.index(c) for c in pos)})


def fast_scrambler(refl, rotors, left, middle, right):
    """Independent table composition (right -> middle -> left -> reflector and back)."""
    w = [rotor_wiring(r) for r in rotors]            # left, middle, right
    wi = [inverse(x) for x in w]
    cores = (left, middle, right)
    rf = reflector_wiring(refl)
    perm = []
    for c in range(N):
        x = c
        for k in (2, 1, 0):
            o = cores[k]
            x = (w[k][(x + o) % N] - o) % N
        x = rf[x]
        for k in (0, 1, 2):
            o = cores[k]
            x = (wi[k][(x + o) % N] - o) % N
        perm.append(x)
    return perm


# --- single drum board ----------------------------------------------------------------------------


def drum_spec(rotor, copy="A", offset=0):
    """Drum PCB copy A (ring 4 -> ring 3) or B (ring 1 -> ring 2) for the drum at letter ``offset``.

    With the drum showing letter L the contact j touches pad (j + L), so the contact-to-contact
    map is c -> W[c + L] - L (offset 0 = the pad-to-pad table of the board).
    """
    copy = copy.upper()
    if copy not in ("A", "B"):
        raise ValueError("copy must be A or B")
    w = rotor_wiring(rotor)
    perm = [(w[(i + offset) % N] - offset) % N for i in range(N)]
    rin, rout = (4, 3) if copy == "A" else (1, 2)
    la = lambda i: "%d%s" % (rin, LETTERS[i])          # noqa: E731
    lb = lambda j: "%d%s" % (rout, LETTERS[j])         # noqa: E731
    return Spec("drum %s copy %s offset %s" % (rotor.upper(), copy, LETTERS[offset]), _perm_links(perm),
                la, lb, perm, "drum", {"rotor": rotor.upper(), "copy": copy, "offset": offset})


def reflector_spec(name):
    """Reflector board: A_i joined to A_rho(i); side B unused (must stay isolated)."""
    rho = reflector_wiring(name)
    links = [(a_node(i), a_node(r)) for i, r in enumerate(rho) if i < r]
    la, lb = _letter_labels("REF", "unused")
    return Spec("reflector %s" % name.upper(), links, la, lb, None, "reflector", {"reflector": name.upper()})


def isolation_spec(name, label_a="A", label_b="B"):
    """Nothing may conduct (e.g. drum ring 4 against ring 1: copy A against copy B)."""
    return Spec("isolation " + name, [], lambda i: "%s-%s" % (label_a, LETTERS[i]),
                lambda j: "%s-%s" % (label_b, LETTERS[j]), None, "isolation")


# --- relay shifter and relay unit ------------------------------------------------------------------


def shift_binary(c, control, reverse=False):
    """Port of relay.rs shift_binary: five stages of 1, 2, 4, 8, 16 places."""
    x = c
    for i in range(5):
        if control >> i & 1:
            x = (x - (1 << i)) % N if reverse else (x + (1 << i)) % N
    return x


def shifter_spec(control, reverse=False):
    perm = [shift_binary(i, control, reverse) for i in range(N)]
    la, lb = _letter_labels("IN", "OUT")
    return Spec("shifter control %d%s" % (control, " (reverse)" if reverse else ""), _perm_links(perm), la, lb,
                perm, "shifter", {"control": control, "reverse": reverse})


def shifter_stage_spec(stage, active, reverse=False):
    if not 0 <= stage < 5:
        raise ValueError("stage 0..4 (1, 2, 4, 8, 16 places)")
    step = 1 << stage
    if not active:
        perm = list(range(N))
    else:
        perm = [(i - step) % N if reverse else (i + step) % N for i in range(N)]
    la, lb = _letter_labels("IN", "OUT")
    return Spec("shifter stage %d (%d places) %s" % (stage, step, "active" if active else "released"),
                _perm_links(perm), la, lb, perm, "shifter", {"stage": stage, "active": active})


def relay_controls(left, middle, right, k=0):
    """Port of relay.rs Controls::from_cores -> (a1, a2, a3, a4_l)."""
    rk = (right + k) % N
    return rk, (middle - rk) % N, (left - middle) % N, left


def relay_scramble(order, controls, c):
    """Port of relay.rs RelayScrambler::scramble (shifter / wiring-card form of the unit)."""
    from christopher import WheelOrder

    o = WheelOrder.parse(order)
    lw, mw, rw = (rotor_wiring(r) for r in o.rotors)
    rf = reflector_wiring(o.reflector)
    a1, a2, a3, a4 = controls
    x = shift_binary(c, a1)
    x = rw[x]
    x = shift_binary(x, a2)
    x = mw[x]
    x = shift_binary(x, a3)
    x = lw[x]
    x = shift_binary(x, a4, True)
    x = rf[x]
    x = shift_binary(x, a4)
    x = inverse(lw)[x]
    x = shift_binary(x, a3, True)
    x = inverse(mw)[x]
    x = shift_binary(x, a2, True)
    x = inverse(rw)[x]
    return shift_binary(x, a1, True)


def relay_unit_spec(order, left, middle, right, k=0):
    """Relay scrambler unit (same IN/OUT interface as the gear unit) from the shifter model.

    Equals the gear unit at right core (right + k): the crib offset k advances the right drum."""
    from christopher import WheelOrder

    o = WheelOrder.parse(order)
    ctl = relay_controls(left, middle, right, k)
    perm = [relay_scramble(o, ctl, c) for c in range(N)]
    la, lb = _letter_labels("IN", "OUT")
    return Spec("relay unit %s cores %s%s%s k=%d" % (o, LETTERS[left], LETTERS[middle], LETTERS[right], k),
                _perm_links(perm), la, lb, perm, "unit", {"order": o, "pos": (left, middle, right), "k": k})


# --- sequence of bombe positions ---------------------------------------------------------------------


def position_sequence(start=(0, 0, 0), count=None):
    """Positions the machine visits (relay.rs Counters::clock order: left fastest, carries to
    middle, then right). The gear machine's 30-point order differs; the sweep check therefore
    uses the position the unit itself reports (POS bus), not this sequence."""
    l, m, r = start
    n = 0
    while count is None or n < count:
        yield l, m, r
        n += 1
        l = (l + 1) % N
        if l == 0:
            m = (m + 1) % N
            if m == 0:
                r = (r + 1) % N
