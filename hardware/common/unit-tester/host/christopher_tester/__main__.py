"""Command line: python -m christopher_tester [--port COMx | --sim] <command> ...

  scan   --unit "B II-IV-V" --pos ABC     one position of a scrambler unit
  scan   --drum III --copy A [--offset A] a single drum board
  scan   --shifter 7 | --reflector B      relay shifter / reflector board
  sweep  --unit "B II-IV-V" -n 30 [--k 7] STROBE-synchronised sweep of the unit's own drive; POS = bank core position
                                          (no crib offset k), the unit's drum letters are (l, m, r + k)
  raw    "DRIVE A 3"                      send one protocol line
  --sim runs the real firmware logic against a simulated good unit (optionally --fault ...).
"""

import argparse
import sys

from . import reference as ref
from . import sim
from .diagnose import diagnose
from .protocol import SerialTransport, Tester
from .sweep import check_sweep


def _spec(a):
    letters = lambda s: [ref.LETTERS.index(c) for c in s.upper()]      # noqa: E731
    if a.unit:
        return ref.unit_spec(a.unit, *letters(a.pos))
    if a.relay_unit:
        return ref.relay_unit_spec(a.relay_unit, *letters(a.pos), k=a.k)
    if a.drum:
        return ref.drum_spec(a.drum, a.copy, letters(a.offset)[0])
    if a.shifter is not None:
        return ref.shifter_spec(a.shifter, a.reverse)
    if a.reflector:
        return ref.reflector_spec(a.reflector)
    raise SystemExit("choose --unit, --relay-unit, --drum, --shifter or --reflector")


def _fault(text):
    kind, _, arg = text.partition(":")
    if kind == "open":
        return sim.open_contact(int(arg))
    if kind == "short":
        a, b = arg.split(",")
        node = lambda s: ref.a_node(ref.LETTERS.index(s[1].upper())) if s[0] == "A" else \
            ref.b_node(ref.LETTERS.index(s[1].upper()))                    # noqa: E731
        return sim.short(node(a), node(b))
    if kind == "swap":
        i, j = arg.split(",")
        return sim.swap_pair(int(i), int(j))
    raise SystemExit("fault must be open:<i> | short:A<L>,B<L> | swap:<i>,<j>")


def main(argv=None):
    p = argparse.ArgumentParser(prog="christopher_tester", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port")
    p.add_argument("--sim", action="store_true")
    p.add_argument("--fault", action="append", default=[])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "sweep"):
        s = sub.add_parser(name)
        s.add_argument("--unit")
        s.add_argument("--relay-unit")
        s.add_argument("--pos", default="AAA")
        s.add_argument("--drum")
        s.add_argument("--copy", default="A")
        s.add_argument("--offset", default="A")
        s.add_argument("--shifter", type=int)
        s.add_argument("--reverse", action="store_true")
        s.add_argument("--reflector")
        s.add_argument("-n", type=int, default=26)
        s.add_argument("--sides", default="AB" if name == "scan" else "A")
        s.add_argument("--k", type=int, default=0, help="crib offset k of the unit (POS has no k, 0003 4.5)")
    r = sub.add_parser("raw")
    r.add_argument("line")
    a = p.parse_args(argv)

    if a.sim:
        if a.cmd == "sweep":
            order = a.unit
            seq = sim.unit_sequence(order, list(ref.position_sequence(count=a.n)), k=a.k)
            transport, _ = sim.make_sim(sequence=seq)
        else:
            transport, _ = sim.make_sim(_spec(a), [_fault(f) for f in a.fault]) if a.cmd == "scan" else \
                sim.make_sim()
    elif a.port:
        transport = SerialTransport(a.port)
    else:
        raise SystemExit("give --port COMx or --sim")
    t = Tester(transport)

    if a.cmd == "raw":
        print(t.command(a.line))
        return 0
    if a.cmd == "scan":
        spec = _spec(a)
        rep = diagnose(spec, t.scan(a.sides), idle=t.sense())
        print(rep.render())
        return 0 if rep.ok else 1
    if not a.unit:
        raise SystemExit("sweep needs --unit")
    rep = check_sweep(a.unit, t.sweep(a.n, a.sides), k=a.k)
    print(rep.render())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
