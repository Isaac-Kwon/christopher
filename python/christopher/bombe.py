"""The Bombe: plug up a menu, run wheel orders, read the stops, check them."""

from __future__ import annotations

from dataclasses import dataclass, field

from christopher import _core
from christopher._text import A, letter, letters, to_str
from christopher.enigma import Enigma, canonical_model
from christopher.menu import Menu


@dataclass(frozen=True)
class WheelOrder:
    """Reflector, optional greek wheel (M4) and three rotors, left to right (the bombe's drums)."""

    reflector: str
    rotors: tuple[str, str, str]
    greek: str | None = None

    @classmethod
    def parse(cls, value) -> "WheelOrder":
        """From ``"B II-IV-V"``, ``"B-THIN BETA II-IV-I"``, ``("B", "II", "IV", "V")`` or a WheelOrder."""
        if isinstance(value, WheelOrder):
            return value
        if isinstance(value, str):
            parts = value.upper().replace("-", " ").split()
            if len(parts) > 1 and parts[1] == "THIN":  # "B-THIN" lost its hyphen above
                parts[0:2] = [parts[0] + "-THIN"]
        else:
            parts = [str(p).strip().upper() for p in value]
        if len(parts) == 4:
            return cls(parts[0], tuple(parts[1:]))
        if len(parts) == 5:
            return cls(parts[0], tuple(parts[2:]), parts[1])
        raise ValueError(f"wheel order needs reflector [greek] and three rotors, got {value!r}")

    @classmethod
    def _from_core(cls, o) -> "WheelOrder":
        return cls(o[0], tuple(o[2]), o[1])

    def _core(self) -> tuple:
        return (self.reflector, self.greek, list(self.rotors))

    def __str__(self) -> str:
        mid = f" {self.greek}" if self.greek else ""
        return f"{self.reflector}{mid} {'-'.join(self.rotors)}"


def _is_all(spec) -> bool:
    return isinstance(spec, str) and spec.strip().lower() == "all"


def _expand_orders(spec, model: str) -> list[WheelOrder]:
    """Expand ``"all"``, one order or a list of orders into a list of :class:`WheelOrder`."""
    if _is_all(spec):
        return [WheelOrder._from_core(o) for o in _core.all_orders(model)]
    one = isinstance(spec, (str, WheelOrder)) or (
        isinstance(spec, tuple) and all(isinstance(p, str) and " " not in p for p in spec)
    )
    return [WheelOrder.parse(spec)] if one else [WheelOrder.parse(s) for s in spec]


def _drums(order: WheelOrder, value) -> tuple[int, int, int, int]:
    """Drum letters ("BLA", greek first on M4) or a core ``(greek, left, middle, right)`` -> core form."""
    if isinstance(value, str) or (isinstance(value, (list, tuple)) and value and isinstance(value[0], str)):
        idx = [ord(c) - A for c in letters(value, 4 if order.greek else 3, "drum position")]
        return tuple(idx) if order.greek else (0, *idx)
    g, l, m, r = value
    return (g, l, m, r)


def _drum_letters(order: WheelOrder, pos) -> str:
    return to_str(pos if order.greek else pos[1:])


@dataclass
class Solution:
    """A key recovered by the checking machine."""

    wheel_order: WheelOrder
    model: str
    rings: str          #: ring settings; only the right ring (and the middle ring if the left rotor
                        #: steps in the message) is determined — the others are reported as A
    positions: str      #: window letters at the start of the message (equivalent to the true key)
    plugboard: str      #: e.g. "AV BS CG"
    plaintext: str
    unresolved: str     #: letters whose stecker the crib did not determine
    score: float        #: German bigram log-likelihood per letter pair (higher is better)
    stop_position: str  #: the bombe's drum letters (core offsets)
    guessed: str = ""   #: subset of `unresolved` paired by the language-model hill-climb
    turnover_at: int | None = None  #: middle-rotor turnover hypothesis of the stop (crib position)

    def enigma(self) -> Enigma:
        """An Enigma set to the recovered key, ready to decrypt from the start of the message."""
        o = self.wheel_order
        return Enigma(
            self.model, rotors=o.rotors, reflector=o.reflector, greek=o.greek,
            rings=self.rings, positions=self.positions, plugboard=self.plugboard,
        )

    def __str__(self) -> str:
        shown = self.plaintext if len(self.plaintext) <= 60 else self.plaintext[:57] + "..."
        return (
            f"{self.wheel_order}  rings {self.rings}  start {self.positions}  "
            f"plugboard [{self.plugboard}]  guessed [{self.guessed}]  {shown}"
        )


