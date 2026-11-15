"""Compare a measured scan with a reference Spec and explain the differences."""

from . import reference as ref
from .reference import LETTERS, N


class Finding:
    def __init__(self, kind, text, nodes=()):
        self.kind = kind          # open | short | misroute | swap | dead | stuck | note
        self.text = text
        self.nodes = tuple(nodes)

    def __repr__(self):
        return "Finding(%s: %s)" % (self.kind, self.text)


class Report:
    def __init__(self, spec, findings, identified=(), checked=0):
        self.spec = spec
        self.findings = list(findings)
        self.identified = list(identified)
        self.checked = checked    # number of driven lines compared

    @property
    def ok(self):
        return not self.findings

    def kinds(self):
        return sorted({f.kind for f in self.findings})

    def render(self, limit=12):
        lines = ["%s: %s (%d lines driven)" % (self.spec.name, "PASS" if self.ok else "FAIL", self.checked)]
        shown = 0
        for f in self.findings:
            if shown >= limit and f.kind in ("misroute", "open"):
                continue
            lines.append("  %-8s %s" % (f.kind.upper(), f.text))
            shown += 1
        hidden = len(self.findings) - shown
        if hidden > 0:
            lines.append("  ... %d more findings (open/misroute) not shown" % hidden)
        for text in self.identified:
            lines.append("  IDENTIFIED %s" % text)
        return "\n".join(lines)


def _net_text(spec, comp):
    return "{" + ", ".join(spec.label(n) for n in sorted(comp)) + "}"


def observed_permutation(obs, spec_perm_len=N):
    """(perm, valid): perm[i] = the single B line seen when driving A_i (None if not exactly one clean
    partner)."""
    perm = [None] * spec_perm_len
    for i in range(spec_perm_len):
        o = obs.get(i)
        if o is None:
            continue
        a = [n for n in o if n < N]
        b = [n - N for n in o if n >= N]
        if a == [i] and len(b) == 1:
            perm[i] = b[0]
    return perm


def diagnose(spec, obs, idle=frozenset(), identify=True):
    """``obs``: {driven node: frozenset(sensed nodes)} from Tester.scan / a sweep sample.
    ``idle``: nodes found high with nothing driven."""
    comps = spec.components()
    findings = []

    for n in sorted(idle):
        findings.append(Finding("stuck", "%s reads high with nothing driven (foreign voltage, "
                                "leakage path or stuck sense input)" % spec.label(n), [n]))

    open_pairs = set()
    short_nets = set()
    misroutes = {}          # driven node -> (missing node, extra node)
    for d in sorted(obs):
        o = set(obs[d])
        if d not in o:
            findings.append(Finding("dead", "%s does not read high while driven (dead driver or sense "
                                    "channel, or open at the tester connector)" % spec.label(d), [d]))
            o.add(d)
        expected = comps[d]
        missing = expected - o
        extra = o - expected
        # extras whose whole expected net arrived: a short between two nets
        loose = set()
        for e in extra:
            ce = comps[e]
            if ce <= o:
                if ce != expected:
                    short_nets.add(frozenset((expected, ce)))
            else:
                loose.add(e)
        if len(missing) == 1 and len(loose) == 1:
            misroutes[d] = (next(iter(missing)), next(iter(loose)))
            continue
        for m in missing:
            if not loose:
                open_pairs.add(frozenset((d, m)))
            else:
                misroutes.setdefault(d, (m, next(iter(loose))))
        if loose and not missing:
            for e in loose:
                short_nets.add(frozenset((expected, frozenset((e,)))))

    # swaps: i -> x instead of m, j -> m' ... with x on j's net and x' on i's net
    swapped = set()
    for d, (m, x) in sorted(misroutes.items()):
        partner_of_x = [n for n in comps[x] if n != x]
        for p in partner_of_x:
            if p in misroutes and misroutes[p][1] in comps[m] and misroutes[p][0] == x:
                key = frozenset((frozenset((d, x)), frozenset((p, misroutes[p][1]))))
                if key not in swapped:
                    swapped.add(key)
                    a, b = sorted((d, p))
                    findings.append(Finding(
                        "swap", "swapped pair: %s and %s are cross-connected (%s -> %s and %s -> %s, "
                        "expected %s -> %s and %s -> %s)" % (
                            spec.label(a), spec.label(b), spec.label(a), spec.label(misroutes[a][1]),
                            spec.label(b), spec.label(misroutes[b][1]), spec.label(a), spec.label(misroutes[a][0]),
                            spec.label(b), spec.label(misroutes[b][0])), (a, b)))
    in_swap = {n for k in swapped for conn in k for n in conn}
    plain_misroutes = {d: v for d, v in misroutes.items() if d not in in_swap}
    reported = set()
    for d, (m, x) in sorted(plain_misroutes.items()):
        if frozenset((d, x)) in reported:
            continue
        reported.add(frozenset((d, x)))
        findings.append(Finding("misroute", "%s conducts to %s, expected %s" % (
            spec.label(d), spec.label(x), spec.label(m)), (d, m, x)))
    for pair in sorted(open_pairs, key=sorted):
        a, b = sorted(pair)
        if a in in_swap or b in in_swap:
            continue
        findings.append(Finding("open", "no conduction between %s and %s (open contact or wire)" % (
            spec.label(a), spec.label(b)), (a, b)))
    for pair in sorted(short_nets, key=lambda p: sorted(sorted(c) for c in p)):
        n1, n2 = sorted(pair, key=sorted)
        findings.append(Finding("short", "net %s is connected to net %s (short between lines)" % (
            _net_text(spec, n1), _net_text(spec, n2)), sorted(n1 | n2)))

    identified = []
    if identify and spec.perm is not None and (plain_misroutes or swapped or len(misroutes) > 2):
        identified = identify_unit(spec, obs)
    return Report(spec, findings, identified, checked=len(obs))


