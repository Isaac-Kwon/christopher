"""Letter and number parsing shared by the package (A = 0 internally, 1-based numbers for humans)."""

from __future__ import annotations

import re
from typing import Iterable

A = ord("A")


def letter(value: str | int) -> int:
    """A letter ("b") or a 1-based number (2) as an index 0..25."""
    if isinstance(value, int):
        if not 1 <= value <= 26:
            raise ValueError(f"number {value} is outside 1..26 (A=1 ... Z=26)")
        return value - 1
    value = str(value).strip().upper()
    if len(value) != 1 or not "A" <= value <= "Z":
        raise ValueError(f"not a letter: {value!r}")
    return ord(value) - A


def letters(value: str | int | Iterable, count: int | None = None, what: str = "value") -> str:
    """Normalise a setting to a string of capital letters.

    Accepts ``"BUL"``, ``"B U L"``, ``"02 21 12"`` / ``[2, 21, 12]`` (1-based numbers, as on the
    machine's ring and window markings) or ``["B", "U", "L"]``.  ``count`` checks the length.
    """
    if isinstance(value, str):
        tokens = re.split(r"[\s,;-]+", value.strip())
        tokens = [t for t in tokens if t]
        if tokens and all(t.isdigit() for t in tokens):
            parts: list = [int(t) for t in tokens]
        else:
            parts = [c for c in "".join(tokens)]
    else:
        parts = list(value)
    try:
        out = "".join(chr(A + letter(p)) for p in parts)
    except ValueError as exc:
        raise ValueError(f"bad {what} {value!r}: {exc}") from None
    if count is not None and len(out) != count:
        raise ValueError(f"{what} needs {count} letters, got {len(out)}: {value!r}")
    return out


def pairs(value: str | Iterable) -> str:
    """Plugboard: ``"AV BS CG"``, ``"AVBSCG"`` or ``[("A", "V"), ("B", "S")]`` -> ``"AV BS CG"``."""
    if isinstance(value, str):
        return value
    return " ".join("".join(pair) for pair in value)


def to_str(indexes: Iterable[int]) -> str:
    return "".join(chr(A + i) for i in indexes)