def _letters(mask: int) -> str:
    return "".join(chr(A + i) for i in range(26) if mask >> i & 1)


def _solution(order: WheelOrder, model: str, s: tuple) -> Solution:
    rings, positions, plugboard, plain, unresolved, score, pos, guessed, turnover_at = s
    return Solution(
        order, model, rings, positions, plugboard, plain, _letters(unresolved), score,
        _drum_letters(order, pos), _letters(guessed), turnover_at,
    )


@dataclass
class Stop:
    """A bombe stop, as the operator reads it off the machine."""

    wheel_order: WheelOrder
    position: str                 #: drum letters (greek first on M4)
    test_register: int            #: 26-bit mask of live wires on the test cable
    test_letter: str
    turnover_at: int | None = None
    _bombe: "Bombe | None" = field(default=None, repr=False, compare=False)
    _pos: tuple = field(default=(), repr=False, compare=False)

    @property
    def live(self) -> str:
        """Live letters of the test register."""
        return "".join(chr(A + i) for i in range(26) if self.test_register >> i & 1)

    @property
    def stecker_candidates(self) -> list[str]:
        """Possible partners of the test letter: the single live wire, else every dead wire."""
        if self.test_register.bit_count() == 1:
            return [self.live]
        return [chr(A + i) for i in range(26) if not self.test_register >> i & 1]

    def check(self) -> list[Solution]:
        """Run the checking machine: stecker hypotheses, whole crib, ring settings, plaintext."""
        return self._bombe.check(self)

    def trace(self, input_wire=None, matrix: bool = False):
        """Full 676-wire state at this stop (see :meth:`Bombe.trace`)."""
        return self._bombe.trace(self.wheel_order, self.position, input_wire, matrix, self.turnover_at)

    def __str__(self) -> str:
        n = self.test_register.bit_count()
        cand = ",".join(self.stecker_candidates) if n == 1 or n >= 24 else f"{26 - n} dead wires"
        return (
            f"STOP  {self.wheel_order}  drums {self.position}  "
            f"test register {self.test_letter}: live {self.live or '-'} ({n}/26)  "
            f"S({self.test_letter}) in [{cand}]"
        )

    __repr__ = __str__


