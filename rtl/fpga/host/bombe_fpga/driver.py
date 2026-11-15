"""Configure and run FPGA bombe engines (register map: rtl/fpga/README.md)."""

from __future__ import annotations

from dataclasses import dataclass, field

REG = dict(
    CTRL=0x000, STATUS=0x004, ORDER=0x008, OPTIONS=0x00C, EDGE_EN=0x010, POSITIONS=0x014,
    CYCLES=0x018, STOP_POS=0x01C, STOP_REG=0x020, PARAMS=0x024, EDGE=0x100,
)
ENGINE_STRIDE = 0x1000
GLOBAL_ID, GLOBAL_INFO = 0xF000, 0xF004
ID_MAGIC = 0x424F4D42

ST_BUSY, ST_DONE, ST_STOP, ST_OVERFLOW = 1, 2, 4, 8

ROTOR_IDS = {"I": 0, "II": 1, "III": 2, "IV": 3, "V": 4, "VI": 5, "VII": 6, "VIII": 7, "BETA": 8, "GAMMA": 9}
REFLECTOR_IDS = {"B": 0, "C": 1, "B-THIN": 2, "C-THIN": 3}


class EdgeLimitError(ValueError):
    pass


def encode_order(order) -> int:
    """ORDER register word for a christopher ``WheelOrder`` (or anything with reflector/greek/rotors)."""
    left, middle, right = (ROTOR_IDS[r] for r in order.rotors)
    word = REFLECTOR_IDS[order.reflector] | left << 8 | middle << 12 | right << 16
    if order.greek:
        word |= 1 << 2 | ROTOR_IDS[order.greek] << 4
    return word


@dataclass(frozen=True)
class EngineConfig:
    """Everything written to one engine before START."""

    order_word: int
    options_word: int
    edge_words: tuple[int, ...]

    @property
    def edge_en(self) -> int:
        return (1 << len(self.edge_words)) - 1


def config_from(menu, order, diagonal_board: bool = True, turnover_at: int | None = None,
                max_edges: int = 32) -> EngineConfig:
    """Engine configuration for a christopher ``Menu`` and ``WheelOrder`` (options as ``Bombe.run``)."""
    if len(menu.edges) > max_edges:
        raise EdgeLimitError(
            f"menu has {len(menu.edges)} edges but the FPGA supports {max_edges}: use menu.select({max_edges})")
    test, wire = ord(menu.test_letter) - 65, ord(menu.input_wire) - 65
    opt = test | wire << 8 | int(diagonal_board) << 16
    if turnover_at is not None:
        opt |= 1 << 17 | (turnover_at & 0xFF) << 24
    edges = tuple((ord(e.plain) - 65) | (ord(e.cipher) - 65) << 8 | e.pos << 16 for e in menu.edges)
    return EngineConfig(encode_order(order), opt, edges)


def decode_stop(pos_word: int, reg_word: int):
    """(core position (greek, left, middle, right), 26-bit test register) of a FIFO head."""
    pos = ((pos_word >> 20) & 31, (pos_word >> 15) & 31, (pos_word >> 10) & 31, (pos_word >> 5) & 31)
    return pos, reg_word & 0x3FFFFFF


@dataclass
class Engine:
    """One engine window."""

    bus: object          # Transport
    index: int
    max_edges: int = 16
    stats: dict = field(default_factory=dict)

    @property
    def base(self) -> int:
        return self.index * ENGINE_STRIDE

    def rd(self, reg: int, i: int = 0) -> int:
        return self.bus.read32(self.base + reg + 4 * i)

    def wr(self, reg: int, value: int, i: int = 0) -> None:
        self.bus.write32(self.base + reg + 4 * i, value)

    def status(self) -> int:
        return self.rd(REG["STATUS"])

    def configure(self, cfg: EngineConfig) -> None:
        if len(cfg.edge_words) > self.max_edges:
            raise EdgeLimitError(f"{len(cfg.edge_words)} edges > engine MAX_EDGES {self.max_edges}")
        self.wr(REG["ORDER"], cfg.order_word)
        self.wr(REG["OPTIONS"], cfg.options_word)
        for i, w in enumerate(cfg.edge_words):
            self.wr(REG["EDGE"], w, i)
        self.wr(REG["EDGE_EN"], cfg.edge_en)

    def start(self) -> None:
        self.wr(REG["CTRL"], 1)

    def abort(self) -> None:
        self.wr(REG["CTRL"], 2)

    def pop_stops(self) -> list:
        """Drain the stop FIFO: [(position, register), ...]."""
        out = []
        while True:
            pos_word = self.rd(REG["STOP_POS"])
            if not pos_word >> 31:
                return out
            out.append(decode_stop(pos_word, self.rd(REG["STOP_REG"])))   # STOP_REG read pops

    @staticmethod
    def finished(status: int) -> bool:
        return bool(status & ST_DONE) and not status & ST_BUSY and not status & ST_STOP

    def poll(self):
        """One polling step: returns (stops_drained, finished).  Also checks STOP_OVERFLOW."""
        st = self.status()
        if st & ST_OVERFLOW:
            raise RuntimeError(f"engine {self.index}: stop FIFO overflow (stops lost)")
        stops = []
        if st & ST_STOP:
            stops = self.pop_stops()
            st = self.status()           # re-read: done may have arrived while we drained
        return stops, self.finished(st)

    def counters(self) -> dict:
        return {"positions": self.rd(REG["POSITIONS"]), "cycles": self.rd(REG["CYCLES"])}


class FpgaBombe:
    """The engine array behind a transport."""

    def __init__(self, bus, verify: bool = True):
        self.bus = bus
        ident = bus.read32(GLOBAL_ID)
        if verify and ident != ID_MAGIC:
            raise RuntimeError(f"unexpected ID register {ident:#010x} (expected {ID_MAGIC:#010x}, 'BOMB')")
        info = bus.read32(GLOBAL_INFO)
        self.n_engines = info & 0xFF
        self.max_edges = (info >> 8) & 0xFF
        self.clock_mhz = info >> 16
        self.engines = [Engine(bus, k, self.max_edges) for k in range(self.n_engines)]

    def __repr__(self):
        return f"FpgaBombe({self.n_engines} engines, {self.max_edges} edges, {self.clock_mhz} MHz)"

    def run(self, menu, wheel_orders="all", model: str = "enigma-i", diagonal_board: bool = True,
            turnover_at: int | None = None, **kw):
        """Same call shape and result as ``christopher.Bombe.run`` (list of ``Stop``)."""
        from bombe_fpga.farm import Farm

        return Farm(self).run(menu, wheel_orders, model, diagonal_board, turnover_at, **kw)
