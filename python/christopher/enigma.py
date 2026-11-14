"""The Enigma machine (Enigma I, M3, M4)."""

from __future__ import annotations

from christopher import _core
from christopher._text import letters, pairs


class Enigma:
    """An Enigma set up the way the day's key sheet says.

    ``rotors`` are left to right, ``rings``/``positions`` follow the same order (greek wheel first on
    the M4).  Both accept ``"BUL"``, ``"B U L"``, ``"02 21 12"`` or ``[2, 21, 12]`` (numbers are
    1-based, as on the machine).  ``plugboard`` is ``"AV BS CG"`` or a list of pairs.

    >>> e = Enigma("enigma-i", rotors=("I", "II", "III"), reflector="B")
    >>> e.encrypt("AAAAA")
    'BDZGO'
    >>> e.positions            # the rotors have stepped
    'AAF'
    """

    def __init__(
        self,
        model: str = "enigma-i",
        *,
        rotors,
        reflector: str = "B",
        rings="A",
        positions="A",
        plugboard="",
        greek: str | None = None,
    ):
        model = canonical_model(model)
        self.rotors = tuple(str(r).strip().upper() for r in rotors)
        if len(self.rotors) != 3:
            raise ValueError(f"three rotors needed (left, middle, right), got {self.rotors}")
        self.reflector = reflector.strip().upper()
        self.greek = greek.strip().upper() if greek else None
        n = 4 if model == "m4" else 3
        self.rings = _expand(rings, n, "rings")
        self.start = _expand(positions, n, "positions")
        self.plugboard = pairs(plugboard)
        self._m = _core.Enigma(
            model, self.reflector, self.greek, list(self.rotors), self.rings, self.start, self.plugboard
        )
        self.model = model

    @property
    def positions(self) -> str:
        """Letters in the windows now (greek wheel first on the M4)."""
        return self._m.window()

    @positions.setter
    def positions(self, value) -> None:
        self._m.set_positions(letters(value, len(self.start), "positions"))

    def encrypt(self, text: str) -> str:
        """Encipher (or decipher: the machine is reciprocal). Non-letters are dropped; rotors step."""
        return self._m.process(text)

    decrypt = encrypt

    def reset(self) -> "Enigma":
        """Return the rotors to the starting positions."""
        self._m.set_positions(self.start)
        return self

    def copy(self) -> "Enigma":
        e = Enigma(
            self.model, rotors=self.rotors, reflector=self.reflector, rings=self.rings,
            positions=self.start, plugboard=self.plugboard, greek=self.greek,
        )
        e.positions = self.positions
        return e

    def __repr__(self) -> str:
        wheels = f"{self.reflector} " + (f"{self.greek} " if self.greek else "") + "-".join(self.rotors)
        return (
            f"Enigma({self.model}, {wheels}, rings={self.rings}, start={self.start}, "
            f"now={self.positions}, plugboard={self.plugboard!r})"
        )


_MODELS = {
    "enigma-i": "enigma-i", "i": "enigma-i", "army": "enigma-i", "1": "enigma-i",
    "enigma1": "enigma-i", "enigma-1": "enigma-i",
    "m3": "m3", "navy3": "m3",
    "m4": "m4", "navy4": "m4", "shark": "m4",
}


def canonical_model(model: str) -> str:
    """"Enigma I", "army", "M3", "shark" ... -> "enigma-i", "m3" or "m4"."""
    key = model.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in _MODELS:
        raise ValueError(f"unknown model {model!r} (use enigma-i, m3, m4)")
    return _MODELS[key]


def _expand(value, n: int, what: str) -> str:
    """Letters for ``n`` rotors; a single letter/number is repeated ("A" -> "AAA")."""
    out = letters(value, None, what)
    return out * n if len(out) == 1 else letters(out, n, what)
