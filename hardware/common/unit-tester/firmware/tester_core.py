"""Scrambler unit tester: protocol and test logic (hardware independent).

Runs unchanged on MicroPython (Raspberry Pi Pico) and CPython (PC, with hal_sim.SimHal).
Only the small HAL interface (bottom of this file) touches hardware. Avoid anything MicroPython
lacks (no dataclasses, no typing, no annotations).

Line protocol (ASCII, one command per line; every reply ends with a line starting OK or ERR)
---------------------------------------------------------------------------------------------
Node numbering: side A line i (0..25 = A..Z) is node i, side B line j is node 26+j.
A "mask" is 52 bits = 13 hex digits, bit n = node n sensed high.

    ID                       -> OK christopher-unit-tester <version> <hal name>
    PING                     -> OK
    SENSE                    -> OK <mask>                      nothing driven
    DRIVE <A|B> <i>          -> OK <mask>                      drive one line, read all 52
    SCAN <A|B|AB>            -> OK <mask> * 26 (or 52)         drive each line in turn
    CSCAN <A|B>              -> OK <mask> * 5                  coded scan: plane k drives all lines
                                                               whose (index+1) has bit k set
    AUX                      -> OK <pos4hex> <flags2hex>       position bus + control flags
    RUN <0|1>, HOME <0|1>    -> OK                             control outputs to the unit
    SETTLE <us>              -> OK                             settle time after a drive change
    ADC <A|B> <i> <A|B> <j>  -> OK <counts>                    drive i, measure node j (optional)
    SWEEP <n> <A|B|AB|CA|CB|-> [timeout_ms]
                             -> S <seq> <pos4hex> <width_us> <scan_us> <mask>*  (n times, one
                                per STROBE), then OK <count> [end]  or  ERR timeout <count>
    OFF                      -> OK                             all drivers off

Position bus (POS): 15 bits = the bank's CORE position (l, m, r), bits 0-4 left (fast) drum, 5-9 middle,
10-14 right (0..25).  It does not contain the crib offset k (decision 0003 4.5): a unit's drum letters are
l, m and r + k, which the host computes.
Flags: bit0 STROBE, bit1 STOPPED, bit2 AT_HOME, bit3 END, bit4 ESTOP loop closed (and fed), bit5 ILOCK loop closed,
bit6 JUDGE, bit7 CLEAR, bit8 STOP_LATCH (register: stop relay S is operated).
"""

VERSION = "0.1"
N = 26
NODES = 52
MASK52 = (1 << NODES) - 1

F_STROBE = 1
F_STOPPED = 2
F_AT_HOME = 4
F_END = 8
F_ESTOP_OK = 16
F_ILOCK_OK = 32
F_JUDGE = 64
F_CLEAR = 128
F_STOP_LATCH = 256

# Front end constants, shared with the host's contact-resistance estimate (README section 4).
V_DRIVE = 24.0            # V, test supply
R_SERIES = 1000.0         # ohm, per-channel series resistor in the drive path
R_LED = 2200.0            # ohm, optocoupler LED resistor on every sense channel
V_LED = 1.2               # V, optocoupler LED forward voltage
ADC_RATIO = 10.0 / 92.0   # analog divider 82k / 10k in front of the mux
ADC_VREF = 3.3
ADC_FULL = 4095


def _gen():
    yield


_GEN = type(_gen())


class ProtocolError(Exception):
    pass


def node(side, i):
    if side not in ("A", "B"):
        raise ProtocolError("side must be A or B")
    if not 0 <= i < N:
        raise ProtocolError("line out of range")
    return i if side == "A" else N + i


def fmt_mask(m):
    return "%013x" % (m & MASK52)