# --- "what is this really" -----------------------------------------------------------------------------


def _matches(perm, observed):
    seen = 0
    for p, o in zip(perm, observed):
        if o is None:
            continue
        seen += 1
        if p != o:
            return False
    return seen > 0


def identify_unit(spec, obs):
    """Candidate explanations when the unit conducts like a clean permutation of a different kind."""
    observed = observed_permutation(obs)
    valid = sum(o is not None for o in observed)
    if valid < 13:
        return []
    out = []
    if spec.kind == "drum":
        for rotor in sorted(k[1] for k in ref.wiring_table() if k[0] == "rotor"):
            for off in range(N):
                w = ref.rotor_wiring(rotor)
                perm = [(w[(i + off) % N] - off) % N for i in range(N)]
                if _matches(perm, observed):
                    out.append("conducts like rotor %s at drum letter %s (expected %s at %s)" % (
                        rotor, LETTERS[off], spec.context["rotor"], LETTERS[spec.context["offset"]]))
    elif spec.kind == "unit" and "order" in spec.context:
        order = spec.context["order"]
        pos = spec.context["pos"]
        rotors = sorted(k[1] for k in ref.wiring_table() if k[0] == "rotor")
        refls = sorted(k[1] for k in ref.wiring_table() if k[0] == "reflector" and "THIN" not in k[1])
        found = False
        for rf in refls:
            for a in rotors:
                for b in rotors:
                    for c in rotors:
                        if len({a, b, c}) < 3:
                            continue
                        if _matches(ref.fast_scrambler(rf, (a, b, c), *pos), observed):
                            found = True
                            out.append("conducts like wheel order %s %s-%s-%s at the expected position "
                                       "(expected %s)" % (rf, a, b, c, order))
        if not found:
            for p in locate_positions(order, observed):
                out.append("conducts like the expected wheel order at drum letters %s (expected %s)" % (
                    "".join(LETTERS[v] for v in p), "".join(LETTERS[v] for v in pos)))
    elif spec.kind == "shifter":
        for c in range(N):
            for rev in (False, True):
                if _matches([ref.shift_binary(i, c, rev) for i in range(N)], observed):
                    out.append("conducts like a shifter with control %d%s" % (c, " (reverse)" if rev else ""))
    return out


def locate_positions(order, observed):
    """Drum positions (l, m, r) at which the expected wheel order would show the observed permutation."""
    rf = order.reflector
    l_w, m_w, r_w = (ref.rotor_wiring(r) for r in order.rotors)
    li, mi, ri = (ref.inverse(w) for w in (l_w, m_w, r_w))
    rfl = ref.reflector_wiring(rf)
    first = next(i for i, o in enumerate(observed) if o is not None)

    def one(c, l, m, r):
        x = (r_w[(c + r) % N] - r) % N
        x = (m_w[(x + m) % N] - m) % N
        x = (l_w[(x + l) % N] - l) % N
        x = rfl[x]
        x = (li[(x + l) % N] - l) % N
        x = (mi[(x + m) % N] - m) % N
        return (ri[(x + r) % N] - r) % N

    out = []
    for l in range(N):
        for m in range(N):
            for r in range(N):
                if one(first, l, m, r) == observed[first]:
                    if all(o is None or one(i, l, m, r) == o for i, o in enumerate(observed)):
                        out.append((l, m, r))
    return out
