"""Check a STROBE-synchronised sweep against the reference at every reported position."""

from functools import lru_cache

from .diagnose import Finding, Report, diagnose
from . import reference as ref
from .protocol import coded_sources, unpack_pos


class SweepReport:
    def __init__(self, order, total, failures, positions, duplicates, late, widths, timed_out, ended_early):
        self.order = order
        self.total = total              # samples taken
        self.failures = failures        # [(position tuple, Report)]
        self.positions = positions      # distinct positions seen
        self.duplicates = duplicates    # positions seen more than once
        self.late = late                # samples whose scan outlasted STROBE
        self.widths = widths
        self.timed_out = timed_out
        self.ended_early = ended_early

    @property
    def ok(self):
        return not self.failures and not self.timed_out and not self.late

    def render(self, limit=5):
        letters = lambda p: "".join(ref.LETTERS[v] for v in p)      # noqa: E731
        lines = ["sweep %s: %s, %d samples at %d distinct positions" % (
            self.order, "PASS" if self.ok else "FAIL", self.total, len(self.positions))]
        if self.widths:
            lines.append("  STROBE width us: min %d  mean %.0f  max %d" % (
                min(self.widths), sum(self.widths) / len(self.widths), max(self.widths)))
        if self.timed_out:
            lines.append("  TIMEOUT: no STROBE within the timeout after %d samples" % self.total)
        if self.late:
            lines.append("  LATE: %d scans took longer than the STROBE pulse (readings after STROBE ends "
                         "are not valid; scan one side only or slow the drive)" % len(self.late))
        if self.duplicates:
            lines.append("  note: %d positions seen more than once" % len(self.duplicates))
        for pos, rep in self.failures[:limit]:
            lines.append("  position %s:" % letters(pos))
            lines.extend("    " + t for t in rep.render().splitlines()[1:])
        if len(self.failures) > limit:
            lines.append("  ... %d more failing positions" % (len(self.failures) - limit))
        return "\n".join(lines)


def _check_coded(spec, planes):
    """Coded sweep sample (5 planes, drives side A): every OUT line must read the code of its source."""
    inv = [None] * ref.N
    for i, p in enumerate(spec.perm):
        inv[p] = i
    got = coded_sources(planes, "A")
    bad = [Finding("misroute", "%s reads source %s, expected %s (coded scan: rerun a full scan at this "
                   "position to classify)" % (spec.label(ref.b_node(j)),
                                              "none" if got[j] is None else spec.label(ref.a_node(got[j])),
                                              spec.label(ref.a_node(inv[j]))), (ref.b_node(j),))
           for j in range(ref.N) if got[j] != inv[j]]
    return Report(spec, bad, checked=5)


@lru_cache(maxsize=4096)
def _spec(order_key, l, m, r):
    return ref.unit_spec(order_key, l, m, r)


def check_sweep(order, sweep, spec_for=None):
    """``order``: wheel order of the unit; ``sweep``: protocol.Sweep. Uses the position the unit
    itself reports (POS bus) to look up the reference, so a wrong POS shows as a mismatch.
    ``spec_for(pos)`` -> Spec overrides the scrambler reference (e.g. a single drum board whose
    left field of POS is its drum letter: ``lambda p: drum_spec("III", "A", p[0])``)."""
    from christopher import WheelOrder

    o = order if spec_for else WheelOrder.parse(order)
    key = str(o)
    failures, seen, dup, late, widths = [], set(), set(), [], []
    for s in sweep.samples:
        pos = unpack_pos(s.pos)
        if max(pos) >= ref.N:
            failures.append((pos, Report(ref.Spec("position bus", [], str, str), [
                Finding("note", "position bus value %04x out of range" % s.pos)])))
            continue
        if pos in seen:
            dup.add(pos)
        seen.add(pos)
        if s.width_us >= 0:
            widths.append(s.width_us)
            if s.scan_us > s.width_us:
                late.append(s.seq)
        spec = spec_for(pos) if spec_for else _spec(key, *pos)
        if s.planes:
            rep = _check_coded(spec, s.planes)
        else:
            rep = diagnose(spec, s.masks, identify=False)
        if not rep.ok:
            failures.append((pos, rep))
    return SweepReport(o, len(sweep.samples), failures, seen, dup, late, widths, sweep.timed_out,
                       sweep.ended_early)