class Tester:
    """Command interpreter. ``handle(line)`` returns an iterable of reply lines."""

    def __init__(self, hal):
        self.hal = hal

    # -- helpers -------------------------------------------------------------------------
    def _check_safe(self):
        _, flags = self.hal.aux()
        if not flags & F_ESTOP_OK or not flags & F_ILOCK_OK:
            raise ProtocolError("interlock open")

    def _drive_read(self, nd):
        """Drive one node, settle, read, and always release the driver."""
        hal = self.hal
        try:
            hal.drive(1 << nd)
            hal.settle()
            return hal.sense()
        finally:
            hal.drive(0)

    def _scan(self, sides):
        out = []
        for s in sides:
            for i in range(N):
                out.append(self._drive_read(node(s, i)))
        return out

    def _cscan(self, side):
        """Five drive planes (coded scan): plane k drives every line of ``side`` whose index+1 has
        bit k set. A healthy permutation unit lets each far-side line read its source's code."""
        hal = self.hal
        out = []
        for k in range(5):
            m = 0
            for i in range(N):
                if (i + 1) >> k & 1:
                    m |= 1 << node(side, i)
            try:
                hal.drive(m)
                hal.settle()
                out.append(hal.sense())
            finally:
                hal.drive(0)
        return out

    @staticmethod
    def _sides(arg):
        if arg not in ("A", "B", "AB"):
            raise ProtocolError("side must be A, B or AB")
        return arg

    # -- command dispatch ----------------------------------------------------------------
    def handle(self, line):
        """Reply lines for one command line. Errors become a single ``ERR ...`` line."""
        try:
            replies = self._handle(line)
            if type(replies) is _GEN:
                return self._guard(replies)
            return replies
        except ProtocolError as e:
            self.hal.drive(0)
            return ["ERR %s" % e]
        except (ValueError, IndexError):
            self.hal.drive(0)
            return ["ERR bad arguments"]

    def _guard(self, gen):
        try:
            for r in gen:
                yield r
        except ProtocolError as e:
            self.hal.drive(0)
            yield "ERR %s" % e
        finally:
            self.hal.drive(0)

    def _handle(self, line):
        parts = line.strip().upper().split()
        if not parts:
            return []
        cmd, args = parts[0], parts[1:]
        hal = self.hal
        if cmd == "PING":
            return ["OK"]
        if cmd == "ID":
            return ["OK christopher-unit-tester %s %s" % (VERSION, hal.name)]
        if cmd == "OFF":
            hal.drive(0)
            return ["OK"]
        if cmd == "SENSE":
            return ["OK " + fmt_mask(hal.sense())]
        if cmd == "AUX":
            pos, flags = hal.aux()
            return ["OK %04x %02x" % (pos, flags)]
        if cmd in ("RUN", "HOME"):
            v = int(args[0])
            if v not in (0, 1):
                raise ProtocolError("argument must be 0 or 1")
            if v:
                self._check_safe()
            (hal.set_run if cmd == "RUN" else hal.set_home)(v)
            return ["OK"]
        if cmd == "SETTLE":
            us = int(args[0])
            if not 0 <= us <= 20000:
                raise ProtocolError("settle out of range")
            hal.settle_us = us
            return ["OK"]
        if cmd == "DRIVE":
            self._check_safe()
            return ["OK " + fmt_mask(self._drive_read(node(args[0], int(args[1]))))]
        if cmd == "SCAN":
            self._check_safe()
            masks = self._scan(self._sides(args[0]))
            return ["OK " + " ".join(fmt_mask(m) for m in masks)]
        if cmd == "CSCAN":
            self._check_safe()
            if args[0] not in ("A", "B"):
                raise ProtocolError("side must be A or B")
            return ["OK " + " ".join(fmt_mask(m) for m in self._cscan(args[0]))]
        if cmd == "ADC":
            if not hal.has_adc:
                raise ProtocolError("no adc fitted")
            self._check_safe()
            d = node(args[0], int(args[1]))
            j = node(args[2], int(args[3]))
            try:
                hal.drive(1 << d)
                hal.settle()
                v = hal.adc(j)
            finally:
                hal.drive(0)
            return ["OK %d" % v]
        if cmd == "SWEEP":
            self._check_safe()
            n = int(args[0])
            side = args[1]
            if side not in ("-", "CA", "CB"):
                self._sides(side)
            timeout = int(args[2]) if len(args) > 2 else 2000
            if n < 1 or timeout < 1:
                raise ProtocolError("bad sweep arguments")
            return self._sweep(n, side, timeout)
        raise ProtocolError("unknown command %s" % cmd)

    def _sweep(self, n, side, timeout_ms):
        hal = self.hal
        got = 0
        while got < n:
            if not hal.wait_strobe(timeout_ms):
                yield "ERR timeout %d" % got
                return
            t0 = hal.ticks_us()
            pos, flags = hal.aux()
            if side in ("CA", "CB"):
                masks = self._cscan(side[1])
            else:
                masks = self._scan(side) if side != "-" else []
            scan_us = hal.ticks_since(t0)
            width = hal.strobe_width()
            yield "S %d %04x %d %d %s" % (got, pos, width, scan_us, " ".join(fmt_mask(m) for m in masks))
            got += 1
            if flags & F_END:
                yield "OK %d end" % got
                return
        yield "OK %d" % got


# HAL interface expected by Tester (see hal_pico.py and hal_sim.py):
#   name: str, has_adc: bool, settle_us: int
#   drive(mask52)        bit n set -> node n driven to +24 V through R_SERIES; 0 = all drivers off
#   settle()             wait settle_us
#   sense() -> mask52    read all 52 sense channels
#   aux() -> (pos15, flags)
#   set_run(v), set_home(v)
#   wait_strobe(timeout_ms) -> bool   block until a STROBE rising edge
#   strobe_width() -> int             us of the STROBE pulse just seen (-1 if unknown)
#   ticks_us(), ticks_since(t0)
#   adc(node) -> counts 0..4095
