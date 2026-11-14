"""Cribs and menus: what the operator draws up before plugging the bombe."""

from __future__ import annotations

from typing import NamedTuple

from christopher import _core
from christopher._text import A, letter, letters, to_str


class Edge(NamedTuple):
    """Plain letter ``plain`` is enciphered to ``cipher`` at crib position ``pos`` (0-based)."""

    plain: str
    cipher: str
    pos: int


def possible_offsets(ciphertext: str, crib: str) -> list[int]:
    """Offsets where ``crib`` can sit: no crib letter may equal the cipher letter beneath it."""
    return _core.possible_offsets(ciphertext, letters(crib, None, "crib"))


class Menu:
    """A bombe menu: edges (one scrambler each), a test letter and the input wire.

    Build one from a crib (:meth:`from_crib`), usually trim it to what fits on the machine
    (:meth:`select`), then hand it to :meth:`Bombe.plug_up`.
    """

    def __init__(self, raw: tuple, ciphertext: str | None = None):
        self._raw = raw
        offset, crib, edges, test, wire = raw
        self.offset: int = offset
        self.crib: str = crib
        self.edges: tuple[Edge, ...] = tuple(Edge(chr(A + p), chr(A + c), k) for p, c, k in edges)
        self.test_letter: str = chr(A + test)
        self.input_wire: str = chr(A + wire)
        self.ciphertext = ciphertext
        self.loops, self.span, comps = _core.menu_info(raw)
        self.components: list[str] = comps

    @classmethod
    def from_crib(cls, ciphertext: str, crib: str, offset: int) -> "Menu":
        """Every crib letter becomes an edge. ``offset`` is where the crib starts in the ciphertext
        (non-letters of ``ciphertext`` are ignored, so it counts letters only)."""
        raw = _core.menu_from_crib(ciphertext, letters(crib, None, "crib"), offset)
        return cls(raw, ciphertext)

    @classmethod
    def from_edges(cls, edges, test_letter: str, input_wire: str = "A") -> "Menu":
        """Hand-made menu from ``(plain, cipher, pos)`` triples. It cannot be checked (no crib)."""
        raw = (0, "", [(letter(p), letter(c), int(k)) for p, c, k in edges], letter(test_letter), letter(input_wire))
        return cls(raw)

    def select(self, max_edges: int = 12) -> "Menu":
        """Keep a loop-rich connected sub-menu of at most ``max_edges`` edges (the bombe has a
        limited number of scramblers; 12 per bank on the British machine)."""
        return Menu(_core.menu_select(self._raw, max_edges), self.ciphertext)

    @property
    def letters(self) -> str:
        return to_str(sorted({ord(x) - A for e in self.edges for x in e[:2]}))

    def __len__(self) -> int:
        return len(self.edges)

    def __str__(self) -> str:
        head = (
            f"menu at offset {self.offset}: {len(self)} edges, {len(self.letters)} letters, "
            f"{self.loops} loops, span {self.span}, test letter {self.test_letter} "
            f"(input wire {self.input_wire})"
        )
        rows = [f"  {e.pos + 1:>3}: {e.plain} - {e.cipher}" for e in self.edges]
        return "\n".join([head, *rows])

    __repr__ = __str__
