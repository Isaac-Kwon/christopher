"""Project Christopher: Enigma and the Turing-Welchman Bombe.

Follow the real workflow: draw up a :class:`Menu` from a crib, plug it into a :class:`Bombe`,
``run`` wheel orders, read the :class:`Stop` list and ``check`` each stop into a :class:`Solution`.
:func:`search` does all of it at once.
"""

from christopher.bombe import Bombe, SearchReport, Solution, Stop, WheelOrder, search
from christopher.enigma import Enigma
from christopher.menu import Edge, Menu, possible_offsets
from christopher.vectors import export_vectors

__all__ = [
    "Bombe", "Edge", "Enigma", "Menu", "SearchReport", "Solution", "Stop", "WheelOrder",
    "export_vectors", "possible_offsets", "search",
]
