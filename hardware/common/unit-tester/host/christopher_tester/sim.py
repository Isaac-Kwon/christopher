"""Simulated tester: the real firmware logic running against a simulated passive unit.

``SimTransport`` has the same write_line/read_line interface as ``SerialTransport``, so the host
code and the firmware's command interpreter are exercised exactly as on the bench; only the
hardware layer (firmware/hal_sim.py) is replaced.
"""

import sys
from collections import deque
from pathlib import Path

from . import reference as ref
from .reference import N

_FW = Path(__file__).resolve().parents[2] / "firmware"


def _firmware():
    """Import the firmware modules (plain top-level modules, as on the Pico)."""
    if str(_FW) not in sys.path:
        sys.path.insert(0, str(_FW))
    import hal_sim
    import tester_core

    return tester_core, hal_sim


class SimTransport:
    def __init__(self, hal):
        core, _ = _firmware()
        self.hal = hal
        self.tester = core.Tester(hal)
        self._replies = deque()

    def write_line(self, text):
        self._replies.extend(self.tester.handle(text))

    def read_line(self):
        if not self._replies:
            raise TimeoutError("no reply")
        return self._replies.popleft()

    def close(self):
        pass


def network_for(spec, faults=()):
    """SimNetwork of a spec with fault callables ``f(net, spec)`` applied."""
    _, hal_sim = _firmware()
    net = hal_sim.network_from_links(spec.links)
    for f in faults:
        f(net, spec)
    return net


def make_sim(spec=None, faults=(), network=None, sequence=None, **hal_kwargs):
    """(SimTransport, SimHal) for a spec (+faults), a ready SimNetwork, or a STROBE sequence."""
    _, hal_sim = _firmware()
    if network is None and spec is not None:
        network = network_for(spec, faults)
    hal = hal_sim.SimHal(network=network, sequence=sequence, **hal_kwargs)
    return SimTransport(hal), hal


# --- fault injectors ---------------------------------------------------------------------------------


def open_contact(i):
    """Open the contact that joins A_i to its partner."""
    def f(net, spec):
        for a, b in spec.links:
            if a == i or b == i:
                net.disconnect(a, b)
    return f


def short(n1, n2):
    """Short two nodes (use ref.a_node / ref.b_node)."""
    def f(net, spec):
        net.connect(n1, n2, 0.01)
    return f


def swap_pair(i, j):
    """A_i and A_j exchange their partners (two crossed wires). Needs a permutation spec."""
    def f(net, spec):
        pi, pj = spec.perm[i], spec.perm[j]
        net.disconnect(ref.a_node(i), ref.b_node(pi))
        net.disconnect(ref.a_node(j), ref.b_node(pj))
        net.connect(ref.a_node(i), ref.b_node(pj))
        net.connect(ref.a_node(j), ref.b_node(pi))
    return f


def high_resistance(i, ohm):
    """Raise the resistance of the contact(s) at node A_i."""
    def f(net, spec):
        for a, b in spec.links:
            if a == i or b == i:
                net.connect(a, b, ohm)
    return f


def unit_sequence(order, positions, faults_at=None):
    """STROBE sequence (pos15, SimNetwork) of a good unit through ``positions`` [(l,m,r) ...];
    ``faults_at`` {index: [fault callables]} injects faults at some positions."""
    from .protocol import pack_pos

    faults_at = faults_at or {}
    for k, (l, m, r) in enumerate(positions):
        spec = ref.unit_spec(order, l, m, r)
        yield pack_pos(l, m, r), network_for(spec, faults_at.get(k, ()))
