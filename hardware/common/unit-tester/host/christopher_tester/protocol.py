"""Host side of the tester's line protocol (see firmware/tester_core.py for the command list)."""

from collections import namedtuple

N = 26

# Front end constants; must equal firmware/tester_core.py (a test checks it).
V_DRIVE = 24.0
R_SERIES = 1000.0
R_LED = 2200.0
V_LED = 1.2
ADC_RATIO = 10.0 / 92.0
ADC_VREF = 3.3
ADC_FULL = 4095

F_STROBE, F_STOPPED, F_AT_HOME, F_END, F_ESTOP_OK, F_ILOCK_OK = 1, 2, 4, 8, 16, 32
F_JUDGE, F_CLEAR, F_STOP_LATCH = 64, 128, 256          # 165 bits 72, 73, 74 (decision 0003 T4)

Sample = namedtuple("Sample", "seq pos width_us scan_us masks planes")
Sweep = namedtuple("Sweep", "samples ended_early timed_out")


class TesterError(Exception):
    __test__ = False


def node_of(side, i):
    if side not in "AB" or len(side) != 1 or not 0 <= i < N:
        raise ValueError("bad line %r %r" % (side, i))
    return i if side == "A" else N + i


def mask_to_nodes(mask):
    return frozenset(n for n in range(2 * N) if mask >> n & 1)


def coded_sources(planes, side="A"):
    """Decode a coded scan: for every line j of the far side, the near-side index that drove it
    (None when no plane or an impossible code reached it). ``side`` = the driven side."""
    far = N if side == "A" else 0
    out = []
    for j in range(N):
        code = sum(1 << k for k, plane in enumerate(planes) if far + j in plane)
        out.append(code - 1 if 1 <= code <= N else None)
    return out


def unpack_pos(pos):
    """(l, m, r) CORE positions of the bank from the 15-bit POS bus.  POS has no crib offset k (decision 0003 4.5);
    the drum letters of a unit with offset k are ``drum_letters(pos, k)``."""
    return pos & 31, pos >> 5 & 31, pos >> 10 & 31


def drum_letters(pos, k=0):
    """Drum letters (left, middle, right) of a unit with crib offset k: (l, m, (r + k) mod 26)."""
    l, m, r = pos
    return l, m, (r + k) % N


def pack_pos(left, middle, right):
    return left | middle << 5 | right << 10


def contact_resistance(counts):
    """Estimated resistance (ohm) of the conduction path from the optional ADC reading.

    Model (README section 3.6): the driven node feeds its own sense LED (R_LED) and, through the
    unit path R, the far node's sense LED. From the far node voltage y = V_node - V_LED:
    x = (R_LED (V_DRIVE - V_LED) - R_SERIES y) / (R_LED + R_SERIES),  R = R_LED (x / y - 1).
    Coarse (about +-2 ohm plus component tolerances; calibrate with a shorting plug). None when
    nothing conducts.
    """
    if counts <= 0:
        return None
    y = counts / ADC_FULL * ADC_VREF / ADC_RATIO - V_LED
    if y <= 0:
        return None
    x = (R_LED * (V_DRIVE - V_LED) - R_SERIES * y) / (R_LED + R_SERIES)
    return max(0.0, R_LED * (x / y - 1.0))


class SerialTransport:
    """pyserial transport (import deferred so the module works without pyserial)."""

    def __init__(self, port, baud=115200, timeout=3.0):
        import serial  # pyserial

        self._s = serial.Serial(port, baud, timeout=timeout)

    def write_line(self, text):
        self._s.write((text + "\n").encode("ascii"))

    def read_line(self):
        raw = self._s.readline()
        if not raw:
            raise TesterError("timeout waiting for the tester")
        return raw.decode("ascii").strip()

    def close(self):
        self._s.close()


class Tester:
    """Client. ``transport`` offers write_line(str), read_line() -> str."""

    __test__ = False   # not a pytest class

    def __init__(self, transport):
        self.t = transport

    def _final(self, line):
        if line.startswith("ERR"):
            raise TesterError(line[4:] or "error")

    def command(self, text):
        """Send one command; return the payload of its single OK line."""
        self.t.write_line(text)
        while True:
            line = self.t.read_line()
            if line.startswith("OK"):
                return line[2:].strip()
            self._final(line)

    def ident(self):
        return self.command("ID")

    def sense(self):
        """Nodes high with nothing driven (must be empty; else foreign voltage / stuck input)."""
        return mask_to_nodes(int(self.command("SENSE"), 16))

    def drive(self, side, i):
        return mask_to_nodes(int(self.command("DRIVE %s %d" % (side, i)), 16))

    def scan(self, sides="AB"):
        """{driven node: frozenset of nodes sensed high}."""
        words = self.command("SCAN " + sides).split()
        out = {}
        nodes = [node_of(s, i) for s in sides for i in range(N)]
        if len(words) != len(nodes):
            raise TesterError("scan returned %d masks, expected %d" % (len(words), len(nodes)))
        for nd, w in zip(nodes, words):
            out[nd] = mask_to_nodes(int(w, 16))
        return out

    def cscan(self, side="A"):
        """Coded scan: five masks (plane k = lines whose index+1 has bit k set were driven)."""
        words = self.command("CSCAN " + side).split()
        if len(words) != 5:
            raise TesterError("cscan returned %d masks, expected 5" % len(words))
        return [mask_to_nodes(int(w, 16)) for w in words]

    def aux(self):
        pos, flags = (int(x, 16) for x in self.command("AUX").split())
        return pos, flags

    def set_run(self, on):
        self.command("RUN %d" % int(bool(on)))

    def set_home(self, on):
        self.command("HOME %d" % int(bool(on)))

    def settle(self, us):
        self.command("SETTLE %d" % us)

    def off(self):
        self.command("OFF")

    def adc(self, drive_side, i, sense_side, j):
        return int(self.command("ADC %s %d %s %d" % (drive_side, i, sense_side, j)))

    def resistance(self, drive_side, i, sense_side, j):
        return contact_resistance(self.adc(drive_side, i, sense_side, j))

    def sweep(self, n, sides="A", timeout_ms=2000):
        """One scan per STROBE edge, n times. Returns Sweep(samples, ended_early, timed_out)."""
        self.t.write_line("SWEEP %d %s %d" % (n, sides, timeout_ms))
        samples, ended, timed_out = [], False, False
        coded = sides in ("CA", "CB")
        nodes = [] if coded else [node_of(s, i) for s in sides if s in "AB" for i in range(N)]
        while True:
            line = self.t.read_line()
            if line.startswith("S "):
                w = line.split()
                if coded:
                    samples.append(Sample(int(w[1]), int(w[2], 16), int(w[3]), int(w[4]), {},
                                          [mask_to_nodes(int(x, 16)) for x in w[5:]]))
                else:
                    masks = {nd: mask_to_nodes(int(x, 16)) for nd, x in zip(nodes, w[5:])}
                    samples.append(Sample(int(w[1]), int(w[2], 16), int(w[3]), int(w[4]), masks, []))
            elif line.startswith("OK"):
                ended = line.endswith("end")
                break
            elif line.startswith("ERR timeout"):
                timed_out = True
                break
            else:
                self._final(line)
        return Sweep(samples, ended, timed_out)