class Bombe:
    """A bombe: ``plug_up`` a menu, then ``run`` wheel orders and read the stops.

    ``diagonal_board=True`` is Welchman's improvement (the real machine); ``False`` is Turing's
    original wiring, which gives far more false stops on menus with few loops.
    """

    def __init__(self, model: str = "enigma-i", diagonal_board: bool = True):
        self.model = canonical_model(model)
        self.diagonal_board = diagonal_board
        self.menu: Menu | None = None
        self.positions_tested = 0

    def plug_up(self, menu: Menu) -> "Bombe":
        """Wire the menu into the machine (the cable plugging the operators did by hand)."""
        if not len(menu):
            raise ValueError("menu has no edges")
        self.menu = menu
        return self

    def _need_menu(self) -> Menu:
        if self.menu is None:
            raise ValueError("no menu plugged up: call bombe.plug_up(menu) first")
        return self.menu

    def run(self, wheel_orders="all", turnover_at: int | None = None, parallel: bool = True) -> list[Stop]:
        """Run every drum position of each wheel order (GIL released) and return the stops.

        ``wheel_orders`` is ``"all"``, ``"B II-IV-V"``, ``("B","II","IV","V")`` or a list of those.
        ``turnover_at`` assumes the middle rotor steps at that crib position (default: not within
        the crib). The positions tried are counted in ``self.positions_tested``.
        """
        menu = self._need_menu()
        orders = _expand_orders(wheel_orders, self.model)
        results = _core.run_bombe(menu._raw, [o._core() for o in orders], self.diagonal_board, turnover_at, parallel)
        self.positions_tested = sum(n for n, _ in results)
        return [
            self._stop(o, pos, reg, turnover_at) for o, (_, stops) in zip(orders, results) for pos, reg in stops
        ]

    def _stop(self, order: WheelOrder, pos: tuple, reg: int, turnover_at) -> Stop:
        return Stop(order, _drum_letters(order, pos), reg, self._need_menu().test_letter, turnover_at, self, pos)

    def check(self, stop: Stop) -> list[Solution]:
        """Checking machine for one stop; returns zero or more :class:`Solution`."""
        menu = self._need_menu()
        if menu.ciphertext is None or not menu.crib:
            raise ValueError("checking needs the ciphertext and crib: build the menu with Menu.from_crib")
        o = stop.wheel_order
        sols = _core.check_stop(
            menu.ciphertext, menu._raw, o._core(), (stop._pos, stop.test_register),
            self.diagonal_board, stop.turnover_at,
        )
        return [_solution(o, self.model, s) for s in sols]

    def trace(self, wheel_order, position, input_wire=None, matrix: bool = False, turnover_at: int | None = None):
        """Full state of all 676 wires at one drum position (for pictures and RTL vectors).

        Returns 26 integers, one per cable (letter), where bit ``w`` is set when wire ``w`` is live;
        with ``matrix=True`` a 26x26 list of bools ``[cable][wire]``. ``input_wire`` defaults to the
        menu's. ``position`` is drum letters ("BLA", greek first on the M4).
        """
        menu = self._need_menu()
        order = WheelOrder.parse(wheel_order)
        wire = None if input_wire is None else letter(input_wire)
        live = _core.trace(menu._raw, order._core(), _drums(order, position), wire, self.diagonal_board, turnover_at)
        return [[bool(m >> w & 1) for w in range(26)] for m in live] if matrix else live


@dataclass
class SearchReport:
    orders_tried: int
    positions_tested: int
    stops: list[Stop]
    solutions: list[Solution]   #: best (highest German score) first

    def __str__(self) -> str:
        return (
            f"{self.orders_tried} wheel orders, {self.positions_tested} positions, "
            f"{len(self.stops)} stops, {len(self.solutions)} solutions"
        )


def search(
    ciphertext: str,
    crib: str,
    offset: int,
    model: str = "enigma-i",
    max_edges: int | None = 12,
    wheel_orders="all",
    diagonal_board: bool = True,
    turnover_hypotheses: bool = False,
    parallel: bool = True,
) -> SearchReport:
    """The whole pipeline: menu, bombe over the wheel orders (all CPU cores), checking machine.

    ``max_edges=None`` keeps every crib letter as an edge.
    """
    menu = Menu.from_crib(ciphertext, crib, offset)
    if max_edges:
        menu = menu.select(max_edges)
    bombe = Bombe(model, diagonal_board).plug_up(menu)
    orders = None if _is_all(wheel_orders) else [o._core() for o in _expand_orders(wheel_orders, bombe.model)]
    n, positions, stops, sols = _core.run_search(
        ciphertext, menu._raw, bombe.model, orders, diagonal_board, turnover_hypotheses, parallel
    )
    return SearchReport(
        n,
        positions,
        [bombe._stop(WheelOrder._from_core(o), p, reg, t) for o, p, reg, t in stops],
        [_solution(WheelOrder._from_core(o), bombe.model, s) for o, s in sols],
    )
